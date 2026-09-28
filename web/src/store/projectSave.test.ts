/**
 * ⌘S 保存到项目（ADR 0096）：`runManualSave` 按「这份排版和项目的关系」分三路。
 *
 * 后端用一个假的 `/api/layouts` + `/api/autosave`：真 `saveLayout` 发出去的 URL 与
 * 查询参数（`target=project`、`base_revision`）都经过它，所以「前端真的把基线带过去了」
 * 是在请求上量的，不是在调用参数上。冲突判据照后端 `_revision_conflict` 两条边写。
 */
import { formatMessage, literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyProject, type TextObject } from '@/types/document'
import { readProjectFile, type ProjectFileBinding } from '@/lib/projectFile'
import { setCurrentProjectId } from '@/lib/session'
import { forgetLayoutRevisions, knownLayoutRevision } from '@/lib/layoutRevision'
import { runManualSave } from './actions'
import {
  activeProjectFile,
  applyDerivedUpdate,
  setProjectFile,
  startAutosave,
  useDocumentStore,
} from './documentStore'
import { useUiStore } from './uiStore'
import { onLayoutSaved, type LayoutSavedVia } from '@/lib/layoutSaved'
import { currentTimelineCtx } from '@/lib/timelineContext'

const text = (id: string): TextObject => ({
  id, type: 'text', text: id, sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const revisionOf = (body: string) => `r${body.length}-${[...body].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7)}`

/** 项目里的排版文件：名字 → 内容 */
const projectFiles = new Map<string, string>()
const autosave = new Map<string, string>()
const requests: URL[] = []
/** 每次真写成的内容（按顺序） */
const written: string[] = []
/** 在写入前卡一下，模拟「写盘途中用户又改了」 */
let beforeLayoutWrite: (() => void) | null = null
/** 在判冲突之前挂住请求（模拟慢盘 / 慢网），用例手动放行 */
let layoutGate: Promise<void> | null = null
/** 同上，挂住本机自动保存的写盘（`saveNow()` 那一步） */
let autosaveGate: Promise<void> | null = null

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = new URL(String(url), 'http://x')
  const auto = u.pathname.match(/^\/api\/autosave\/([^/]+)$/)
  if (auto) {
    const id = decodeURIComponent(auto[1])
    if (init?.method === 'PUT') {
      if (autosaveGate) await autosaveGate
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
    if (layoutGate) await layoutGate
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
    written.push(String(init.body))
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
  written.length = 0
  beforeLayoutWrite = null
  layoutGate = null
  autosaveGate = null
  forgetLayoutRevisions()
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

describe('写成了就发「排版写成了」（时间线的「保存」点，ADR 0101 §7；Codex #679）', () => {
  let seen: [LayoutSavedVia, string][] = []
  let off: () => void = () => {}
  beforeEach(() => {
    seen = []
    off = onLayoutSaved((via, { moment }) => seen.push([via, moment.ctx]))
  })
  afterEach(() => off())

  it('⌘S 写回项目文件：写成后发 project_file，带按下 ⌘S 那一刻的上下文', async () => {
    bind()
    edit('t1')
    const at = currentTimelineCtx()
    await runManualSave()
    expect(seen).toEqual([['project_file', at]])
  })

  it('写成之后的记账 / 状态条出错：事件照发（它在写成之后、任何后续步骤之前）', async () => {
    bind()
    edit('t1')
    const real = useUiStore.getState().setStatus
    useUiStore.setState({
      setStatus: (m, tone, o) => {
        if (tone !== 'error') throw new Error('状态条坏了')
        real(m, tone, o)
      },
    })
    try {
      await runManualSave().catch(() => {})
    } finally {
      useUiStore.setState({ setStatus: real })
    }
    expect(written).toHaveLength(1)
    expect(seen.map(([via]) => via)).toEqual(['project_file'])
  })

  it('写的途中接着改：事件带的快照就是写出去的那一份（不是之后的样子）', async () => {
    bind()
    edit('t1')
    const docs: string[][] = []
    const offDocs = onLayoutSaved((_via, { moment }) => docs.push(moment.identity.doc.objects.map((o) => o.id)))
    let open!: () => void
    layoutGate = new Promise<void>((r) => (open = r))
    const saving = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    edit('t2') // 请求已经发出去了：写出去的是 t1 那一份
    layoutGate = null
    open()
    await saving
    offDocs()
    const sent = JSON.parse(written[0]).canvases[0].objects.map((o: TextObject) => o.id)
    expect(sent).toEqual(['t1'])
    expect(docs).toEqual([sent])
  })

  it('没写成（409）不发', async () => {
    projectFiles.set('排版一', '{"theirs":1}')
    bind({ revision: 'stale' })
    edit('t1')
    await runManualSave()
    expect(written).toHaveLength(0)
    expect(seen).toEqual([])
  })

  it('写的途中切了项目：事件带的仍是发起时的上下文（点属于被写的那一份）', async () => {
    bind()
    edit('t1')
    const at = currentTimelineCtx()
    let open!: () => void
    layoutGate = new Promise<void>((r) => (open = r))
    const saving = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    const { useTimelineStore } = await import('./timelineStore')
    useTimelineStore.getState().clear()
    layoutGate = null
    open()
    await saving
    expect(seen).toEqual([['project_file', at]])
    expect(currentTimelineCtx()).not.toBe(at)
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

/** ADR 0096 评审第 1 轮的两条 P2 */
describe('写回的并发与项目切换', () => {
  const gate = () => {
    let open!: () => void
    layoutGate = new Promise<void>((r) => (open = r))
    return () => {
      layoutGate = null
      open()
    }
  }

  it('⌘S 连按三下：串行写，后一次带着前一次写成的修订号，不弹冲突', async () => {
    bind()
    edit('t1')
    const release = gate()
    const a = runManualSave()
    const b = runManualSave()
    const c = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    expect(requests).toHaveLength(1) // 第一次在路上，后两次排成一次
    edit('t2') // 排队期间又改了一处：排着的那次要带上它
    release()
    await Promise.all([a, b, c])
    expect(requests).toHaveLength(2)
    expect(requests[0].searchParams.get('base_revision')).toBe('absent')
    // 第二次的基线 = 第一次写成的那一份的修订号（现读绑定，不是按键那一刻的旧绑定）
    expect(requests[1].searchParams.get('base_revision')).toBe(revisionOf(written[0]))
    expect(useUiStore.getState().layoutOpen).toBe(false)
    expect(useUiStore.getState().layoutConflict).toBeNull()
    const ids = JSON.parse(projectFiles.get('排版一')!).canvases[0].objects.map((o: TextObject) => o.id)
    expect(ids).toEqual(['t1', 't2'])
    expect(s().projectFile).toMatchObject({ dirty: false, revision: revisionOf(projectFiles.get('排版一')!) })
  })

  it('前一次撞上冲突：排着的那次不再带旧基线去撞第二次', async () => {
    projectFiles.set('排版一', '{"theirs":1}')
    bind({ revision: 'stale' })
    edit('t1')
    const release = gate()
    const a = runManualSave()
    const b = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    release()
    await Promise.all([a, b])
    expect(requests).toHaveLength(1)
    expect(useUiStore.getState().layoutConflict).not.toBeNull()
  })

  it('排着的那次轮到时换了排版：不把另一份排版写进按 ⌘S 时那份的文件', async () => {
    bind()
    edit('t1')
    const release = gate()
    const a = runManualSave()
    const b = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    expect(requests).toHaveLength(1)
    // 排队期间换到另一份排版，它也绑着同名文件（同一个项目里另开了一份）
    await s().switchDocument(emptyProject(), 'd_other')
    bind()
    edit('other')
    release()
    await Promise.all([a, b])
    expect(requests).toHaveLength(1)
    const ids = JSON.parse(projectFiles.get('排版一')!).canvases[0].objects.map((o: TextObject) => o.id)
    expect(ids).toEqual(['t1'])
    // 用户按过的那次 ⌘S 没写：要说出来，不能静默
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(statusText()).toContain('没有写入项目里的 tavottofile/排版一.json')
  })

  it('A 有一次在路上、一次排着时切到 B 按 ⌘S：B 自成一项写进 B 的文件，不被并进 A 的排队', async () => {
    bind()
    edit('t1')
    const release = gate()
    const a1 = runManualSave()
    const a2 = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    expect(requests).toHaveLength(1)
    await s().switchDocument(emptyProject(), 'd_other')
    bind({ name: '排版二', file: 'tavottofile/排版二.json' })
    edit('other')
    const b = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    release()
    await Promise.all([a1, a2, b])
    expect(requests).toHaveLength(2)
    expect(decodeURIComponent(requests[1].pathname)).toBe('/api/layouts/排版二')
    const ids = JSON.parse(projectFiles.get('排版二')!).canvases[0].objects.map((o: TextObject) => o.id)
    expect(ids).toEqual(['other'])
    expect(s().projectFile).toMatchObject({ name: '排版二', dirty: false })
    expect(statusText()).toContain('tavottofile/排版二.json')
    expect(useUiStore.getState().statusTone).not.toBe('error')
  })

  it('写的途中另存为改绑到了别的文件：旧请求的回执不把绑定拉回旧文件', async () => {
    bind()
    edit('t1')
    const release = gate()
    const saving = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    // 另存为（对话框那条路）在这期间把同一份排版绑到了排版二
    bind({ name: '排版二', file: 'tavottofile/排版二.json', revision: 'rev-b' })
    release()
    await saving
    expect(projectFiles.has('排版一')).toBe(true) // 旧请求本身照常写成
    expect(s().projectFile).toMatchObject({ file: 'tavottofile/排版二.json', revision: 'rev-b' })
    expect(readProjectFile('d_save')).toMatchObject({ file: 'tavottofile/排版二.json', revision: 'rev-b' })
  })

  it('写的途中切了项目：修订号记在发请求那个项目名下，不记到新项目头上', async () => {
    bind()
    edit('t1')
    const release = gate()
    const saving = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    setCurrentProjectId('pjB')
    release()
    await saving
    expect(knownLayoutRevision('排版一', 'pjB')).toBeUndefined()
    expect(knownLayoutRevision('排版一', 'pjA')).toBe(revisionOf(projectFiles.get('排版一')!))
  })

  it('写的途中切了项目又撞上冲突：不在新项目里打开冲突岔口', async () => {
    projectFiles.set('排版一', '{"theirs":1}')
    bind({ revision: 'stale' })
    edit('t1')
    const release = gate()
    const saving = runManualSave()
    await new Promise((r) => setTimeout(r, 20))
    setCurrentProjectId('pjB')
    release()
    await saving
    expect(useUiStore.getState().layoutOpen).toBe(false)
    expect(useUiStore.getState().statusTone).toBe('error')
  })
})

/**
 * ADR 0096 §四之四：保存上下文在入口取一次，链上**每一个 await 点之后**切走都不落到别处。
 * await 点与 `store/saveContext.ts` 头上的清单一一对应；切走的方式覆盖判据的四维。
 */
describe('⌘S 链上每个 await 点之后切走', () => {
  const hold = (which: 'layout' | 'autosave') => {
    let open!: () => void
    const p = new Promise<void>((r) => (open = r))
    if (which === 'layout') layoutGate = p
    else autosaveGate = p
    return () => {
      if (which === 'layout') layoutGate = null
      else autosaveGate = null
      open()
    }
  }
  const tick = () => new Promise((r) => setTimeout(r, 20))
  const B_FILE = { name: '排版二', file: 'tavottofile/排版二.json' }
  const switches: Record<string, () => Promise<unknown> | void> = {
    换排版: async () => {
      await s().switchDocument(emptyProject(), 'd_other')
      bind(B_FILE)
      edit('other')
    },
    换项目: () => setCurrentProjectId('pjB'),
    同一份重新载入: () => s().switchDocument(s().buildProject(), 'd_save'),
    另存为改绑: () => bind({ ...B_FILE, revision: 'rev-b' }),
  }
  const points = ['saveNow', '排队', 'saveLayout'] as const
  const cases = points.flatMap((point) => Object.keys(switches).map((how) => [point, how] as const))

  it.each(cases)('%s 之后%s', async (point, how) => {
    bind()
    edit('t1')
    let pressed: Promise<unknown>
    if (point === 'saveNow') {
      const release = hold('autosave')
      pressed = runManualSave()
      await tick()
      expect(requests).toHaveLength(0) // 确实挂在 saveNow 上
      await switches[how]()
      release()
    } else if (point === '排队') {
      const release = hold('layout')
      const first = runManualSave()
      await tick()
      edit('t2')
      pressed = Promise.all([first, runManualSave()])
      await tick()
      expect(requests).toHaveLength(1) // 第二次确实排在第一次后面
      await switches[how]()
      release()
    } else {
      const release = hold('layout')
      pressed = runManualSave()
      await tick()
      expect(requests).toHaveLength(1)
      await switches[how]()
      release()
    }
    await pressed
    // 不论哪一步切走：只写过 A 的文件，B 没被写、没被弹「存进项目」
    for (const u of requests) expect(decodeURIComponent(u.pathname)).toBe('/api/layouts/排版一')
    expect(projectFiles.has('排版二')).toBe(false)
    expect(useUiStore.getState().layoutOpen).toBe(false)
    if (point === 'saveLayout') {
      // 已经发出去的那次写成了，是事实：状态如实说写到了 A
      expect(requests).toHaveLength(1)
      expect(statusText()).toContain('tavottofile/排版一.json')
    } else {
      // 没写成的那一次要说出来
      expect(requests).toHaveLength(point === '排队' ? 1 : 0)
      expect(useUiStore.getState().statusTone).toBe('error')
      expect(statusText()).toContain('没有写入项目里的 tavottofile/排版一.json')
    }
  })

  it('排着一次时另存为改绑，再按 ⌘S：新的那次自成一项写进新文件，不被并进旧的那项一起跳过', async () => {
    bind()
    edit('t1')
    const release = hold('layout')
    const first = runManualSave()
    await tick()
    edit('t2')
    const second = runManualSave() // 排着，上下文是排版一
    await tick()
    bind({ ...B_FILE, revision: null })
    const third = runManualSave() // 上下文是排版二
    release()
    await Promise.all([first, second, third])
    expect(requests.map((u) => decodeURIComponent(u.pathname))).toEqual([
      '/api/layouts/排版一',
      '/api/layouts/排版二',
    ])
    expect(s().projectFile).toMatchObject({ file: 'tavottofile/排版二.json', dirty: false })
  })

  it.each([
    ['换排版', '原排版途中没再改', false],
    ['换排版', '切走前又改过原排版', true],
    ['同一份重新载入', '途中没再改', false],
    ['同一份重新载入', '重新载入前又改过', true],
  ] as const)('写入在路上时%s，%s：原排版的圆点按它自己的编辑代次判（载入不算编辑）', async (how, _, editedBefore) => {
    bind()
    edit('t1')
    const release = hold('layout')
    const pressed = runManualSave()
    await tick()
    if (editedBefore) edit('t2')
    await switches[how]()
    release()
    await pressed
    expect(readProjectFile('d_save')).toMatchObject({
      file: 'tavottofile/排版一.json',
      dirty: editedBefore,
      revision: revisionOf(projectFiles.get('排版一')!),
    })
    // 此刻开着的 B 不被碰
    if (how === '换排版') expect(s().projectFile).toMatchObject({ file: 'tavottofile/排版二.json' })
  })

  it.each([
    ['只有派生同步', false],
    ['派生同步之外用户也改了', true],
  ] as const)('写入在路上时%s：圆点只跟用户编辑（派生同步不算）', async (_, userEdit) => {
    bind()
    edit('t1')
    const release = hold('layout')
    const pressed = runManualSave()
    await tick()
    // 外部元数据同步（面板源文件的 fileKind / pxW 之类）：内容变了，但不是用户编辑
    applyDerivedUpdate({ doc: { ...s().doc, objects: [...s().doc.objects, text('derived')] } })
    if (userEdit) edit('t2')
    release()
    await pressed
    expect(s().projectFile).toMatchObject({ file: 'tavottofile/排版一.json', dirty: userEdit })
    expect(readProjectFile('d_save')).toMatchObject({ dirty: userEdit })
  })

  it.each(['换排版', '换项目', '同一份重新载入'])('没绑定的 A 在 saveNow 之后%s：不写、不弹「存进项目」，并说没写进项目', async (how) => {
    edit('t1') // A 没有绑定：本来会问名字
    const release = hold('autosave')
    const pressed = runManualSave()
    await tick()
    await switches[how]()
    release()
    await pressed
    expect(requests).toHaveLength(0)
    expect(useUiStore.getState().layoutOpen).toBe(false)
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(statusText()).toContain('这次没有写进项目')
  })
})

/** ADR 0096 评审第 7 轮：本机存储写不进（被禁用 / 配额满）时，本会话以内存里的绑定为准 */
describe('localStorage 写失败', () => {
  afterEach(() => vi.restoreAllMocks())

  it('setItem 一律抛 QuotaExceededError：连按两次 ⌘S 都写成，第二次带第一次写成的修订号，不报冲突', async () => {
    bind()
    edit('t1')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError')
    })
    await runManualSave()
    edit('t2')
    await runManualSave()
    expect(requests).toHaveLength(2)
    expect(requests[0].searchParams.get('base_revision')).toBe('absent')
    expect(requests[1].searchParams.get('base_revision')).toBe(revisionOf(written[0]))
    expect(useUiStore.getState().layoutConflict).toBeNull()
    expect(useUiStore.getState().statusTone).not.toBe('error')
    expect(s().projectFile).toMatchObject({ dirty: false, revision: revisionOf(written[1]) })
    // 持久副本停在 bind 那一刻（之后的写全失败了）：上面的修订号推进只能来自会话层
    expect(JSON.parse(localStorage.getItem('tavotto.projectFile.d_save')!)).toMatchObject({ revision: null })
  })
})
