/**
 * 保存失败 / 冲突的红色提示带出口按钮（docs/rules/frontend/ui-visual-discipline.md「失败出口三种结局」）。
 *
 * 每条：触发失败 → 断言提示带按钮 → 调用按钮 → 断言原动作真的重新执行 / 结果确定。
 * 后端用一个可切换故障的假 fetch：自动保存 PUT（200 / 409 / 500）与项目排版 POST（200 / 500）。
 */
import { formatMessage, literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { emptyProject, type TextObject } from '@/types/document'
import { setCurrentProjectId } from '@/lib/session'
import { forgetLayoutRevisions } from '@/lib/layoutRevision'
import { runManualSave } from './actions'
import { saveNow, setProjectFile, startAutosave, useDocumentStore } from './documentStore'
import { reportAutosaveFailure } from './saveFailureActions'
import { useUiStore } from './uiStore'

const text = (id: string): TextObject => ({
  id, type: 'text', text: id, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})
const revisionOf = (body: string) => `r${body.length}-${[...body].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7)}`

const autosave = new Map<string, string>()
const projectFiles = new Map<string, string>()
let autosavePut: 200 | 409 | 500 | 'external' = 200
let layoutPost: 200 | 500 = 200
let layoutPosts = 0
/** 每次自动保存 PUT 的尝试（不论成败） */
const putAttempts: string[] = []

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = new URL(String(url), 'http://x')
  const auto = u.pathname.match(/^\/api\/autosave\/([^/]+)$/)
  if (auto) {
    const id = decodeURIComponent(auto[1])
    if (init?.method === 'PUT') {
      putAttempts.push(id)
      if (autosavePut === 'external') return new Response(JSON.stringify({ code: 'external_change', error: 'x', revision: 'r-disk' }), { status: 409 })
      if (autosavePut === 409) return new Response(JSON.stringify({ code: 'stale_write', error: 'x' }), { status: 409 })
      if (autosavePut === 500) return new Response('{"error":"disk full"}', { status: 500 })
      autosave.set(id, String(init.body))
      return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: revisionOf(String(init.body)) }))
    }
    if (init?.method === 'DELETE') return new Response('{"ok":true}')
    const v = autosave.get(id)
    return new Response(v ?? '{}', { status: v ? 200 : 404, headers: v ? { 'X-Tavotto-Revision': revisionOf(v) } : {} })
  }
  const lay = u.pathname.match(/^\/api\/layouts\/([^/]+)$/)
  if (lay && init?.method === 'POST') {
    layoutPosts += 1
    if (layoutPost === 500) return new Response('{"error":"permission denied"}', { status: 500 })
    const name = decodeURIComponent(lay[1])
    projectFiles.set(name, String(init.body))
    return new Response(JSON.stringify({ ok: true, revision: revisionOf(String(init.body)), name, file: `tavottofile/${name}.json` }))
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const s = () => useDocumentStore.getState()
const ui = () => useUiStore.getState()
const edit = (id: string) => s().commit(literal('加字'), (d) => { d.objects.push(text(id)) })
const statusText = () => formatMessage(ui().status)
const actionLabel = () => formatMessage(ui().statusAction?.label ?? null)
const ids = (json: string | undefined) =>
  (JSON.parse(json!).canvases[0].objects as TextObject[]).map((o) => o.id)

/** App.tsx 里同一条接线：自动保存失败事件 → 提示 */
const onAutosaveError = (ev: Event) => reportAutosaveFailure((ev as CustomEvent).detail)

let stopAutosave: () => void
beforeEach(async () => {
  localStorage.clear()
  autosave.clear()
  projectFiles.clear()
  autosavePut = 200
  layoutPost = 200
  layoutPosts = 0
  putAttempts.length = 0
  forgetLayoutRevisions()
  setCurrentProjectId(null)
  useUiStore.setState({ layoutOpen: false, layoutIntent: 'saveToProject', status: null, statusAction: null })
  await s().switchDocument(emptyProject(), 'd_fail')
  stopAutosave = startAutosave()
  window.addEventListener('tavotto:autosave-error', onAutosaveError)
})
afterEach(() => {
  window.removeEventListener('tavotto:autosave-error', onAutosaveError)
  stopAutosave()
  setCurrentProjectId(null)
})

describe('自动保存写盘失败 →「重试保存」', () => {
  it('提示带按钮；点下去真的重新写盘，存成后提示收掉', async () => {
    autosavePut = 500
    edit('t1')
    await saveNow()
    expect(ui().statusTone).toBe('error')
    expect(statusText()).toContain('没能保存到磁盘')
    expect(actionLabel()).toBe('重试保存')
    expect(autosave.has('d_fail')).toBe(false)

    autosavePut = 200
    ui().statusAction!.run()
    await new Promise((r) => setTimeout(r, 50))
    await saveNow()
    expect(ids(autosave.get('d_fail'))).toEqual(['t1'])
    expect(s().saveState).toBe('saved')
    expect(ui().status).toBeNull()
  })
})

describe('「重试保存」绑定失败的那份排版', () => {
  it('自动保存失败后切到别的排版再点：只收掉提示，不对此刻开着的那份写盘', async () => {
    autosavePut = 500
    edit('t1')
    await saveNow()
    expect(actionLabel()).toBe('重试保存')
    const action = ui().statusAction!
    await s().switchDocument(emptyProject(), 'd_other')
    await new Promise((r) => setTimeout(r, 50)) // 离开时那次冲刷的失败事件先落定
    edit('b1') // 此刻开着的这份有内容：不核对象的话 saveNow() 会真去写它
    await saveNow() // 先把 B 自己的防抖写掉（写盘仍是 500，不会真写进去）
    const before = putAttempts.length
    action.run()
    await new Promise((r) => setTimeout(r, 50))
    expect(putAttempts.length, '没有对此刻开着的排版发起写盘').toBe(before)
    expect(ui().status).toBeNull()
  })

  it('⌘S 失败后切到别的排版再点：不对新排版重跑 ⌘S，说「切换了排版」', async () => {
    autosavePut = 500
    edit('t1')
    await runManualSave()
    const action = ui().statusAction!
    await s().switchDocument(emptyProject(), 'd_other')
    await new Promise((r) => setTimeout(r, 50)) // 离开时那次冲刷的失败事件先落定
    autosavePut = 200
    autosave.clear()
    action.run()
    await new Promise((r) => setTimeout(r, 50))
    expect(autosave.size).toBe(0)
    expect(statusText()).toContain('切换了排版')
  })
})

describe('自动保存被另一个窗口的新版挡下（stale）→「存成副本」', () => {
  it('提示一句话 + 主按钮「存成副本」；点下去本窗口的改动存成新排版，原排版一个字节不动', async () => {
    edit('theirs')
    await saveNow()
    const original = autosave.get('d_fail')
    expect(ids(original)).toEqual(['theirs'])

    autosavePut = 409
    edit('mine')
    await saveNow()
    expect(s().saveState).toBe('conflict')
    expect(statusText()).toContain('另一个窗口保存了更新的版本。')
    expect(actionLabel()).toBe('存成副本')

    autosavePut = 200
    ui().statusAction!.run()
    await new Promise((r) => setTimeout(r, 80))
    expect(s().documentId).not.toBe('d_fail')
    expect(s().projectMeta.name).toContain('副本')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['theirs', 'mine'])
    expect(s().saveState).not.toBe('conflict')
    expect(autosave.get('d_fail'), '两边都留：原排版没被碰').toBe(original)
    expect(ids(autosave.get(s().documentId))).toEqual(['theirs', 'mine'])
  })

  it('点下去时冲突已被别处裁决：只收掉提示，不分叉、不动文档', async () => {
    edit('theirs')
    await saveNow()
    autosavePut = 409
    edit('mine')
    await saveNow()
    const action = ui().statusAction!
    useDocumentStore.setState({ saveState: 'dirty', saveIssue: null })
    action.run()
    await new Promise((r) => setTimeout(r, 30))
    expect(s().documentId).toBe('d_fail')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['theirs', 'mine'])
    expect(ui().status).toBeNull()
  })
})

