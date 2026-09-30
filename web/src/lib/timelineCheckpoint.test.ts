/**
 * 时间线打点的唯一入口（ADR 0101）：自动节点的间隔、关键时刻、命名与「程序起的名字」、
 * 以及「关项目那一刻」拍的是哪个项目。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/timelineThumb', () => ({
  captureThumbSources: vi.fn(() => new Map()),
  composeTimelineThumb: vi.fn(async () => null),
}))

import { emptyProject, type TextObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import {
  DEBOUNCE_MS,
  MIN_GAP_MS,
  markWorkspaceOpenedAfter,
  startVersionCheckpoints,
} from '@/hooks/useVersionCheckpoints'
import { useTimelineStore } from '@/store/timelineStore'
import { captureMoment, markMoment, takeCheckpoint } from './timelineCheckpoint'
import { composeTimelineThumb } from './timelineThumb'
import { groupTimeline } from './timelineGroups'

const text = (id: string): TextObject => ({
  id, type: 'text', text: 'x', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

interface Post {
  url: string
  headers: Record<string, string>
  body: Record<string, unknown>
}
const posts: Post[] = []
let seq = 0
/** 下一次创建节点的请求怎么回：`fail` = 500，`skip` = 服务端判重 */
let nextCreate: 'fail' | 'skip' | null = null
let createAttempts = 0

beforeEach(async () => {
  posts.length = 0
  nextCreate = null
  createAttempts = 0
  localStorage.clear()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    if (String(url).includes('/api/versions/') && init?.method === 'POST' && nextCreate) {
      createAttempts += 1
      const how = nextCreate
      nextCreate = null
      return how === 'fail'
        ? new Response(JSON.stringify({ error: '磁盘一时写不进' }), { status: 500 })
        : new Response(JSON.stringify({ skipped: true, version: { id: 'v_prev' } }), { status: 200 })
    }
    if (String(url).includes('/api/versions/') && init?.method === 'POST') {
      createAttempts += 1
      posts.push({
        url: String(url),
        headers: (init.headers ?? {}) as Record<string, string>,
        body: JSON.parse(String(init.body)),
      })
      seq += 1
      return new Response(JSON.stringify({ version: { id: `v${seq}` } }), { status: 200 })
    }
    return new Response('{}', { status: 404 })
  }) as typeof fetch
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cp')
})

afterEach(() => {
  vi.useRealTimers()
  setCurrentProjectId(null)
  delete (window as unknown as Record<string, unknown>).__TAVOTTO_TIMELINE_TIMING__
})

const edit = (id: string) =>
  useDocumentStore.getState().commit({ key: 'x', ns: 'workspace' }, (d) => {
    d.objects.push(text(id))
  })

describe('takeCheckpoint', () => {
  it('用户起的名字 → named；程序起的名字（恢复前）不是命名节点', async () => {
    edit('t1')
    await takeCheckpoint({ auto: false, name: '投稿前' })
    await takeCheckpoint({ auto: true, moment: 'before_restore', name: '恢复前（10:32）', programName: true })
    expect(posts[0].body).toMatchObject({ named: true, name: '投稿前', auto: false })
    expect(posts[1].body).toMatchObject({ moment: 'before_restore', auto: true })
    expect(posts[1].body.named).toBeUndefined()
  })

  it('空画布不拍自动节点；命名 / 恢复前明确要拍时照拍', async () => {
    await takeCheckpoint({ auto: true })
    expect(posts).toHaveLength(0)
    await takeCheckpoint({ auto: false, name: '空白起点', allowEmpty: true })
    expect(posts).toHaveLength(1)
  })

  it('节点属于**拍的那一刻**的项目：调用之后项目立刻换掉，请求仍带原来的 pj', async () => {
    setCurrentProjectId('p_A')
    edit('t1')
    const pending = takeCheckpoint({ auto: true, moment: 'close' })
    setCurrentProjectId('p_B') // 回主页 / 切项目紧跟着发生
    await pending
    expect(posts[0].headers['X-Tavotto-Project']).toBe('p_A')
    expect(posts[0].url).toContain('pj=p_A')
    expect(currentProjectId()).toBe('p_B')
  })
})

