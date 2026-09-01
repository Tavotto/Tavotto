/**
 * 导出作业的编排（ADR 0031）。
 *
 * 四件事各一组：晚到的快照不许把界面倒回进行中、SSE 与轮询是同一条落点、
 * 完成时用**此刻的文档**判"期间有没有被编辑"、失败保留用户设置。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ExportJob } from '@/lib/api'
import {
  applyExportJob,
  cancelCurrentExport,
  liveRevision,
  prepareExport,
  resetExportState,
  runExport,
  useExportStore,
} from './exportStore'
import { useDocumentStore } from './documentStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { literal } from '@/i18n'
import type { ExportRequestInput } from '@/lib/exportRequest'

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
}

const job = (over: Partial<ExportJob> = {}): ExportJob => ({
  job_id: 'j1',
  status: 'running',
  outputs: [],
  warnings: [],
  conflicts: [],
  error: null,
  ...over,
})

const doneOutput = {
  format: 'pdf',
  name: 'Fig 1.pdf',
  url: '/exports/Fig 1.pdf',
  bytes: 10,
  dimensions: { px: null, mm: [80, 60] as [number, number] },
  vector: true,
  status: 'done' as const,
  replaced: false,
  error: null,
}

function inputOf(over: Partial<ExportRequestInput> = {}): ExportRequestInput {
  return {
    scope: 'canvas',
    formats: ['pdf'],
    filename: 'Fig 1',
    ppi: 600,
    documentId: 'd1',
    doc: useDocumentStore.getState().doc,
    ...over,
  }
}

let bodies: Record<string, unknown>[] = []

beforeEach(async () => {
  resetExportState()
  bodies = []
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_store')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 180, h: 120 }
    d.objects = [{ ...panel }]
  })
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    // **只记导出的请求**：`switchDocument` / `commit` 会带出一次自动保存，
    // 把它也算进来的话「prepareExport 不发网络」那条判据量的就不是导出
    if (String(input).includes('/api/export')) {
      bodies.push(JSON.parse(String(init?.body ?? '{}')))
    }
    return new Response(JSON.stringify(job({ status: 'done', outputs: [doneOutput] })), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch
})

afterEach(() => {
  resetExportState()
  vi.restoreAllMocks()
})

describe('起作业', () => {
  it('文件名不合法时**根本不发请求**，并保留输入以便改完重试', async () => {
    const res = await runExport(inputOf({ filename: 'Fig?1' }))
    expect(res).toBeNull()
    expect(bodies).toHaveLength(0)
    expect(useExportStore.getState().startError?.code).toBe('bad_filename')
    expect(useExportStore.getState().lastInput?.filename).toBe('Fig?1')
  })

  it('终局直接回来时不再轮询', async () => {
    const res = await runExport(inputOf())
    expect(res?.status).toBe('done')
    expect(useExportStore.getState().running).toBe(false)
    expect(useExportStore.getState().job?.outputs).toHaveLength(1)
  })
})

describe('晚到的快照', () => {
  it('已经进终局的作业不许被一条绕远路的 running 倒回进行中', async () => {
    await runExport(inputOf())
    expect(useExportStore.getState().running).toBe(false)
    applyExportJob(job({ status: 'running', progress: { phase: 'rendering', step: 0, total: 1 } }))
    expect(useExportStore.getState().running).toBe(false)
    expect(useExportStore.getState().job?.status).toBe('done')
  })
})

describe('作业在服务器上没了', () => {
  it('`unknown` 是终局：停止轮询，界面不再停在"进行中"', async () => {
    await runExport(inputOf())
    // 后端重启：/state 回 unknown
    applyExportJob(job({ status: 'unknown', outputs: [] }))
    // 已经进过终局的作业挡掉晚到快照，所以先重置再单独验 unknown 这一档
    resetExportState()
    useExportStore.setState({
      running: true,
      startedRevision: 'x',
      lastInput: inputOf(),
      ownedJobId: 'j1', // 只有自己认领过的作业才收快照
    })
    applyExportJob(job({ status: 'unknown', outputs: [] }))
    expect(useExportStore.getState().running, 'unknown 不当终局的话轮询会一直问下去').toBe(
      false,
    )
    expect(useExportStore.getState().job?.status).toBe('unknown')
  })
})

describe('只收自己那个作业的快照', () => {
  it('别的标签页的进度推过来时一律不收', async () => {
    await runExport(inputOf())
    const mine = useExportStore.getState().job
    expect(mine?.job_id).toBe('j1')

    // `export.progress` 是项目级广播：同一个项目的另一个标签页也会推过来
    applyExportJob(job({ job_id: 'other-tab', status: 'running', outputs: [] }))
    expect(useExportStore.getState().job?.job_id, '把别人的作业显示成了自己的').toBe('j1')
    expect(useExportStore.getState().running).toBe(false)
  })

  it('`resetExportState()` 之后，还在飞的那个轮询回来什么都不写', async () => {
    await runExport(inputOf())
    resetExportState()
    // 请求早就发出去了，停轮询停不掉它
    applyExportJob(job({ job_id: 'j1', status: 'done', outputs: [doneOutput] }))
    expect(useExportStore.getState().job, '切项目之后旧作业的迟到快照把状态填回去了').toBeNull()
  })
})

describe('作废的回执', () => {
  it('`startExport()` 还在飞的时候换了项目：那份回执一个字都不写', async () => {
    let release: (() => void) | null = null
    globalThis.fetch = (async () => {
      await new Promise<void>((r) => {
        release = r
      })
      return new Response(JSON.stringify(job({ status: 'done', outputs: [doneOutput] })), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch

    const pending = runExport(inputOf())
    await new Promise<void>((r) => setTimeout(r, 0))
    // 用户在回执回来之前切了项目
    resetExportState()
    release!()
    expect(await pending).toBeNull()
    expect(
      useExportStore.getState().job,
      '作废的回执把旧项目的结果填进了新项目',
    ).toBeNull()
    expect(useExportStore.getState().running).toBe(false)
  })
})

describe('导出期间文档又被改过', () => {
  it('判据用**此刻的文档**，不是导出开始时冻住的那一份', async () => {
    const input = inputOf()
    const before = liveRevision(input)
    // 拿冻住的那份跟自己比永远相等——那条判据于是恒成立、恒不报警，
    // 而空的 diff 与"没变化"长得一模一样
    useDocumentStore.getState().commit(literal('挪'), (d) => {
      d.objects[0].x = 42
    })
    expect(liveRevision(input)).not.toBe(before)
  })

  it('完成时文档变了就标出来；没变就不冒这句话', async () => {
    await runExport(inputOf())
    expect(useExportStore.getState().editedDuringExport).toBe(false)

    resetExportState()
    const started = runExport(inputOf())
    useDocumentStore.getState().commit(literal('挪'), (d) => {
      d.objects[0].x = 77
    })
    await started
    expect(useExportStore.getState().editedDuringExport).toBe(true)
  })
})

describe('取消', () => {
  it('没有在跑的作业时回 false，不发请求', async () => {
    expect(await cancelCurrentExport()).toBe(false)
    expect(bodies).toHaveLength(0)
  })
})

describe('prepareExport 不发网络', () => {
  it('输入框每敲一个字都可以调它', () => {
    const p = prepareExport(inputOf({ filename: 'Fig 1.pdf' }))
    expect(p.request.filename).toBe('Fig 1')
    expect(p.names).toEqual(['Fig 1.pdf'])
    expect(p.filenameProblem).toBeNull()
    expect(bodies).toHaveLength(0)
  })
})

describe('陈旧的轮询不许改排当下的轮询', () => {
  const res = (o: unknown) =>
    new Response(JSON.stringify(o), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })

  /**
   * 摆出那个赛跑：jA 的 `/state` **卡在网络上**（`stopPolling()` 撤不回它），
   * 这期间用户又起了 jB；然后让 jA 那一次按 `finishA` 收场。
   *
   * 两条收场路径都要量：**回来了**走 `.then`，**报错了**走 `.catch`。
   * 只量一条的话另一条上的无条件重排活得好好的。
   */
  const raceStaleAgainst = async (
    finishA: (resolve: (r: Response) => void, reject: (e: unknown) => void) => void,
  ) => {
    const polled: string[] = []
    let startId = 'jA'
    let resolveA!: (r: Response) => void
    let rejectA!: (e: unknown) => void

    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/export/start')) return res(job({ job_id: startId }))
      if (url.includes('/api/export/state')) {
        const id = new URL(url, 'http://x').searchParams.get('job_id') as string
        polled.push(id)
        if (id === 'jA')
          return new Promise<Response>((resolve, reject) => {
            resolveA = resolve
            rejectA = reject
          })
        return res(job({ job_id: id, status: 'done', outputs: [doneOutput] }))
      }
      return res({})
    }) as typeof fetch

    await runExport(inputOf())
    await vi.runOnlyPendingTimersAsync()
    expect(polled).toEqual(['jA'])

    // 用户又起了一次导出：代次 +1，归属换成 jB，jB 自己排了一轮
    startId = 'jB'
    await runExport(inputOf())
    expect(useExportStore.getState().ownedJobId).toBe('jB')

    // 现在 jA 那一次才收场
    finishA(resolveA, rejectA)
    await vi.advanceTimersByTimeAsync(0)

    await vi.runOnlyPendingTimersAsync()
    return polled
  }

  it('迟到的**非终局回执**不许掐掉新作业的轮询（SSE 不通时轮询是唯一通道）', async () => {
    vi.useFakeTimers()
    try {
      const polled = await raceStaleAgainst((resolve) =>
        resolve(res(job({ job_id: 'jA', status: 'running' }))),
      )
      expect(polled, '迟到的 jA 回执把定时器抢回去了，jB 再也不会被轮询').toEqual(['jA', 'jB'])
      expect(useExportStore.getState().job?.job_id).toBe('jB')
      expect(useExportStore.getState().running, 'jB 已完成，界面却还停在进行中').toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('迟到的**失败**同样不许——拒收路径正是陈旧快照必经的那条', async () => {
    vi.useFakeTimers()
    try {
      const polled = await raceStaleAgainst((_resolve, reject) => reject(new Error('断线')))
      expect(polled, 'jA 那次失败重试把定时器抢回去了，jB 再也不会被轮询').toEqual(['jA', 'jB'])
      expect(useExportStore.getState().job?.job_id).toBe('jB')
      expect(useExportStore.getState().running).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})