describe('自动保存撞上外部修改（external）→「重新加载」', () => {
  it('提示带按钮；点下去取回磁盘那版，本窗口的编辑进恢复槽位，冲突态清掉', async () => {
    edit('theirs')
    await saveNow()
    expect(ids(autosave.get('d_fail'))).toEqual(['theirs'])

    autosavePut = 'external'
    edit('mine')
    await saveNow()
    expect(s().saveState).toBe('conflict')
    expect(statusText()).toContain('在别处被改过')
    expect(actionLabel()).toBe('重新加载')

    ui().statusAction!.run()
    await new Promise((r) => setTimeout(r, 50))
    expect(s().saveState).toBe('clean')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['theirs'])
    expect(s().docNotice?.kind, '本窗口的编辑没丢：恢复横幅提供出来').toBe('recovery')
    expect(ui().status).toBeNull()
  })
})

describe('⌘S 保存', () => {
  it('写盘失败：提示带「重试保存」，点下去重新走整条 ⌘S', async () => {
    autosavePut = 500
    edit('t1')
    await runManualSave()
    expect(statusText()).toContain('无法保存')
    expect(actionLabel()).toBe('重试保存')

    autosavePut = 200
    ui().statusAction!.run()
    await new Promise((r) => setTimeout(r, 80))
    expect(ids(autosave.get('d_fail'))).toEqual(['t1'])
    expect(statusText()).toContain('已保存在本机')
    expect(ui().statusTone).toBe('done')
  })

  it('撞上冲突：提示带「重新加载」，点下去取回磁盘那版', async () => {
    edit('theirs')
    await runManualSave()
    autosavePut = 409
    edit('mine')
    await runManualSave()
    expect(statusText()).toContain('这次没存上')
    expect(actionLabel()).toBe('重新加载')

    ui().statusAction!.run()
    await new Promise((r) => setTimeout(r, 50))
    expect(s().saveState).toBe('clean')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['theirs'])
    expect(ui().status).toBeNull()
  })
})

describe('写进项目文件失败 →「重试保存」', () => {
  it('提示带按钮；点下去用同一个保存上下文再写一次项目文件', async () => {
    setCurrentProjectId('pjA')
    setProjectFile({ projectId: 'pjA', name: '排版一', file: 'tavottofile/排版一.json', revision: null, dirty: true })
    layoutPost = 500
    edit('t1')
    await runManualSave()
    expect(statusText()).toContain('没能存进项目里的')
    expect(actionLabel()).toBe('重试保存')
    expect(layoutPosts).toBe(1)
    expect(projectFiles.size).toBe(0)

    layoutPost = 200
    ui().statusAction!.run()
    await new Promise((r) => setTimeout(r, 80))
    expect(layoutPosts).toBe(2)
    expect(ids(projectFiles.get('排版一'))).toEqual(['t1'])
    expect(statusText()).toContain('已保存到项目')
  })
})