describe('节点记下的画布名（Codex #679）', () => {
  it('当前画布刚改名就打节点：记下的是新名字（取活文档，不取还没同步的画布列表）', async () => {
    edit('t1')
    const st = useDocumentStore.getState()
    st.renameCanvas(st.activeCanvasId, '改过的名字')
    expect(useDocumentStore.getState().canvases.find((c) => c.id === st.activeCanvasId)?.name).not.toBe(
      '改过的名字',
    ) // 前提：列表里那一条此刻确实还是旧名字
    await takeCheckpoint({ auto: false, name: '存一个', allowEmpty: true })
    expect(posts[0].body.canvasName).toBe('改过的名字')
  })
})

describe('启动时的「打开项目」点只打给启动时那个项目（Codex #679）', () => {
  const deferred = () => {
    let resolve!: () => void
    const promise = new Promise<void>((r) => (resolve = r))
    return { promise, resolve }
  }

  it('加载途中切到别的项目：一个 open 都不打（新项目自己的「打开」由 adoptNow 打）', async () => {
    const stop = startVersionCheckpoints()
    setCurrentProjectId('p_A')
    edit('t1')
    const ready = deferred()
    markWorkspaceOpenedAfter(ready.promise)
    useTimelineStore.getState().clear() // 换项目：代际 +1
    setCurrentProjectId('p_B')
    ready.resolve()
    await new Promise((r) => setTimeout(r, 0))
    stop()
    expect(posts.filter((p) => p.body.moment === 'open')).toHaveLength(0)
  })

  it('Workspace 卸载（取消）之后才到齐：不打', async () => {
    const stop = startVersionCheckpoints()
    edit('t1')
    const ready = deferred()
    markWorkspaceOpenedAfter(ready.promise)()
    ready.resolve()
    await new Promise((r) => setTimeout(r, 0))
    stop()
    expect(posts.filter((p) => p.body.moment === 'open')).toHaveLength(0)
  })

  it('对照：不切换，到齐之后照常打一个', async () => {
    const stop = startVersionCheckpoints()
    setCurrentProjectId('p_A')
    edit('t1')
    const ready = deferred()
    markWorkspaceOpenedAfter(ready.promise)
    ready.resolve()
    await vi.waitFor(() => expect(posts.filter((p) => p.body.moment === 'open')).toHaveLength(1))
    stop()
  })
})

describe('关键时刻', () => {
  it('时间线没在跑时 markMoment 什么都不发（单元测试里的成功路径不多一个请求）', async () => {
    edit('t1')
    await markMoment('export')
    expect(posts).toHaveLength(0)
  })

  it.each([
    ['保存（事件带快照）', async () => {
      const { emitLayoutSaved } = await import('@/lib/layoutSaved')
      emitLayoutSaved('local', { moment: captureMoment() })
    }, 'save'],
    ['打开', () => markMoment('open'), 'open'],
    ['离开', () => markMoment('close'), 'close'],
  ] as const)('空画布上%s也打一个关键时刻（Codex #679）', async (_name, fire, moment) => {
    const stop = startVersionCheckpoints()
    expect(useDocumentStore.getState().doc.objects).toHaveLength(0)
    await fire()
    await vi.waitFor(() => expect(posts.map((p) => p.body.moment)).toEqual([moment]))
    stop()
  })

  it('对照：普通自动节点在空画布上仍然不拍', async () => {
    await takeCheckpoint({ auto: true })
    expect(posts).toHaveLength(0)
  })

  it('时间线在跑：每个关键时刻打一个带标记的点', async () => {
    const stop = startVersionCheckpoints()
    edit('t1')
    await markMoment('export')
    await markMoment('writeback')
    await markMoment('save')
    stop()
    expect(posts.map((p) => p.body.moment)).toEqual(['export', 'writeback', 'save'])
    expect(posts.every((p) => p.body.auto === true)).toBe(true)
  })
})

