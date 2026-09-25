/**
 * 磁盘写盘协调器的纯用例：假端口 + 手动 gate，不起 store、不碰 localStorage。
 *
 * 与 `store/documentStore.test.ts` / `saveStateMachine.test.ts` 的分工：那两份从
 * store 的公开入口（flushAutosave / saveNow / …）往下看整条链；这里直接量协调器
 * 自己的时序——同 id 合并、跨 id 串行、pj 随载荷、409 不推基线、写前只探一次、
 * 冲突挡住排队那份、排空才唤醒等待方、手动标志只消费一次。
 */
import { describe, expect, it } from 'vitest'
import { ApiError, REVISION_ABSENT } from '@/lib/api'
import { emptyProject } from '@/types/document'
import type { ProjectDocument } from '@/types/document'
import {
  blocksDiskWrite,
  conflictIssue,
  createDiskWriter,
  hasUnsavedWork,
  type DiskWriterPorts,
  type SaveIssue,
  type SaveOutcome,
  type SaveTrigger,
} from './diskWriter'

const doc = (updatedAt: number): ProjectDocument => ({ ...emptyProject(), updatedAt })

/** 把在途的微任务放干净（假 API 全是立刻 resolve / reject 的 Promise，微任务足够） */
const tick = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve()
}

interface Put {
  id: string
  updatedAt: number
  baseline: number | undefined
  revision: string | undefined
  pj: string | null
}

/**
 * 一个可控的假磁盘：`puts` 记每一次 PUT 的参数；`gate` 非空时 PUT 挂起、`release()`
 * 放行；`putMode` 决定下一次 PUT 的结局；`onDisk` 决定写前探测（fetch）看到什么。
 */
function harness(opts: { current?: string; blocked?: () => boolean } = {}) {
  const puts: Put[] = []
  const fetches: string[] = []
  const events: string[] = []
  const outcomes: [SaveTrigger, SaveOutcome][] = []
  const dropped: string[] = []
  let putMode: 'ok' | 'stale' | 'external' | 'io' = 'ok'
  let gate: (() => void) | null = null
  let onDisk: { revision: string | null } | null = null
  let fetchThrows = false
  let current = opts.current ?? 'A'
  const ports: DiskWriterPorts = {
    api: {
      put: (id, pd, baseline, revision, pj) => {
        puts.push({ id, updatedAt: pd.updatedAt, baseline, revision, pj })
        const settle = () => {
          if (putMode === 'ok') return Promise.resolve({ saved_at: 1000, revision: `r${pd.updatedAt}` })
          if (putMode === 'io') return Promise.reject(new Error('boom'))
          return Promise.reject(
            new ApiError('409', 409, {
              code: putMode === 'stale' ? 'stale_write' : 'external_change',
              revision: 'disk-rev',
            }),
          )
        }
        if (!gate) return settle()
        return new Promise((resolve, reject) => {
          const g = gate as () => void
          gate = () => {
            g()
            settle().then(resolve, reject)
          }
        })
      },
      fetch: (id) => {
        fetches.push(id)
        if (fetchThrows) return Promise.reject(new Error('down'))
        return Promise.resolve(onDisk)
      },
      fetchSummary: () => Promise.resolve(null),
    },
    isCurrent: (id) => id === current,
    onSaving: (id) => events.push(`saving:${id}`),
    onWritten: (id, savedAt) => events.push(`written:${id}@${savedAt}`),
    onFailed: (id, issue) => events.push(`failed:${id}:${issue ? issue.kind : 'io'}`),
    writeBlocked: opts.blocked ?? (() => false),
    dropLocalCopy: (id) => dropped.push(id),
    outcome: (t, o) => outcomes.push([t, o]),
  }
  const writer = createDiskWriter(ports)
  return {
    writer,
    puts,
    fetches,
    events,
    outcomes,
    dropped,
    setPutMode: (m: typeof putMode) => {
      putMode = m
    },
    setOnDisk: (v: typeof onDisk) => {
      onDisk = v
    },
    setFetchThrows: (v: boolean) => {
      fetchThrows = v
    },
    setCurrent: (id: string) => {
      current = id
    },
    holdPuts: () => {
      gate = () => {}
    },
    release: () => {
      const g = gate
      gate = null
      g?.()
    },
  }
}

describe('队列：同 id 合并、跨 id 串行、pj 随载荷', () => {
  it('同一份文档在写盘期间再排两次只留最新一份，写完接着写它', async () => {
    const h = harness()
    h.writer.rememberRevision('A', null)
    h.holdPuts()
    h.writer.schedule('A', doc(1), 'p1')
    h.writer.schedule('A', doc(2), 'p1')
    h.writer.schedule('A', doc(3), 'p1')
    expect(h.writer.busy).toBe(true)
    expect(h.writer.queued).toBe(1)
    h.release()
    await tick()
    expect(h.puts.map((p) => p.updatedAt)).toEqual([1, 3])
    expect(h.writer.busy).toBe(false)
  })

  it('不同文档依次串行，各自带自己排队那一刻的项目 id', async () => {
    const h = harness()
    h.writer.rememberRevision('A', null)
    h.writer.rememberRevision('B', null)
    h.holdPuts()
    h.writer.schedule('A', doc(1), 'p1')
    h.writer.schedule('B', doc(2), 'p2')
    h.release()
    await tick()
    expect(h.puts.map((p) => [p.id, p.pj])).toEqual([
      ['A', 'p1'],
      ['B', 'p2'],
    ])
  })
})

