/**
 * 时间线打点的唯一入口（ADR 0101）：自动节点的间隔、关键时刻、命名与「程序起的名字」、
 * 以及「关项目那一刻」拍的是哪个项目。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/timelineThumb', () => ({ composeTimelineThumb: vi.fn(async () => null) }))

import { emptyProject, type TextObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { DEBOUNCE_MS, MIN_GAP_MS, startVersionCheckpoints } from '@/hooks/useVersionCheckpoints'
import { markMoment, takeCheckpoint } from './timelineCheckpoint'
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

beforeEach(async () => {
  posts.length = 0
  localStorage.clear()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    if (String(url).includes('/api/versions/') && init?.method === 'POST') {
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

describe('关键时刻', () => {
  it('时间线没在跑时 markMoment 什么都不发（单元测试里的成功路径不多一个请求）', async () => {
    edit('t1')
    await markMoment('export')
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
