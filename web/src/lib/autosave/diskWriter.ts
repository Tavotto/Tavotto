/**
 * 磁盘写盘协调器：按文档排队、串行 PUT、乐观并发基线、外部修改基线、写前确认、
 * 队列排空的等待方——「一份文档怎样安全地落到磁盘上」这一整段，从
 * `store/documentStore.ts` 提出来（2026-09-18，审计任务书 PR E 的前端那一半）。
 *
 * **不 import store、不碰 `window` / `localStorage` / 遥测**：它需要向外面要的东西
 * 全部经 `DiskWriterPorts` 递进来（磁盘 API、「这份还是不是当前文档」、状态回写、
 * 本机副本清理、事件与遥测），所以能用假端口 + 手动 gate 逐条量它的时序，不起
 * store。`documentStore` 只负责装配：造一个实例，把 `useDocumentStore` 的读写
 * 包成回调递进来。正文（队列 / 基线 / 写前确认 / 冲突判定）从 store 逐字搬来。
 *
 * 判据的主语（改动前先读）：
 *   * **队列按 documentId**：同一个 id 天然合并成最新一份，不同 id 依次串行 PUT。
 *     单槽变量的话切文档时后来者会顶掉前一个文档排队的那份——那份连本机兜底副本
 *     都还在等写盘成功后才清，顶掉即永久丢失。
 *   * **项目 id 跟着载荷一起排队**：一次写入属于排队那一刻的项目，不属于「socket
 *     打开那一刻碰巧是哪个」。
 *   * **收到 409 基线一个都不推进**：推进等于承认对方那版是我的起点，下一次写就会
 *     把它盖掉。
 */
import { ApiError, REVISION_ABSENT, type DiskDocumentSummary } from '@/lib/api'
import type { ProjectDocument } from '@/types/document'

/**
 * 当前文档这一次保存走到了哪一步。
 *
 * ```text
 * clean ──编辑──▶ dirty ──flush──▶ saving ──成功且期间没再编辑──▶ saved ──▶ clean
 *                   ▲                 │                └──期间又编辑了──▶ dirty
 *                   │                 ├──写盘失败──▶ save_error ──重试──▶ saving
 *                   └────编辑─────────┴──409────────▶ conflict ──裁决──▶ dirty/clean
 * ```
 *
 * `saved` 是一个**短暂反馈态**，1.6 秒后自己回到 `clean`；它存在的理由是
 * 「刚刚存好了」和「一直是干净的」对用户是两件事。
 *
 * **「未决的恢复副本」与「那份文档读不了」不在这个枚举里**（Prompt 03 §二
 * 把 `recovery_available` / `read_only` 与它们并列）。理由是它们与保存进度
 * 是**两根互不相干的轴**，塞进同一个枚举两者就会互相顶掉：恢复副本还在本机
 * 躺着，一次成功的自动保存把状态推成 `saved`，横幅就没了，而那份副本一直到
 * 下次启动才再被想起来。它们走 `DocNotice`（在 store 里）。
 */
export type SaveState = 'clean' | 'dirty' | 'saving' | 'saved' | 'save_error' | 'conflict'

/** 保存卡住的原因；`saveState` 说卡在哪一步，它说卡的是什么 */
export interface SaveIssue {
  /**
   * - `io`：写盘本身失败（磁盘满、权限、后端不可达）
   * - `stale`：另一个标签页存了更新的版本（后端 `stale_write`）
   * - `external`：磁盘上那份不是我上次读到/写出的那份（后端 `external_change`）
   */
  kind: 'io' | 'stale' | 'external'
  docId: string
  /** 磁盘上那份的摘要（`external` / `stale`）；拿不到时为 null */
  disk?: DiskDocumentSummary | null
}

/**
 * 这个状态下**不许碰磁盘**：磁盘上那份不是我以为的那份，得先由用户裁决，
 * 否则每一次防抖自动保存都是一次静默覆盖的尝试。
 */
export const blocksDiskWrite = (state: SaveState) => state === 'conflict'

/**
 * 有没有「还没安全落盘」的工作？关闭保护、切文档提示都问它。
 *
 * `clean` / `saved` 是安全的：磁盘上就是内存里这份。**未决的恢复副本不在
 * 此列**——那份副本本身就在本机磁盘上，关掉窗口它还在，下次打开照样问。
 */
