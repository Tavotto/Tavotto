import { msg } from '@/i18n'
import { copyAsSvg, type ExportObject } from '@/lib/api'
import { toExportObjects } from '@/lib/exportPayload'
import { visibleBounds } from '@/lib/transform'
import { isKnownObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

/**
 * 复制为 SVG（ADR 0189）：选中的标注 / 形状 / 路径 / 文字，用**导出 SVG 的同一个写入器**（后端 `svgwriter`，
 * `/api/svg/copy`）写成一段 SVG，放进剪贴板——Illustrator、Inkscape、Figma 粘贴进去就是矢量。页面 = 选区看得见的范围
 * （剪切组按蒙版）。图（面板）不在这里：它的 SVG 走导出。
 *
 * 剪贴板：`ClipboardItem` 收一个 Promise（Safari / WKWebView 要求在用户手势里同步调用 `write`，内容可以晚到）；
 * 环境认 `image/svg+xml` 就两种都放，否则只放文本（这几个软件都认粘贴进来的 SVG 文本）。
 */

const tell = (key: string, tone: 'info' | 'done') => useUiStore.getState().setStatus(msg(`status.${key}`, undefined, 'workspace'), tone)

function selected() {
  const ids = new Set(useSelectionStore.getState().ids)
  return useDocumentStore.getState().doc.objects.filter((o) => ids.has(o.id))
}

/** 能不能复制为 SVG：选区里有看得见的对象，而且没有图、没有本构建不认识的对象 */
export function canCopyAsSvg(): boolean {
  const sel = selected()
  return sel.some((o) => !o.hidden) && sel.every((o) => o.type !== 'panel' && isKnownObject(o))
}

const shift = (o: ExportObject, dx: number, dy: number): ExportObject => ({
  ...o,
  x_mm: o.x_mm - dx,
  y_mm: o.y_mm - dy,
  ...(o.clip ? { clip: o.clip.map((m) => ({ ...m, x_mm: m.x_mm - dx, y_mm: m.y_mm - dy })) } : {}),
})

/** 选区 → SVG 文本（页面 = 看得见的范围）；做不了 = null */
export async function selectionSvg(): Promise<string | null> {
  const all = useDocumentStore.getState().doc.objects
  const sel = selected()
  const box = visibleBounds(sel, all)
  if (!box || !canCopyAsSvg()) return null
  const payload = toExportObjects(sel, all).map((o) => shift(o, box.x, box.y))
  if (!payload.length) return null
  const r = await copyAsSvg(payload, box.w, box.h)
  return r.ok && r.svg ? r.svg : null
}

export async function copySelectionAsSvg(): Promise<boolean> {
  if (!canCopyAsSvg()) return false
  const svgP = selectionSvg()
  const blobOf = (type: string) => {
    const p = svgP.then((s) => {
      if (s == null) throw new Error('svg')
      return new Blob([s], { type })
    })
    // 剪贴板没来取（write 先失败了）时它的拒绝没人接：挂一个空接收，结局由下面的退路说
    p.catch(() => {})
    return p
  }
  try {
    const items: Record<string, Promise<Blob>> = { 'text/plain': blobOf('text/plain') }
    const CI = globalThis.ClipboardItem as (typeof ClipboardItem & { supports?: (t: string) => boolean }) | undefined
    if (!CI || !navigator.clipboard?.write) throw new Error('no clipboard items')
    if (CI.supports?.('image/svg+xml')) items['image/svg+xml'] = blobOf('image/svg+xml')
    await navigator.clipboard.write([new CI(items)])
  } catch {
    // 退一步：拿到文字再写（不在手势里的环境可能拒绝，那就如实说没复制成）
    try {
      const s = await svgP
      if (s == null || !navigator.clipboard?.writeText) throw new Error('svg')
      await navigator.clipboard.writeText(s)
    } catch {
      tell('svgCopyFailed', 'info')
      return false
    }
  }
  tell('svgCopied', 'done')
  return true
}