describe('关键时刻拍发起那一刻的内容（快照；Codex #679）', () => {
  const ids = (p: Post) => (p.body.doc as { objects: { id: string }[] }).objects.map((o) => o.id)

  it('发起之后又改了同一份排版：节点里是发起时那份，缩略图用发起时取的图源', async () => {
    const stop = startVersionCheckpoints()
    edit('t1')
    const snap = captureMoment()
    edit('t2') // 导出 / 保存 / 写回途中接着改
    await markMoment('writeback', snap)
    await vi.waitFor(() => expect(posts.filter((p) => p.body.moment)).toHaveLength(1))
    stop()
    const node = posts.find((p) => p.body.moment)!
    expect(ids(node)).toEqual(['t1'])
    expect(vi.mocked(composeTimelineThumb)).toHaveBeenLastCalledWith(snap.identity.doc, snap.thumb)
  })

  it('对照：不带快照（打开 / 离开这类同步时刻）拍的是此刻', async () => {
    const stop = startVersionCheckpoints()
    edit('t1')
    edit('t2')
    await markMoment('open')
    await vi.waitFor(() => expect(posts.filter((p) => p.body.moment)).toHaveLength(1))
    stop()
    expect(ids(posts.find((p) => p.body.moment)!)).toEqual(['t1', 't2'])
  })

  it('保存事件：拍的是事件带来的那份（写出去的那份），不是此刻', async () => {
    const stop = startVersionCheckpoints()
    edit('t1')
    const written = captureMoment()
    edit('t2')
    const { emitLayoutSaved } = await import('@/lib/layoutSaved')
    emitLayoutSaved('project_file', { moment: written })
    await vi.waitFor(() => expect(posts.filter((p) => p.body.moment === 'save')).toHaveLength(1))
    stop()
    expect(ids(posts.find((p) => p.body.moment === 'save')!)).toEqual(['t1'])
  })
})

describe('关键时刻挂在各自的成功点上', () => {
  const job = (status: string) =>
    ({ job_id: 'j1', status, outputs: [], warnings: [], conflicts: [], error: null }) as never

  it('导出交付了文件（done / partial）打一个 export；失败不打；同一作业只打一次', async () => {
    const { applyExportJob, useExportStore } = await import('@/store/exportStore')
    const stop = startVersionCheckpoints()
    edit('t1')
    useExportStore.setState({ ownedJobId: 'j1', job: null })
    applyExportJob(job('running'))
    applyExportJob(job('done'))
    applyExportJob(job('done')) // 晚到的重复快照
    await Promise.resolve()
    useExportStore.setState({ ownedJobId: 'j2', job: null })
    applyExportJob({ ...(job('failed') as object), job_id: 'j2' } as never)
    await vi.waitFor(() => expect(posts.length).toBeGreaterThan(0))
    await new Promise((r) => setTimeout(r, 0))
    stop()
    expect(posts.map((p) => p.body.moment)).toEqual(['export'])
  })

  it('「排版写进了文件」（保存侧发的事件）打一个 save；时间线停了就不听', async () => {
    const { emitLayoutSaved } = await import('@/lib/layoutSaved')
    const stop = startVersionCheckpoints()
    edit('t1')
    emitLayoutSaved('project_file', { moment: captureMoment() })
    await vi.waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0].body.moment).toBe('save')
    stop()
    emitLayoutSaved('layout_file', { moment: captureMoment() })
    await new Promise((r) => setTimeout(r, 0))
    expect(posts).toHaveLength(1)
    // 停了再起（换项目重挂）：旧的订阅不能留着——留着的话一次保存打两个点
    const again = startVersionCheckpoints()
    emitLayoutSaved('local', { moment: captureMoment() })
    await vi.waitFor(() => expect(posts).toHaveLength(2))
    await new Promise((r) => setTimeout(r, 0))
    again()
    expect(posts).toHaveLength(2)
  })

  it('关键时刻带着发起那一刻的上下文：换了排版才完成的，不给新排版打点', async () => {
    const stop = startVersionCheckpoints()
    edit('t1')
    const atA = captureMoment()
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cp_other')
    edit('t2')
    await markMoment('writeback', atA)
    const { emitLayoutSaved } = await import('@/lib/layoutSaved')
    emitLayoutSaved('local', { moment: atA })
    await new Promise((r) => setTimeout(r, 0))
    expect(posts.filter((p) => p.body.moment)).toEqual([])
    // 对照：带上此刻的上下文就照常打
    await markMoment('writeback', captureMoment())
    await vi.waitFor(() => expect(posts.map((p) => p.body.moment)).toContain('writeback'))
    expect(posts.find((p) => p.body.moment)!.url).toContain('d_cp_other')
    stop()
  })

  it('回主页（showPicker）打一个 close，拍的是离开的那个项目', async () => {
    const { useProjectStore } = await import('@/store/projectStore')
    const stop = startVersionCheckpoints()
    setCurrentProjectId('p_A')
    edit('t1')
    useProjectStore.setState({ phase: 'open', switching: false, project: { open: true, id: 'p_A' } } as never)
    useProjectStore.getState().showPicker()
    await vi.waitFor(() => expect(posts).toHaveLength(1))
    stop()
    expect(posts[0].body.moment).toBe('close')
    expect(posts[0].headers['X-Tavotto-Project']).toBe('p_A')
  })
})