describe('基线：成功推进、409 一个都不推', () => {
  it('写成功后 updatedAt 与后端回的 revision 成为下一次 PUT 的基线', async () => {
    const h = harness()
    h.writer.rememberRevision('A', null)
    h.writer.schedule('A', doc(10), null)
    await tick()
    h.writer.schedule('A', doc(11), null)
    await tick()
    expect(h.puts[0].baseline).toBeUndefined()
    expect(h.puts[0].revision).toBe(REVISION_ABSENT)
    expect(h.puts[1].baseline).toBe(10)
    expect(h.puts[1].revision).toBe('r10')
    expect(h.dropped).toEqual(['A', 'A'])
    expect(h.outcomes).toEqual([
      ['autosave', 'ok'],
      ['autosave', 'ok'],
    ])
  })

  it('409 之后基线原样：下一次写仍带旧基线，而不是转头盖掉对方', async () => {
    const h = harness()
    h.writer.rememberRevision('A', { revision: 'old' })
    h.writer.setBaseline('A', 5)
    h.setPutMode('stale')
    h.writer.schedule('A', doc(6), null)
    await tick()
    expect(h.events).toEqual(['saving:A', 'failed:A:stale'])
    expect(h.outcomes).toEqual([['autosave', 'conflict']])
    expect(h.dropped).toEqual([])
    h.setPutMode('ok')
    h.writer.schedule('A', doc(7), null)
    await tick()
    expect(h.puts[1].baseline).toBe(5)
    expect(h.puts[1].revision).toBe('old')
  })

  it('io 失败报 failed、状态回 io，本机副本不清', async () => {
    const h = harness()
    h.writer.rememberRevision('A', null)
    h.setPutMode('io')
    h.writer.schedule('A', doc(1), null)
    await tick()
    expect(h.events).toEqual(['saving:A', 'failed:A:io'])
    expect(h.outcomes).toEqual([['autosave', 'failed']])
    expect(h.dropped).toEqual([])
  })
})

describe('写前确认：本会话没确认过磁盘状况的文档先探一次', () => {
  it('磁盘上没有 → 记成 absent、照常写；之后不再探', async () => {
    const h = harness()
    h.setOnDisk(null)
    h.writer.schedule('A', doc(1), null)
    await tick()
    h.writer.schedule('A', doc(2), null)
    await tick()
    expect(h.fetches).toEqual(['A'])
    expect(h.puts.map((p) => p.revision)).toEqual([REVISION_ABSENT, 'r1'])
  })

  it('磁盘上有一份我从没读过的 → 这是冲突（external），不写', async () => {
    const h = harness()
    h.setOnDisk({ revision: 'someone' })
    h.writer.schedule('A', doc(1), null)
    await tick()
    expect(h.puts).toEqual([])
    expect(h.events).toEqual(['saving:A', 'failed:A:external'])
    expect(h.outcomes).toEqual([['autosave', 'conflict']])
    // 探过一次就记住了：下一次不再探（用户裁决后 store 会改基线）
    h.writer.schedule('A', doc(2), null)
    await tick()
    expect(h.fetches).toEqual(['A'])
  })

  it('探不到（后端不可达）→ 不猜不记，这次照常写；写也没成的话下次仍要探', async () => {
    const h = harness()
    h.setFetchThrows(true)
    h.setPutMode('io')
    h.writer.schedule('A', doc(1), null)
    await tick()
    expect(h.puts).toHaveLength(1)
    expect(h.puts[0].revision).toBeUndefined()
    expect(h.writer.knowsRevision('A')).toBe(false)
    h.writer.schedule('A', doc(2), null)
    await tick()
    expect(h.fetches).toEqual(['A', 'A'])
  })

  it('探不到但写成了 → 后端回的 revision 就是基线，之后不再探', async () => {
    const h = harness()
    h.setFetchThrows(true)
    h.writer.schedule('A', doc(1), null)
    await tick()
    expect(h.writer.knowsRevision('A')).toBe(true)
    h.writer.schedule('A', doc(2), null)
    await tick()
    expect(h.fetches).toEqual(['A'])
    expect(h.puts[1].revision).toBe('r1')
  })

  it('三档不压扁：读过但拿不到 hash 是 null（不带、不再探），不是缺席', async () => {
    const h = harness()
    h.writer.rememberRevision('A', { revision: null })
    expect(h.writer.knowsRevision('A')).toBe(true)
    h.writer.schedule('A', doc(1), null)
    await tick()
    expect(h.fetches).toEqual([])
    expect(h.puts[0].revision).toBeUndefined()
  })
})

