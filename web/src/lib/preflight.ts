import type { PanelInfo } from './api'
import { panelRender, type PanelRender } from '@/store/renderStore'
import { formatMessage, msg, type UiMessage } from '@/i18n'
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
  /**
   * 问题描述的**描述符**，不是翻译好的字符串：预检结果会在对话框里一直挂
   * 着，中途切语言得跟着换。`id` 才是机器可读的稳定身份（proof report 里
   * 两者都写）。
   */
  message: UiMessage
  objectIds: string[]
}

/** 显示用文本（组件里请配合 useTranslation 订阅语言变化）。 */
export const issueText = (issue: PreflightIssue): string => formatMessage(issue.message)

const pf = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`preflight.${key}`, values, 'errors')

const MIN_PT = 6
const MIN_DPI = 300
const EPS = 0.05

export function runPreflight(
  doc: FigureDocument,
  assets: Record<string, PanelInfo>,
  /** 渲染态按「文件 + 变体」分键：取某个面板的那一份必须带上面板本身 */
  render: { byKey: Record<string, PanelRender>; latest: Record<string, string> },
): PreflightIssue[] {
  const issues: PreflightIssue[] = []
  const visible = doc.objects.filter((o) => !o.hidden)
  const panels = visible.filter((o): o is PanelObject => o.type === 'panel')
  const push = (
    id: string,
    severity: PreflightIssue['severity'],
    message: UiMessage,
    objs: CanvasObject[],
  ) => {
    if (objs.length) issues.push({ id, severity, message, objectIds: objs.map((o) => o.id) })
  }

  // 缺失素材：文件已不在图库（跨机器 / 被删）
  push(
    'missing-asset',
    'error',
    pf('missingAsset'),
    panels.filter((o) => !assets[o.fileId]),
  )

  // 过期渲染：脚本已更新但尚未重建
  push(
    'stale-render',
    'warn',
    pf('staleRender'),
    panels.filter((o) => panelRender(render, o)?.stale),
  )

  // 渲染失败：带图内修改但最近一次渲染报错
  push(
    'render-error',
    'error',
    pf('renderError'),
    panels.filter(
      (o) => o.overrides.length > 0 && panelRender(render, o)?.status === 'error',
    ),
  )

  // 越界：部分在页面外，超出部分会被裁掉
  push(
    'out-of-page',
    'warn',
    pf('outOfPage'),
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
      pf('outsideMargin', { margin: m }),
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
  push('overlap', 'warn', pf('overlap'), [...overlapped])

  // 低字号：矢量面板等效字号 < 6pt；画布标注 < 6pt
  push(
    'low-font-panel',
    'warn',
    pf('lowFontPanel', { min: MIN_PT }),
    panels.filter(
      (o) => o.fileKind === 'pdf' && effectivePt(panelFullSize(o).w, o.nativeW) < MIN_PT,
    ),
  )
  push(
    'low-font-text',
    'warn',
    pf('lowFontText', { min: MIN_PT }),
    visible.filter((o) => o.type === 'text' && o.sizePt < MIN_PT),
  )

  // 低 DPI：位图面板摆放尺寸下的等效分辨率
  push(
    'low-dpi',
    'warn',
    pf('lowDpi', { min: MIN_DPI }),
    panels.filter(
      (o) => o.fileKind === 'raster' && !!o.pxW && effectiveDpi(o.pxW, panelFullSize(o).w) < MIN_DPI,
    ),
  )

  // 位图化取舍：翻转 / 半透明面板在 PDF 里按位图嵌入
  push(
    'bitmap-embed',
    'warn',
    pf('bitmapEmbed'),
    panels.filter((o) => o.flipH || o.flipV || (o.opacity != null && o.opacity < 1)),
  )

  // 隐藏对象：不会出现在导出中（防「怎么少了一块」）
  push(
    'hidden',
    'warn',
    pf('hidden'),
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
    // proof report 是**投稿留档**：id 是稳定的机器键，text 是生成那一刻的
    // 界面语言下的人类可读文本（留档记录当时看到的是什么）
    checks: issues.map((i) => ({
      id: i.id,
      severity: i.severity,
      text: issueText(i),
      count: i.objectIds.length,
    })),
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