describe('离开项目：先收手势、再打 close（两条路同一份顺序，Codex #679）', () => {
  const closeIds = () =>
    posts
      .filter((p) => p.body.moment === 'close')
      .map((p) => (p.body.doc as { objects: { id: string }[] }).objects.map((o) => o.id))

  it.each([
    ['编辑器开着直接切项目（adoptNow）', async () => {
      const { useProjectStore } = await import('@/store/projectStore')
      void useProjectStore
        .getState()
        .adoptOpenedProject({ open: true, id: 'p_B', name: 'B' } as never)
        .catch(() => {})
    }],
    ['回主页（showPicker）', async () => {
      const { useProjectStore } = await import('@/store/projectStore')
      useProjectStore.getState().showPicker()
    }],
  ] as const)('%s：close 节点拍的是手势落定之后的内容', async (_name, leave) => {
    const { useProjectStore } = await import('@/store/projectStore')
    const { registerGesture } = await import('@/store/gestureCoordinator')
    const stop = startVersionCheckpoints()
    setCurrentProjectId('p_A')
    edit('t1')
    // 一轮还开着的连续编辑：收尾时才落定最后那一笔
    registerGesture(() => edit('t_settled'))
    useProjectStore.setState({ phase: 'open', switching: false, project: { open: true, id: 'p_A' } } as never)
    await leave()
    await vi.waitFor(() => expect(closeIds()).toHaveLength(1))
    stop()
    expect(closeIds()[0]).toEqual(['t1', 't_settled'])
    expect(posts.find((p) => p.body.moment === 'close')!.headers['X-Tavotto-Project']).toBe('p_A')
  })
})

