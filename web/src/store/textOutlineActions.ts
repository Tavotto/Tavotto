import { msg, type UiMessage } from '@/i18n'
import { outlineText } from '@/lib/api'
import { toExportObjects } from '@/lib/exportPayload'
import { groupPathOf, setGroupPath } from '@/lib/groupTree'
import { newId } from '@/lib/id'
import type { CanvasObject, PathTextObject, TextObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { activePathEdit } from './pathEditStore'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

/**
 * 文字转轮廓（⇧⌘O，ADR 0189）：字形在后端用导出的同一份字体生成（`rendercore/textoutline`），转出来的路径与导出的字
 * 逐点重合。一个文字对象变成一条或几条路径（底框 / 字 / 公式 / 下划线各一条时成一个新组），原位替换、留在原来的组里；
 * 一次 commit、选中结果。转不了的（字体不可用）不动它、说一句。
 */

const hist = (key: string, values?: Record<string, unknown>): UiMessage => msg(`history.${key}`, values, 'workspace')
const tell = (key: string, values: Record<string, unknown>, tone: 'info' | 'done') =>
  useUiStore.getState().setStatus(msg(`status.${key}`, values, 'workspace'), tone)

/** 选区里能转轮廓的文字（看得见、没锁定） */
export function outlinableTexts(): (TextObject | PathTextObject)[] {
  const ids = new Set(useSelectionStore.getState().ids)
  return useDocumentStore
    .getState()
    .doc.objects.filter(
      (o): o is TextObject | PathTextObject =>
        ids.has(o.id) && (o.type === 'text' || o.type === 'pathText') && !o.hidden && !o.locked,
    )
}

export const canOutlineText = (): boolean => !activePathEdit() && outlinableTexts().length > 0

export async function outlineSelectedText(): Promise<boolean> {
  const texts = outlinableTexts()
  if (!texts.length) return false
  finishActiveGesture()
  // 裁剪不随载荷走：转出来的路径留在原来的组里，照样被同一个蒙版裁（ADR 0191）
  // 上下文给整份文档：路径文字的自动离线要看一起成组的那条线（ADR 0190）
  const payload = toExportObjects(texts, useDocumentStore.getState().doc.objects).map(({ clip: _c, ...rest }) => rest)
  let results: Awaited<ReturnType<typeof outlineText>>['results']
  try {
    results = (await outlineText(payload)).results
  } catch {
    tell('outlineFailed', { count: texts.length }, 'info')
    return false
  }
  const replace = new Map<string, CanvasObject[]>()
  let failed = 0
  texts.forEach((t, i) => {
    const r = results[i]
    if (!r || !('paths' in r) || !r.paths.length) {
      failed++
      return
    }
    const prefix = groupPathOf(t)
    const gid = r.paths.length > 1 ? newId('g') : null
    replace.set(
      t.id,
      r.paths.map((raw, k) => {
        const p = { ...(raw as CanvasObject), id: newId('p') }
        setGroupPath(p, gid ? [...prefix, gid] : prefix)
        if (k === 0 || !gid) p.name = t.text.slice(0, 40)
        return p
      }),
    )
  })
  if (!replace.size) {
    tell('outlineFailed', { count: texts.length }, 'info')
    return false
  }
  const made: string[] = []
  useDocumentStore.getState().commit(hist('outlineText', { count: replace.size }), (d) => {
    d.objects = d.objects.flatMap((o) => {
      const r = replace.get(o.id)
      if (!r) return [o]
      made.push(...r.map((p) => p.id))
      return r
    })
  })
  useSelectionStore.getState().set(made)
  if (failed) tell('outlinePartial', { count: failed }, 'info')
  else tell('outlined', { count: replace.size }, 'done')
  return true
}