describe('冲突挡住排队那份、排空才唤醒等待方、手动标志只消费一次', () => {
  it('当前文档冲突未决时，队列里排着的它那份不再往磁盘上撞', async () => {
    let blocked = false
    const h = harness({ blocked: () => blocked })
    h.writer.rememberRevision('A', { revision: 'old' })
    h.holdPuts()
    h.setPutMode('external')
    h.writer.schedule('A', doc(1), null)
    h.writer.schedule('A', doc(2), null)
    blocked = true // store 收到 failed 后把状态推成 conflict
    h.release()
    await tick()
    expect(h.puts).toHaveLength(1)
    expect(h.writer.queued).toBe(0)
    expect(h.writer.busy).toBe(false)
  })

  it('冲突挡的是**当前**文档；排着的是别的文档照样写', async () => {
    const h = harness({ blocked: () => true })
    h.writer.rememberRevision('A', null)
    h.writer.rememberRevision('B', null)
    h.holdPuts()
    h.writer.schedule('A', doc(1), null)
    h.writer.schedule('B', doc(2), null)
    h.release()
    await tick()
    expect(h.puts.map((p) => p.id)).toEqual(['A', 'B'])
  })

  it('当前文档那份被冲突挡下后，排在它后面的别的文档照写，whenIdle 等它写完才 resolve（#411）', async () => {
    let blocked = false
    const h = harness({ blocked: () => blocked })
    h.writer.rememberRevision('A', { revision: 'old' })
    h.writer.rememberRevision('B', null)
    h.holdPuts()
    h.setPutMode('external')
    h.writer.schedule('A', doc(1), null)
    h.writer.schedule('A', doc(2), null) // 出队的第一份恰好是 A 自己排的那份
    h.writer.schedule('B', doc(3), null)
    let idle = false
    void h.writer.whenIdle().then(() => {
      idle = true
    })
    await tick() // 让 A 的 PUT 真正挂到 gate 上
    expect(idle).toBe(false)
    blocked = true // store 收到 failed 后把状态推成 conflict
    h.release() // A 在途那次此刻结算成 409
    h.setPutMode('ok')
    await tick()
    // A 在途那次 409；A 排队那份被挡、B 照样落到磁盘上
    expect(h.puts.map((p) => [p.id, p.updatedAt])).toEqual([
      ['A', 1],
      ['B', 3],
    ])
    expect(h.events).toEqual(['saving:A', 'failed:A:external', 'saving:B', 'written:B@1000'])
    expect(h.writer.queued).toBe(0)
    expect(h.writer.busy).toBe(false)
    expect(idle).toBe(true)
  })

  it('whenIdle 在在途那次与队列都排空之后才 resolve；空队列立刻 resolve', async () => {
    const h = harness()
    h.writer.rememberRevision('A', null)
    let idle = false
    void h.writer.whenIdle().then(() => {
      idle = true
    })
    await tick()
    expect(idle).toBe(true)
    idle = false
    h.holdPuts()
    h.writer.schedule('A', doc(1), null)
    h.writer.schedule('A', doc(2), null)
    void h.writer.whenIdle().then(() => {
      idle = true
    })
    await tick()
    expect(idle).toBe(false) // 在途那次被 gate 挂着，队列里还有第二份
    h.release()
    await tick()
    expect(h.puts).toHaveLength(2) // 放行后第二份接着写完
    expect(idle).toBe(true)
  })

  it('markManual 只落到下一次真正开始的写盘上，之后的又是 autosave', async () => {
    const h = harness()
    h.writer.rememberRevision('A', null)
    h.writer.markManual()
    h.writer.schedule('A', doc(1), null)
    await tick()
    h.writer.schedule('A', doc(2), null)
    await tick()
    expect(h.outcomes).toEqual([
      ['manual', 'ok'],
      ['autosave', 'ok'],
    ])
  })

  it('回调按写的那份文档的 id 调，不看当前文档是谁——判「改不改状态」归端口', async () => {
    const h = harness({ current: 'B' })
    h.writer.rememberRevision('A', null)
    h.writer.schedule('A', doc(1), null)
    await tick()
    expect(h.events).toEqual(['saving:A', 'written:A@1000'])
    expect(h.dropped).toEqual(['A'])
  })
})

describe('判据与冲突形状', () => {
  it('conflictIssue：external / stale 分档，409 体里的 revision 成为可覆盖的基线', () => {
    const external = conflictIssue('A', new ApiError('409', 409, { code: 'external_change', revision: 'x' }))
    expect(external.kind).toBe('external')
    expect(external.disk?.revision).toBe('x')
    const stale = conflictIssue('A', new ApiError('409', 409, { code: 'stale_write' }))
    expect(stale.kind).toBe('stale')
    expect(stale.disk).toBeNull()
  })

  it('blocksDiskWrite 只在 conflict；hasUnsavedWork 在 dirty / saving / save_error / conflict', () => {
    const all = ['clean', 'dirty', 'saving', 'saved', 'save_error', 'conflict'] as const
    expect(all.filter(blocksDiskWrite)).toEqual(['conflict'])
    expect(all.filter(hasUnsavedWork)).toEqual(['dirty', 'saving', 'save_error', 'conflict'])
    const issue: SaveIssue = { kind: 'io', docId: 'A' }
    expect(issue.disk).toBeUndefined()
  })
})
