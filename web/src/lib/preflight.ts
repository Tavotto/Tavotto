import type { PanelInfo } from './api'
import type { PanelRender } from '@/store/renderStore'
import { PROOF_KIND } from './brand'
import { effectiveDpi, effectivePt } from './units'
import type { CanvasObject, FigureDocument, PanelObject } from '@/types/document'
import { panelFullSize } from '@/types/document'

/**
 * 导出前检查：把「导出去了才发现」的问题在点导出前列出来。
 * 全部纯计算——doc + 素材表 + 渲染状态进，问题清单出；
 * 每条带 objectIds，配合 revealObjects 一键定位。
 */

export interface PreflightIssue {
  id: string
  severity: 'error' | 'warn'
  text: string
  objectIds: string[]
}

const MIN_PT = 6
const MIN_DPI = 300
const EPS = 0.05

export function runPreflight(
  doc: FigureDocument,
  assets: Record<string, PanelInfo>,
  renderByFile: Record<string, PanelRender>,
): PreflightIssue[] {
  const issues: PreflightIssue[] = []
  const visible = doc.objects.filter((o) => !o.hidden)
  const panels = visible.filter((o): o is PanelObject => o.type === 'panel')
  const push = (id: string, severity: PreflightIssue['severity'], text: string, objs: CanvasObject[]) => {
    if (objs.length) issues.push({ id, severity, text, objectIds: objs.map((o) => o.id) })
  }

  // 缺失素材：文件已不在图库（跨机器 / 被删）
  push(
    'missing-asset',
    'error',
    '面板引用的素材文件不在当前图库中，导出会失败或出空白',
    panels.filter((o) => !assets[o.fileId]),
  )

  // 过期渲染：脚本已更新但尚未重建
  push(
    'stale-render',
    'warn',
    '面板的脚本已更新但尚未重建，导出的会是旧图',
    panels.filter((o) => renderByFile[o.fileId]?.stale),
  )

  // 渲染失败：带图内修改但最近一次渲染报错
  push(
    'render-error',
    'error',
    '面板最近一次渲染失败，导出时会再次尝试，建议先修复',
    panels.filter((o) => o.overrides.length > 0 && renderByFile[o.fileId]?.status === 'error'),
  )

  // 越界：部分在页面外，超出部分会被裁掉
  push(
    'out-of-page',
    'warn',
    '对象超出页面范围，超出部分不会出现在成图里',
    visible.filter(
      (o) =>
        o.x < -EPS || o.y < -EPS || o.x + o.w > doc.page.w + EPS || o.y + o.h > doc.page.h + EPS,
    ),
  )

  // 安全区：设置了页边距时，压线的对象单独提示
  if (doc.page.margin && doc.page.margin > 0) {
    const m = doc.page.margin
    push(
      'outside-margin',
      'warn',
      `对象越过了 ${m}mm 安全区页边距`,
      visible.filter(
        (o) =>
          !(o.x < -EPS || o.y < -EPS || o.x + o.w > doc.page.w + EPS || o.y + o.h > doc.page.h + EPS) &&
          (o.x < m - EPS || o.y < m - EPS ||
            o.x + o.w > doc.page.w - m + EPS || o.y + o.h > doc.page.h - m + EPS),
      ),
    )
  }

  // 面板重叠（>1mm² 的实际压盖）
  const overlapped = new Set<PanelObject>()
  for (let i = 0; i < panels.length; i++) {
    for (let j = i + 1; j < panels.length; j++) {
      const a = panels[i]
      const b = panels[j]
      const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
      const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
      if (w > 0 && h > 0 && w * h > 1) {
        overlapped.add(a)
        overlapped.add(b)
      }
    }
  }
  push('overlap', 'warn', '面板互相重叠，确认是有意的压盖再导出', [...overlapped])

  // 低字号：矢量面板等效字号 < 6pt；画布标注 < 6pt
  push(
    'low-font-panel',
    'warn',
    `矢量面板缩得太小，图内正文等效字号低于 ${MIN_PT}pt`,
    panels.filter(
      (o) => o.fileKind === 'pdf' && effectivePt(panelFullSize(o).w, o.nativeW) < MIN_PT,
    ),
  )
  push(
    'low-font-text',
    'warn',
    `标注文字小于 ${MIN_PT}pt，多数期刊不接受`,
    visible.filter((o) => o.type === 'text' && o.sizePt < MIN_PT),
  )

  // 低 DPI：位图面板摆放尺寸下的等效分辨率
  push(
    'low-dpi',
    'warn',
    `位图面板的等效分辨率低于 ${MIN_DPI}dpi`,
    panels.filter(
      (o) => o.fileKind === 'raster' && !!o.pxW && effectiveDpi(o.pxW, panelFullSize(o).w) < MIN_DPI,
    ),
  )

  // 位图化取舍：翻转 / 半透明面板在 PDF 里按位图嵌入
  push(
    'bitmap-embed',
    'warn',
    '翻转或半透明的面板在 PDF 里按导出 DPI 位图嵌入，矢量文字不保留',
    panels.filter((o) => o.flipH || o.flipV || (o.opacity != null && o.opacity < 1)),
  )

  // 隐藏对象：不会出现在导出中（防「怎么少了一块」）
  push(
    'hidden',
    'warn',
    '隐藏的对象不会出现在导出中',
    doc.objects.filter((o) => o.hidden),
  )

  return issues
}

/** proof report 的载荷（随导出落盘，作为投稿留档） */
export function buildProofPayload(
  doc: FigureDocument,
  assets: Record<string, PanelInfo>,
  issues: PreflightIssue[],
  settings: { dpi: number; formats: string[]; stem: string },
) {
  return {
    kind: PROOF_KIND,
    version: 1,
    stem: settings.stem,
    page_mm: { w: doc.page.w, h: doc.page.h, margin: doc.page.margin ?? 0 },
    dpi: settings.dpi,
    formats: settings.formats,
    checks: issues.map((i) => ({ id: i.id, severity: i.severity, text: i.text, count: i.objectIds.length })),
    objects: doc.objects
      .filter((o) => !o.hidden)
      .map((o) =>
        o.type === 'panel'
          ? {
              type: o.type,
              name: o.name ?? o.fileId,
              file: o.fileId,
              mtime: assets[o.fileId]?.mtime ?? null,
              script: o.script ?? null,
              overrides: o.overrides.length,
              rect_mm: [o.x, o.y, o.w, o.h].map((v) => Math.round(v * 100) / 100),
            }
          : {
              type: o.type,
              name: undefined,
              rect_mm: [o.x, o.y, o.w, o.h].map((v) => Math.round(v * 100) / 100),
            },
      ),
  }
}
