/**
 * 「存成副本」= 真分叉：新 documentId、新自动保存槽位、原排版一个字节不动、项目文件写在旁边且不覆盖重名、
 * 历史不继承、presence 端着的 id 跟着换。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { formatMessage, literal } from '@/i18n'
import { forgetLayoutRevisions } from '@/lib/layoutRevision'
import { __setPresenceChannelFactory, type PresenceChannel } from '@/lib/docPresence'
import { readProjectFile } from '@/lib/projectFile'
import { setCurrentProjectId } from '@/lib/session'
import { emptyProject, type TextObject } from '@/types/document'
import { forkCurrentDocument } from './documentFork'
import { saveNow, setProjectFile, startAutosave, useDocumentStore } from './documentStore'
import { useUiStore } from './uiStore'

const text = (id: string): TextObject => ({
  id, type: 'text', text: id, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})
const revisionOf = (body: string) => `r${body.length}-${[...body].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7)}`

const autosave = new Map<string, string>()
const puts: string[] = []
const projectFiles = new Map<string, string>()
const posts: string[] = []
let layoutFail = false

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = new URL(String(url), 'http://x')
  const auto = u.pathname.match(/^\/api\/autosave\/([^/]+)$/)
  if (auto) {
    const id = decodeURIComponent(auto[1])
    if (init?.method === 'PUT') {
      puts.push(id)
      autosave.set(id, String(init.body))
      return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: revisionOf(String(init.body)) }))
    }
    if (init?.method === 'DELETE') return new Response('{"ok":true}')
    const v = autosave.get(id)
    return new Response(v ?? '{}', { status: v ? 200 : 404, headers: v ? { 'X-Tavotto-Revision': revisionOf(v) } : {} })
  }
  const lay = u.pathname.match(/^\/api\/layouts\/([^/]+)$/)
  if (lay && init?.method === 'POST') {
    const name = decodeURIComponent(lay[1])
    posts.push(name)
    if (layoutFail) return new Response('{"error":"permission denied"}', { status: 500 })
    const base = u.searchParams.get('base_revision')
    if (base === 'absent' && projectFiles.has(name)) {
      return new Response(
        JSON.stringify({ code: 'external_change', error: 'x', revision: revisionOf(projectFiles.get(name)!), summary: null }),
        { status: 409 },
      )
    }
    projectFiles.set(name, String(init.body))
    return new Response(JSON.stringify({ ok: true, revision: revisionOf(String(init.body)), name, file: `tavottofile/${name}.json` }))
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const s = () => useDocumentStore.getState()
const edit = (id: string) => s().commit(literal('加字'), (d) => { d.objects.push(text(id)) })
const ids = (json: string | undefined) => (JSON.parse(json!).canvases[0].objects as TextObject[]).map((o) => o.id)

const sent: unknown[] = []
const channel: PresenceChannel = {
  postMessage: (d) => void sent.push(d),
  addEventListener: () => {},
  close: () => {},
}

let stop: () => void
beforeEach(async () => {
  localStorage.clear()
  autosave.clear()
  puts.length = 0
  projectFiles.clear()
  posts.length = 0
  sent.length = 0
  layoutFail = false
  forgetLayoutRevisions()
  setCurrentProjectId(null)
  useUiStore.setState({ status: null, statusAction: null })
  __setPresenceChannelFactory(() => channel)
  await s().switchDocument({ ...emptyProject(), project: { id: 'p1', name: '排版一' } }, 'd_orig')
  stop = startAutosave()
})
afterEach(() => {
  stop()
  __setPresenceChannelFactory(null)
  setCurrentProjectId(null)
})

describe('存成副本（没开项目）', () => {
  it('新 documentId、名字「原名 副本」、内容是此刻窗口里的、历史不继承', async () => {
    edit('a')
    await saveNow()
    edit('b') // 没存的改动随内存里这份进副本
    expect(s().past.length).toBeGreaterThan(0)

    const out = await forkCurrentDocument()

    expect(out.ok).toBe(true)
    expect(s().documentId).not.toBe('d_orig')
    expect(s().documentId).toBe(out.ok && out.id)
    expect(s().projectMeta.name).toBe('排版一 副本')
    expect(s().doc.objects.map((o) => o.id)).toEqual(['a', 'b'])
    expect(s().past).toEqual([])
    expect(formatMessage(useUiStore.getState().status)).toContain('排版一 副本')
  })

  it('原排版一个字节不动：它的自动保存槽位还是分叉前那份，之后也不再被这个窗口写', async () => {
    edit('a')
    await saveNow()
    const before = autosave.get('d_orig')
    edit('b')
    await forkCurrentDocument()
    await saveNow()
    expect(autosave.get('d_orig')).toBe(before)
    expect(ids(autosave.get('d_orig'))).toEqual(['a'])
    expect(puts.slice(puts.indexOf('d_orig') + 1).every((id) => id !== 'd_orig')).toBe(true)
    // 新槽位是副本自己的，含没存的那笔
    expect(ids(autosave.get(s().documentId))).toEqual(['a', 'b'])
  })

  it('presence 端着的 id 换成新的，原 id 不再由本窗口占', async () => {
    await forkCurrentDocument()
    const opens = sent.filter((m) => (m as { type?: string }).type === 'doc-open') as { docId: string }[]
    expect(opens.at(-1)?.docId).toBe(s().documentId)
    expect(opens.at(-1)?.docId).not.toBe('d_orig')
  })
})

describe('存成副本（绑定了项目文件）', () => {
  const bind = () => {
    setCurrentProjectId('pjA')
    setProjectFile({ projectId: 'pjA', name: '排版一', file: 'tavottofile/排版一.json', revision: 'r0', dirty: false })
    projectFiles.set('排版一', '{"orig":1}')
  }

  it('写成原文件旁边的新文件并绑定到副本；原文件与原绑定不动', async () => {
    bind()
    edit('a')
    const out = await forkCurrentDocument()
    expect(out.ok).toBe(true)
    expect(posts).toEqual(['排版一 副本'])
    expect(projectFiles.get('排版一')).toBe('{"orig":1}')
    expect(ids(projectFiles.get('排版一 副本'))).toEqual(['a'])
    expect(readProjectFile(s().documentId)).toMatchObject({ file: 'tavottofile/排版一 副本.json', dirty: false })
    expect(readProjectFile('d_orig')).toMatchObject({ file: 'tavottofile/排版一.json', revision: 'r0' })
    expect(s().projectMeta.name).toBe('排版一 副本')
  })

  it('重名加序号，已有的文件一个都不覆盖', async () => {
    bind()
    projectFiles.set('排版一 副本', '{"keep":1}')
    projectFiles.set('排版一 副本 2', '{"keep":2}')
    const out = await forkCurrentDocument()
    expect(out.ok && out.name).toBe('排版一 副本 3')
    expect(projectFiles.get('排版一 副本')).toBe('{"keep":1}')
    expect(projectFiles.get('排版一 副本 2')).toBe('{"keep":2}')
    expect(projectFiles.has('排版一 副本 3')).toBe(true)
    expect(s().projectMeta.name).toBe('排版一 副本 3')
  })

  it('项目文件没写成：整件不做，当前排版原样（不留半截）', async () => {
    bind()
    layoutFail = true
    const out = await forkCurrentDocument()
    expect(out).toMatchObject({ ok: false, reason: 'write_failed' })
    expect(s().documentId).toBe('d_orig')
    expect(s().projectMeta.name).toBe('排版一')
  })
})