export const hasUnsavedWork = (state: SaveState): boolean =>
  state === 'dirty' || state === 'saving' || state === 'save_error' || state === 'conflict'

export const isStaleWrite = (err: unknown) =>
  err instanceof ApiError && err.status === 409 && err.body.code === 'stale_write'
export const isExternalChange = (err: unknown) =>
  err instanceof ApiError && err.status === 409 && err.body.code === 'external_change'

export type SaveTrigger = 'manual' | 'autosave'
export type SaveOutcome = 'ok' | 'conflict' | 'failed'

/** 协调器要用到的那几条磁盘 API（形状与 `@/lib/api` 同名函数一致，便于直接递真的进来）。 */
export interface DiskApi {
  put: (
    id: string,
    pd: ProjectDocument,
    baseline: number | undefined,
    revision: string | undefined,
    pj: string | null,
  ) => Promise<{ saved_at?: number; revision?: string | null }>
  fetch: (id: string, pj: string | null) => Promise<{ revision: string | null } | null>
  fetchSummary: (id: string, pj: string | null) => Promise<DiskDocumentSummary | null>
}

/** 协调器对外面（store / 页面）提的全部要求。 */
export interface DiskWriterPorts {
  api: DiskApi
  /** 这份还是不是当前文档（冲突挡住排队那份时只挡当前文档的） */
  isCurrent: (id: string) => boolean
  /**
   * 三个回调都按**写的那份文档的 id** 调，不看它是不是当前文档——迟到的结果
   * 不该去改别的文档的状态，这一层判断归端口（store 里按 `documentId` 比）。
   */
  /** 一次写盘真正开始（当前文档：状态推成 `saving`） */
  onSaving: (id: string) => void
  /** 写盘成功：期间又编辑过就还是 dirty，没编辑过才是"存好了" */
  onWritten: (id: string, savedAt: number) => void
  /** 写盘失败或被 409 挡下；`issue` 为 null = io 失败 */
  onFailed: (id: string, issue: SaveIssue | null) => void
  /** 冲突未决：队列里排着的那份不能继续往磁盘上撞（读的是**此刻**的保存状态） */
  writeBlocked: () => boolean
  /** 写盘成功后清掉本机兜底副本 */
  dropLocalCopy: (id: string) => void
  /** 遥测：一次写盘的结局 */
  outcome: (trigger: SaveTrigger, outcome: SaveOutcome) => void
}

/** 「先别写，这是个待裁决的冲突」——走 catch 那条路，不占用 ApiError 的语义 */
export class PendingConflict extends Error {
  issue: SaveIssue
  constructor(issue: SaveIssue) {
    super('pending_conflict')
    this.issue = issue
  }
}

export function conflictIssue(id: string, err: unknown): SaveIssue {
  const body = err instanceof ApiError ? err.body : {}
  const disk = (body.summary as DiskDocumentSummary | undefined) ?? null
  // 后端在 409 体里回了磁盘当下的 hash：显式覆盖拿它当基线，
  // 覆盖前如果又被改了一次，那次仍然会 409。
  const revision = typeof body.revision === 'string' ? body.revision : null
  return {
    kind: isExternalChange(err) ? 'external' : 'stale',
    docId: id,
    disk: disk ?? (revision ? ({ revision } as DiskDocumentSummary) : null),
  }
}

export interface DiskWriter {
  /** 把这一份排进队列（或立刻开始写） */
  schedule: (id: string, pd: ProjectDocument, pj: string | null) => void
  /** 队列排空（含在途那一次）后 resolve。已经空了就立刻 resolve。 */
  whenIdle: () => Promise<void>
  /** 下一次真正开始的写盘算作手动保存（遥测的 trigger） */
  markManual: () => void
  /** 手动保存没写成任何东西（空文档）时把标志放下 */
  clearManual: () => void
  /** 记下这次读到的磁盘状况；三档的含义见 `diskRevision` 上的表 */
  rememberRevision: (id: string, fetched: { revision: string | null } | null) => void
  /** 本会话确认过这份文档的磁盘状况吗（写前要不要先探一次） */
  knowsRevision: (id: string) => boolean
  setRevision: (id: string, revision: string | null) => void
  forgetRevision: (id: string) => void
  setBaseline: (id: string, updatedAt: number) => void
  forgetBaseline: (id: string) => void
  /** 队列里有没有东西 / 有没有在途的写（只给用例与调试看） */
  readonly busy: boolean
  readonly queued: number
}

