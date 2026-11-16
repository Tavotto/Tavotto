import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatMessage, literal } from '@/i18n'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type TextObject } from '@/types/document'
import type { SaveIssue } from '@/lib/autosave/diskWriter'
import { runManualSave } from './actions'
import { saveNow, startAutosave, useDocumentStore } from './documentStore'
import { resetRestoreStateForTests } from './documentRestore'
import { useUiStore } from './uiStore'

const id = 'save-failure-working'
const storageKey = `tavotto.autosave.${id}`
const state = () => useDocumentStore.getState()
const disk = new Map<string, string>()
let rejection: { status: number; body: Record<string, unknown> } | 'network' | null = null
let stopAutosave: (() => void) | null = null
const notices: { id: string; reason: string; issue?: SaveIssue }[] = []
const onError = (event: Event) => notices.push((event as CustomEvent).detail)

beforeEach(async () => {
  localStorage.clear()
  disk.clear()
  rejection = null
  notices.length = 0
  setCurrentProjectId(null)
  resetRestoreStateForTests()
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    const match = String(url).match(/\/api\/autosave\/([^/?]+)/)
    if (!match) return new Response('{}', { status: 404 })
    const key = decodeURIComponent(match[1])
    if (init?.method === 'PUT') {
      if (rejection === 'network') throw new TypeError('Failed to fetch')
      if (rejection) return new Response(JSON.stringify(rejection.body), { status: rejection.status })
      disk.set(key, String(init.body))
      return new Response(JSON.stringify({ ok: true, revision: 'saved-revision', saved_at: 1 }))
    }
    return new Response(disk.get(key) ?? '{}', {
      status: disk.has(key) ? 200 : 404,
      headers: disk.has(key) ? { 'X-Tavotto-Revision': 'saved-revision' } : undefined,
    })
  }))
  const project = emptyProject()
  project.canvases[0].objects.push({
    id: 'text', type: 'text', text: 'original', sizePt: 9, bold: false,
    color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
  } as TextObject)
  await state().switchDocument(project, id)
  await saveNow()
  stopAutosave = startAutosave()
  window.addEventListener('tavotto:autosave-error', onError)
  useUiStore.setState({ status: null })
})

afterEach(() => {
  stopAutosave?.()
  stopAutosave = null
  window.removeEventListener('tavotto:autosave-error', onError)
  vi.unstubAllGlobals()
})

const edit = () => state().commit(literal('edit'), (doc) => { doc.guides.push({ axis: 'x', pos: 12 }) })

describe('保存错误从真实 API 边界进入同一状态和通知平面', () => {
  it('422 保原正文、本机新副本与 code/params；通知同源，成功重试清错误', async () => {
    const original = disk.get(id)
    rejection = { status: 422, body: {
      code: 'chart_source_mismatch', params: { source: '图一.svg' }, error: '来源附件校验失败', unrelated: 'not-in-state',
    } }
    edit()
    expect(await saveNow()).toBe('save_error')
    expect(state().saveIssue).toEqual({
      kind: 'backend', docId: id,
      backend: { status: 422, code: 'chart_source_mismatch', params: { source: '图一.svg' }, fallback: '来源附件校验失败' },
    })
    expect(notices).toEqual([{ id, reason: 'io', issue: state().saveIssue }])
    expect(disk.get(id)).toBe(original)
    expect(JSON.parse(localStorage.getItem(storageKey)!).canvases[0].guides).toEqual([{ axis: 'x', pos: 12 }])
    expect(localStorage.getItem(storageKey)).not.toContain('chart_source_mismatch')
    expect(JSON.stringify(state().buildProject())).not.toContain('not-in-state')
    rejection = null
    expect(await saveNow()).toBe('saved')
    expect(state().saveIssue).toBeNull()
    expect(localStorage.getItem(storageKey)).toBeNull()
    expect(disk.get(id)).not.toBe(original)
  })

  it.each([
    [422, { code: 'non_finite_number', params: { reason: 'NaN @ x' }, error: 'fallback' }, 'NaN @ x'],
    [403, { code: 'layout_read_only', error: 'fallback' }, '只读'],
    [500, { code: 'write_failed', params: { reason: 'ENOSPC' }, error: 'fallback' }, 'ENOSPC'],
  ])('无项目 CtrlS 展示 %s 的真实原因，未写成功不冒充保存', async (status, body, expected) => {
    const original = disk.get(id)
    rejection = { status, body }
    edit()
    await runManualSave()
    expect(state().saveState).toBe('save_error')
    expect(formatMessage(useUiStore.getState().status)).toContain(expected)
    expect(formatMessage(useUiStore.getState().status)).not.toContain('检查磁盘空间')
    expect(disk.get(id)).toBe(original)
    expect(localStorage.getItem(storageKey)).not.toBeNull()
  })

  it('无项目 CtrlS 的网络失败保持通用保存提示和本机副本', async () => {
    rejection = 'network'
    edit()
    await runManualSave()
    expect(state().saveIssue).toEqual({ kind: 'io', docId: id })
    expect(formatMessage(useUiStore.getState().status)).toContain('检查磁盘空间')
    expect(formatMessage(useUiStore.getState().status)).not.toContain('Failed to fetch')
    expect(notices).toEqual([{ id, reason: 'io' }])
    expect(localStorage.getItem(storageKey)).not.toBeNull()
  })
})