describe('自动节点的间隔（2 分钟）', () => {
  it('停顿 15 s 拍第一个；2 分钟内的第二段编辑等到满 2 分钟才拍', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const stop = startVersionCheckpoints()
    edit('t1')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    expect(posts).toHaveLength(1)
    edit('t2')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    expect(posts).toHaveLength(1) // 还没满 2 分钟
    await vi.advanceTimersByTimeAsync(MIN_GAP_MS)
    expect(posts).toHaveLength(2)
    stop()
    expect(MIN_GAP_MS).toBe(120_000)
  })

  it('间隔按排版分别计：A 刚拍过，切到 B 停顿满 15 s 就拍 B 的，不等 A 的 2 分钟（Codex #679）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const stop = startVersionCheckpoints()
    edit('t1')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    expect(posts.map((p) => p.url)).toEqual([expect.stringContaining('d_cp')])
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cp_b')
    edit('b1')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    stop()
    expect(posts).toHaveLength(2)
    expect(posts[1].url).toContain('d_cp_b')
  })

  it('自动节点没写成（请求失败）不记间隔：下一次停顿满 15 s 就重试（Codex #679）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const stop = startVersionCheckpoints()
    nextCreate = 'fail'
    edit('t1')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    expect(createAttempts).toBe(1)
    expect(posts).toHaveLength(0)
    edit('t2')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    stop()
    expect(createAttempts).toBe(2)
    expect(posts).toHaveLength(1)
  })

  it('服务端判重（内容已经在时间线上）算写成：照常重新计间隔', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const stop = startVersionCheckpoints()
    nextCreate = 'skip'
    edit('t1')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    expect(createAttempts).toBe(1)
    edit('t2')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    stop()
    expect(createAttempts).toBe(1) // 还没满 2 分钟
  })

  it('手势还开着时不拍自动节点（不拍中间态）；落定之后照常拍（Codex #679）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const { registerGesture } = await import('@/store/gestureCoordinator')
    const stop = startVersionCheckpoints()
    const done = registerGesture(() => {})
    edit('t1')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS * 2 + 10)
    expect(posts).toHaveLength(0)
    done() // 手势结束（松手）
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    stop()
    expect(posts).toHaveLength(1)
  })

  it('对照：同一份排版里 2 分钟的间隔照旧', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const stop = startVersionCheckpoints()
    edit('t1')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    edit('t2')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    stop()
    expect(posts).toHaveLength(1)
  })

  it('e2e 的时序注入只改这一次启动的间隔，不改产品默认值', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_TIMELINE_TIMING__ = {
      debounceMs: 200,
      minGapMs: 500,
    }
    const stop = startVersionCheckpoints()
    edit('t1')
    await vi.advanceTimersByTimeAsync(250)
    edit('t2')
    await vi.advanceTimersByTimeAsync(800)
    stop()
    expect(posts).toHaveLength(2)
    expect(DEBOUNCE_MS).toBe(15_000)
  })

  it('形状不对的注入当没给', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    ;(window as unknown as Record<string, unknown>).__TAVOTTO_TIMELINE_TIMING__ = {
      debounceMs: 'fast',
      minGapMs: -1,
    }
    const stop = startVersionCheckpoints()
    edit('t1')
    await vi.advanceTimersByTimeAsync(1000)
    expect(posts).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS)
    expect(posts).toHaveLength(1)
    stop()
  })

  it('刚打过关键时刻，紧跟着的自动节点等满间隔（不重复拍同一刻）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    const stop = startVersionCheckpoints()
    edit('t1')
    await markMoment('export')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 10)
    expect(posts.map((p) => p.body.moment ?? 'auto')).toEqual(['export'])
    stop()
  })
})

describe('groupTimeline', () => {
  const v = (id: string, ts: number, kind: 'auto' | 'named' = 'auto') => ({
    id, ts, kind, name: id, auto: kind === 'auto', description: '', objects: 0,
  })
  const now = new Date(2026, 8, 27, 12, 0).getTime()

  it('今天 / 昨天 / 日期分组；跨过午夜的两个节点不在同一组', () => {
    const g = groupTimeline(
      [
        v('a', new Date(2026, 8, 27, 0, 5).getTime()),
        v('b', new Date(2026, 8, 26, 23, 55).getTime()),
        v('c', new Date(2026, 8, 20, 9, 0).getTime()),
      ],
      { namedOnly: false, now },
    )
    expect(g.map((x) => x.day.kind)).toEqual(['today', 'yesterday', 'date'])
    expect(g.map((x) => x.items.map((i) => i.id))).toEqual([['a'], ['b'], ['c']])
  })

  it('组内按时间倒序，不依赖输入顺序', () => {
    const g = groupTimeline([v('early', now - 3000), v('late', now - 1000), v('mid', now - 2000)], {
      namedOnly: false,
      now,
    })
    expect(g[0].items.map((i) => i.id)).toEqual(['late', 'mid', 'early'])
  })

  it('只看命名：别的都滤掉，空组不出现', () => {
    const g = groupTimeline([v('n', now - 2 * 86_400_000, 'named'), v('a', now - 1000)], {
      namedOnly: true,
      now,
    })
    expect(g).toHaveLength(1)
    expect(g[0].items.map((i) => i.id)).toEqual(['n'])
  })
})