export function createDiskWriter(ports: DiskWriterPorts): DiskWriter {
  let diskBusy = false
  /** 按 documentId 排队：同一个 id 天然合并成最新一份，不同 id 依次串行 PUT。 */
  const diskQueue = new Map<string, { pd: ProjectDocument; pj: string | null }>()

  /**
   * 乐观并发基线：本标签页最后一次**成功落盘**时那份文档的 updatedAt。
   * 读档时按磁盘上的值初始化，每次 PUT 成功后推进。PUT 带上它，后端发现磁盘
   * 更新（另一个标签页存过）就回 409 stale_write，不整份覆盖。
   *
   * 没有基线（首次写、从没读过盘）时不带，后端也就不校验——那时磁盘上本来就
   * 没有别人的东西可覆盖。收到 409 后基线**故意不推进**：本窗口后续的写盘会
   * 继续 409，而不是转头把对方的版本盖掉。
   */
  const diskBaseline = new Map<string, number>()

  /**
   * 外部修改基线（R-08）。三种取值**含义各不相同，不许合并**：
   *
   * | 值 | 含义 | 写入时带什么 |
   * | --- | --- | --- |
   * | 内容 hash | 我最后读到/写出的就是这一份 | 那个 hash |
   * | `REVISION_ABSENT` | 我读过，磁盘上没有这份文件 | `absent` 哨兵 |
   * | `null` | 我读过，但**拿不到**内容 hash（旧后端、代理吃了响应头） | 什么都不带 |
   *
   * **条目缺席**是第四种，与上面三种都不同：本会话从没确认过这份文档的磁盘
   * 状况，写之前得先去问一次（`ensureDiskKnown`）。
   *
   * `null` 这一档不是多余的：把它并进「缺席」，`ensureDiskKnown` 每次都会去探，
   * 探到一份「我从没读过的文档」（其实读过，只是没 hash）→ 判成冲突 → 这份文档
   * **永远存不上**；把它并进 `REVISION_ABSENT`，一个明明存在的文件被说成不存在
   * → 后端 409 → 同样永远存不上。两条捷径都通向同一个死结。
   *
   * 与 `diskBaseline` 是**两个维度**而不是同一件事的两种精度：updatedAt 由
   * 文档自己声明，编辑器外的工具改完 `tavottofile/*.json` 往往一动不动；
   * 内容 hash 由字节决定，谁改都瞒不过。所以带得了 hash 就以 hash 为准，
   * 带不了就退回 updatedAt 那条判据——弱一档，但不会把用户锁死。
   */
  const diskRevision = new Map<string, string | null>()

  /** 落盘队列空了就通知等待方（saveNow 靠它知道"这次真的写完了"） */
  const idleWaiters: (() => void)[] = []

  /*  「这次写是手动还是自动」在 `saveNow()` 里知道、在 `schedule()` 里用：手动保存
   *  先把标志举起来，下一次真正开始的写盘把它消费掉。写盘排队时标志会落到排队后
   *  开始的那一次上——粗，但它就是那次手动保存推出去的内容。 */
  let manualSavePending = false

  function settleIdle(): void {
    if (diskBusy || diskQueue.size) return
    for (const w of idleWaiters.splice(0)) w()
  }

  function whenIdle(): Promise<void> {
    if (!diskBusy && !diskQueue.size) return Promise.resolve()
    return new Promise<void>((resolve) => idleWaiters.push(resolve))
  }

  function rememberRevision(id: string, fetched: { revision: string | null } | null): void {
    if (!fetched) diskRevision.set(id, REVISION_ABSENT)
    else diskRevision.set(id, fetched.revision || null)
  }

  /**
   * 写之前确认磁盘状况**确实是我以为的那样**。
   *
   * 触发条件是「`diskRevision` 里没有这个 id 的条目」——也就是本会话从没
   * 确认过它的磁盘状况。这**没有例外**：新建文档、载入画布文件、读盘那次
   * 抛了异常、以及应用启动时那份还没被切换过的初始文档，全都落在这条上。
   * 写一条「只有读盘失败时才确认」的规则更省一次 GET，但那三种情况一样是
   * 手里两个基线都没有，而不带基线的 PUT 后端一律放行——磁盘上要是有一份
   * 我从没读过的文档，这一次 PUT 就把它整份盖掉了。判据留了例外就会从例外
   * 那一侧漏。
   *
   * 代价是每份文档第一次落盘前多一个 GET，之后一次都不多。
   */
  async function ensureDiskKnown(id: string, pj: string | null): Promise<SaveIssue | null> {
    if (diskRevision.has(id)) return null
    let probe: Awaited<ReturnType<DiskApi['fetch']>>
    try {
      probe = await ports.api.fetch(id, pj)
    } catch {
      // 还是问不到：不猜，也不记。这次照常尝试写（后端多半同样不可达，
      // 那就走 save_error），下一次写之前还会再确认一遍。
      return null
    }
    rememberRevision(id, probe)
    if (!probe) return null
    // 磁盘上有一份我从没读过的：这就是冲突，不是「首次写」
    return {
      kind: 'external',
      docId: id,
      disk: await ports.api.fetchSummary(id, pj),
    }
  }

  function schedule(id: string, pd: ProjectDocument, pj: string | null): void {
    if (diskBusy) {
      diskQueue.set(id, { pd, pj })
      return
    }
    diskBusy = true
    const trigger: SaveTrigger = manualSavePending ? 'manual' : 'autosave'
    manualSavePending = false
    ports.onSaving(id)
    void ensureDiskKnown(id, pj)
      .then((issue) => {
        if (issue) throw new PendingConflict(issue)
        // `null`（确认过但拿不到 hash）与「缺席」在这里都变成 undefined = 不带，
        // 但两者在 `ensureDiskKnown` 那里是两回事：前者不再探，后者要探。
        return ports.api.put(id, pd, diskBaseline.get(id), diskRevision.get(id) ?? undefined, pj)
      })
      .then((res) => {
        diskBaseline.set(id, pd.updatedAt)
        if (res.revision) diskRevision.set(id, res.revision)
        ports.dropLocalCopy(id)
        ports.onWritten(id, res.saved_at ?? Date.now())
        ports.outcome(trigger, 'ok')
      })
      .catch((err: unknown) => {
        // 磁盘写失败（含被 409 挡下的过期写）：本机副本仍在（flush 时已写，
        // 这里绝不清）。**基线一个都不推进**——推进等于承认对方那版是我的起点，
        // 下一次写就会把它盖掉。
        const conflict =
          err instanceof PendingConflict
            ? err.issue
            : isStaleWrite(err) || isExternalChange(err)
              ? conflictIssue(id, err)
              : null
        ports.outcome(trigger, conflict ? 'conflict' : 'failed')
        ports.onFailed(id, conflict)
      })
      .finally(() => {
        diskBusy = false
        // 先出队再递归，队列里不会留下已经在写的那一份（不然同一 id 自己排自己）
        const next = diskQueue.entries().next()
        if (!next.done) {
          const [qid, queued] = next.value
          diskQueue.delete(qid)
          // 冲突挡住之后，队列里排着的那份不能继续往磁盘上撞：它的内容已经
          // 在本机副本里，等用户裁决完再写。
          if (ports.writeBlocked() && ports.isCurrent(qid)) {
            settleIdle()
            return
          }
          schedule(qid, queued.pd, queued.pj)
          return
        }
        settleIdle()
      })
  }

  return {
    schedule,
    whenIdle,
    markManual: () => {
      manualSavePending = true
    },
    clearManual: () => {
      manualSavePending = false
    },
    rememberRevision,
    knowsRevision: (id) => diskRevision.has(id),
    setRevision: (id, revision) => {
      diskRevision.set(id, revision)
    },
    forgetRevision: (id) => {
      diskRevision.delete(id)
    },
    setBaseline: (id, updatedAt) => {
      diskBaseline.set(id, updatedAt)
    },
    forgetBaseline: (id) => {
      diskBaseline.delete(id)
    },
    get busy() {
      return diskBusy
    },
    get queued() {
      return diskQueue.size
    },
  }
}
