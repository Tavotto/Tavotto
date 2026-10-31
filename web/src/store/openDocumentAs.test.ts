/**
 * `openDocumentAs` 的「丢失确认」框开着时切了项目（Codex #936 P1 r4240742420）：
 * 冲刷当前文档失败 → 弹丢失确认框（这条路上唯一的 await）→ 框开着时项目被切走 → 用户点确认。
 * 确认之后必须再核一次调用方入口取的上下文，不再是同一个就不装——否则 A 的找回行被装进 B。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type ProjectDocument, type TextObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useUiStore } from './uiStore'
import { openDocumentAs } from './actions'

const text = (id: string): TextObject => ({
  id, type: 'text', text: id, sizePt: 9, bold: false, color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})
const docWith = (id: string): ProjectDocument => {
  const pd = emptyProject()
  pd.canvases[0].objects.push(text(id))
  return pd
}

async function waitForConfirm() {
  for (let i = 0; i < 50 && !useUiStore.getState().confirm; i++) await new Promise((r) => setTimeout(r, 0))
  const c = useUiStore.getState().confirm
  if (!c) throw new Error('丢失确认框没有弹出来（前提不成立）')
  return c
}

beforeEach(async () => {
  localStorage.clear()
  globalThis.fetch = (async () => new Response('{}', { status: 404 })) as typeof fetch
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cur')
  useDocumentStore.getState().commit(literal('加一段字'), (d) => {
    d.objects.push(text('t_cur'))
  })
  // 本机副本写不进去 → flushAutosave 回 'error' → switchDocument 走丢失确认
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota')
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUiStore.getState().setConfirm(null)
})

describe('openDocumentAs：确认框开着时上下文变了', () => {
  it('确认之后不再是同一个项目 / 排版：不装，仍停在原文档', async () => {
    let current = true
    const p = openDocumentAs(docWith('t_a'), 'd_from_a', { stillCurrent: () => current })
    const confirm = await waitForConfirm()
    current = false // 框开着时切走了
    confirm.resolve(true)
    expect(await p).toBe(false)
    expect(useDocumentStore.getState().documentId).toBe('d_cur')
  })

  it('对照：上下文没变，确认之后照常换过去', async () => {
    const p = openDocumentAs(docWith('t_a'), 'd_from_a', { stillCurrent: () => true })
    const confirm = await waitForConfirm()
    confirm.resolve(true)
    expect(await p).toBe(true)
    expect(useDocumentStore.getState().documentId).toBe('d_from_a')
  })
})
