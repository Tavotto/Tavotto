/**
 * ⌘S 保存到项目（ADR 0096）：`runManualSave` 按「这份排版和项目的关系」分三路。
 *
 * 后端用一个假的 `/api/layouts` + `/api/autosave`：真 `saveLayout` 发出去的 URL 与
 * 查询参数（`target=project`、`base_revision`）都经过它，所以「前端真的把基线带过去了」
 * 是在请求上量的，不是在调用参数上。冲突判据照后端 `_revision_conflict` 两条边写。
 */
import { formatMessage, literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { emptyProject, type TextObject } from '@/types/document'
import { readProjectFile, type ProjectFileBinding } from '@/lib/projectFile'
import { setCurrentProjectId } from '@/lib/session'
import { runManualSave } from './actions'
import {
  activeProjectFile,
  applyDerivedUpdate,
  setProjectFile,
  startAutosave,
  useDocumentStore,
} from './documentStore'
import { useUiStore } from './uiStore'

const text = (id: string): TextObject => ({
  id, type: 'text', text: id, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const revisionOf = (body: string) => `r${body.length}-${[...body].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7)}`

/** 项目里的排版文件：名字 → 内容 */
const projectFiles = new Map<string, string>()
const autosave = new Map<string, string>()
const requests: URL[] = []
/** 在写入前卡一下，模拟「写盘途中用户又改了」 */
let beforeLayoutWrite: (() => void) | null = null

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = new URL(String(url), 'http://x')
  const auto = u.pathname.match(/^\/api\/autosave\/([^/]+)$/)
  if (auto) {
    const id = decodeURIComponent(auto[1])
    if (init?.method === 'PUT') {
      autosave.set(id, String(init.body))
      return new Response(JSON.stringify({ ok: true, saved_at: 1, revision: revisionOf(String(init.body)) }))
    }
    if (init?.method === 'DELETE') return new Response('{"ok":true}')
    const v = autosave.get(id)
    return new Response(v ?? '{}', { status: v ? 200 : 404, headers: v ? { 'X-Tavotto-Revision': revisionOf(v) } : {} })
  }
  const lay = u.pathname.match(/^\/api\/layouts\/([^/]+)$/)
  if (lay && init?.method === 'POST') {
    requests.push(u)
    const name = decodeURIComponent(lay[1])
    const base = u.searchParams.get('base_revision')
    const cur = projectFiles.has(name) ? revisionOf(projectFiles.get(name)!) : null
    const conflict = base === 'absent' ? cur !== null : !!base && cur !== null && cur !== base
    if (conflict) {
      return new Response(
        JSON.stringify({ code: 'external_change', error: 'x', revision: cur, summary: null }),
        { status: 409 },
      )
    }
    beforeLayoutWrite?.()
    projectFiles.set(name, String(init.body))
    return new Response(
      JSON.stringify({ ok: true, revision: revisionOf(String(init.body)), name, file: `tavottofile/${name}.json` }),
    )
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

const s = () => useDocumentStore.getState()
const edit = (id: string) =>
  s().commit(literal('加字'), (d) => {
    d.objects.push(text(id))
  })
const statusText = () => formatMessage(useUiStore.getState().status)

const bind = (over: Partial<ProjectFileBinding> = {}) =>
  setProjectFile({
    projectId: 'pjA',
    name: '排版一',
    file: 'tavottofile/排版一.json',
    revision: null,
    dirty: false,
    ...over,
  })

let stopAutosave: () => void

beforeEach(async () => {
  localStorage.clear()
  projectFiles.clear()
  autosave.clear()
  requests.length = 0
  beforeLayoutWrite = null
  setCurrentProjectId('pjA')
  useUiStore.setState({ layoutOpen: false, layoutConflict: null, layoutName: null, status: null })
  await s().switchDocument(emptyProject(), 'd_save')
  stopAutosave = startAutosave()
})

afterEach(() => {
  stopAutosave()
  setCurrentProjectId(null)
})

describe('绑定了项目文件：⌘S 写回它', () => {
  it('带 target=project 与绑定里的修订号，写回同一个文件，圆点灭、基线推进', async () => {
    projectFiles.set('排版一', '{"old":1}')
    bind({ revision: revisionOf('{"old":1}'), dirty: true })
    edit('t1')

    await runManualSave()

    expect(requests).toHaveLength(1)
    expect(requests[0].searchParams.get('target')).toBe('project')
    expect(requests[0].searchParams.get('base_revision')).toBe(revisionOf('{"old":1}'))
    const written = JSON.parse(projectFiles.get('排版一')!)
    expect(written.canvases[0].objects.map((o: TextObject) => o.id)).toEqual(['t1'])
    expect(s().projectFile).toMatchObject({ dirty: false, revision: revisionOf(projectFiles.get('排版一')!) })
    // 本机记录跟着改：刷新之后基线与圆点都还在
    expect(readProjectFile('d_save')).toEqual(s().projectFile)
    expect(statusText()).toContain('tavottofile/排版一.json')
    expect(useUiStore.getState().layoutOpen).toBe(false)
    // 本机自动保存照存
    expect(autosave.has('d_save')).toBe(true)
  })

  it('项目里那份被别处改过：409 → 不写、带着冲突打开命名框，绑定原样', async () => {
    projectFiles.set('排版一', '{"mine":1}')
    bind({ revision: revisionOf('{"mine":1}') })
    projectFiles.set('排版一', '{"theirs":2}') // git pull
    edit('t1')

    await runManualSave()

    expect(projectFiles.get('排版一')).toBe('{"theirs":2}')
    const ui = useUiStore.getState()
    expect(ui.layoutOpen).toBe(true)
    expect(ui.layoutIntent).toBe('saveToProject')
    expect(ui.layoutName).toBe('排版一')
    expect(ui.layoutConflict).toMatchObject({ name: '排版一', revision: revisionOf('{"theirs":2}') })
    expect(ui.statusTone).toBe('error')
    expect(s().projectFile).toMatchObject({ revision: revisionOf('{"mine":1}'), dirty: true })
  })

  it('写盘途中又改了：写成之后圆点仍亮（项目里那份已经落后）', async () => {
    bind()
    edit('t1')
    beforeLayoutWrite = () => edit('t2')
    await runManualSave()
    expect(s().projectFile?.dirty).toBe(true)
  })
})

describe('开着项目、还没有项目文件', () => {
  it('第一次 ⌘S 弹「存进项目」命名框，不写任何项目文件', async () => {
    edit('t1')
    await runManualSave()
    expect(requests).toHaveLength(0)
    const ui = useUiStore.getState()
    expect(ui.layoutOpen).toBe(true)
    expect(ui.layoutIntent).toBe('saveToProject')
    expect(ui.layoutConflict).toBeNull()
    // 本机照存（老数据迁移：不自动写进项目，只问一次名字）
    expect(autosave.has('d_save')).toBe(true)
  })

  it('别的项目里的绑定不算数，也不被抹掉', async () => {
    bind({ projectId: 'pjB' })
    edit('t1')
    expect(activeProjectFile()).toBeNull()
    await runManualSave()
    expect(requests).toHaveLength(0)
    expect(useUiStore.getState().layoutIntent).toBe('saveToProject')
    expect(readProjectFile('d_save')?.projectId).toBe('pjB')
  })
})

describe('没开项目', () => {
  it('只存本机，提示说「存在本机」，不弹框', async () => {
    setCurrentProjectId(null)
    edit('t1')
    await runManualSave()
    expect(requests).toHaveLength(0)
    expect(useUiStore.getState().layoutOpen).toBe(false)
    expect(statusText()).toContain('本机')
  })
})

describe('项目文件的圆点只跟用户编辑', () => {
  it('用户编辑置位并落本机记录；派生同步不置位', async () => {
    bind()
    applyDerivedUpdate({ doc: { ...s().doc, objects: [text('derived')] } })
    expect(s().projectFile?.dirty).toBe(false)
    edit('t1')
    expect(s().projectFile?.dirty).toBe(true)
    expect(readProjectFile('d_save')?.dirty).toBe(true)
  })

  it('改排版名也置位（名字写在项目文件里）', () => {
    bind()
    s().renameProject('新名字')
    expect(s().projectFile?.dirty).toBe(true)
  })

  it('换文档之后读的是那一份自己的绑定', async () => {
    bind({ dirty: true })
    await s().switchDocument(emptyProject(), 'd_other')
    expect(s().projectFile).toBeNull()
    await s().switchDocument(emptyProject(), 'd_save')
    expect(s().projectFile).toMatchObject({ name: '排版一', dirty: true })
  })
})
