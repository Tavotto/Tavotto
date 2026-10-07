/**
 * 导入即扫描（T02）的**前端持有者**：把后端的扫描快照取回来、跟着它的进度轮询、记住「用户关了条」。
 *
 * ### 它做什么、不做什么
 *
 * * 项目认领完成 / 启动恢复时由 `projectStore` 调一次 `start()`（后端单飞 + 新鲜复用，重复认领是便宜的）；
 * * **不判任何事实**：phase / outcome / 目标选择 / 每项检查的状态都是后端 `engine/projscan.py` 给的，
 *   这里没有第二份「能不能运行」「有没有脚本」的判据（`web/AGENTS.md`：事实在后端，前端只翻译不判断）；
 * * 业务事实来自快照，**开合 / 焦点**用既有的 `uiStore`（`scanPanelOpen`），不另起一个全局开关；
 * * **不碰教程**：教程的「已完成」在 `onboardingStore`，本 store 的 `dismissedScanId` 只是「这一轮扫描的条
 *   被关掉了」，下一轮扫描（新的 `scan_id`）自然会再提示，两者互不相干。
 *
 * ### 迟到响应
 *
 * 与 `projectReadinessStore` 同一条纪律：请求序号（只认最后发出的那次）+ 发请求那一刻的项目 id +
 * 项目换代（`clear()` 清掉在途请求的资格）。再加一条扫描特有的：同一个 `scan_id` 内快照的
 * `observation_seq` 只许前进，倒退的是被重排的旧响应。
 *
 * ### 三个不同的动作
 *
 * `hide()`（关掉条，只改呈现）· `cancel()`（取消**扫描**）· 切项目（`clear()`，前端丢弃订阅，后端那一笔
 * 账由项目关闭时处理）。没有任何一个会碰执行 / 安装 / worker——扫描根本没有这些 owner。
 */
import { create } from 'zustand'
import {
  ApiError,
  cancelProjectScan,
  fetchProjectScan,
  startProjectScan,
  type ProjectScan,
} from '@/lib/api'
import { currentProjectId } from '@/lib/session'

/** 扫描跑了多久才值得显示「正在检查」：更短的（静态小项目）不闪一下条再收回去 */
export const SLOW_SCAN_MS = 500
/** 运行中补拉的节奏（SSE 提示之外的兜底） */
const POLL_MS = [400, 800, 1500] as const

export type ScanStartReason = 'claim' | 'restore' | 'manual' | 'refresh'

interface ProjectScanState {
  /** 最后一次成功取回的快照。后台补拉失败不清它——旧事实好过没有事实 */
  scan: ProjectScan | null
  error: string | null
  /** 被「关闭」的那一轮扫描的 `scan_id`；只隐藏，不取消 */
  dismissedScanId: string | null
  /** 用户主动「重新打开」：即使没有需要关注的事也显示 */
  forced: boolean
  /** 这一轮扫描已经跑了超过 `SLOW_SCAN_MS` */
  slow: boolean

  start: (opts?: { force?: boolean; reason?: ScanStartReason }) => Promise<ProjectScan | null>
  /** 补拉（SSE 提示 / 轮询）：只读，不会开始扫描 */
  refresh: () => Promise<ProjectScan | null>
  /** 取消扫描（只此一件事） */
  cancel: () => Promise<void>
  hide: () => void
  reopen: () => void
  /** 换项目：属于旧项目的一切原地丢掉，在途响应失去落地资格 */
  clear: () => void
}

let seq = 0
let applied = 0
/** 已落地的最晚发出的**权威**请求序号：权威回包彼此之间按发出顺序比较（与 GET 的 `applied` 分开记） */
let appliedAuthoritative = 0
let generation = 0
let pollTimer: ReturnType<typeof setTimeout> | null = null
let slowTimer: ReturnType<typeof setTimeout> | null = null
let pollStep = 0
let startInflight: { pj: string | null; promise: Promise<ProjectScan | null> } | null = null

function stopTimers(): void {
  if (pollTimer) clearTimeout(pollTimer)
  if (slowTimer) clearTimeout(slowTimer)
  pollTimer = null
  slowTimer = null
  pollStep = 0
}

export const useProjectScanStore = create<ProjectScanState>((set, get) => {
  /**
   * 一次响应能不能落地。项目 / 换代守卫对所有响应生效；请求序号只约束**非权威**的补拉
   * （GET）。`authoritative`（start / cancel 的 POST 回包）是「此刻谁在跑」的最新事实：若有更晚发出的
   * GET 先带着上一轮终局回来，序号比较会把这次新扫描的回包丢掉、而终局快照又不会再触发轮询。
   */
  const accept = (
    mine: number,
    pj: string | null,
    gen: number,
    data: ProjectScan,
    authoritative: boolean,
  ): boolean => {
    if (gen !== generation || pj !== currentProjectId()) return false
    if (!authoritative && mine < applied) return false
    // 权威回包不受 GET 序号约束，但彼此之间仍按发出顺序：较早发出的 POST（如缓存终局）
    // 晚到，不得覆盖已落地的更晚发出的 POST（如 force 重扫的新一轮）
    if (authoritative && mine < appliedAuthoritative) return false
    const cur = get().scan
    // 同一轮扫描内 observation_seq 只许前进——对权威回包同样成立（后端序号单调）
    if (cur && cur.scan_id === data.scan_id) {
      if (data.observation_seq < cur.observation_seq) return false
    }
    applied = Math.max(applied, mine)
    if (authoritative) appliedAuthoritative = Math.max(appliedAuthoritative, mine)
    return true
  }

  const land = (data: ProjectScan) => {
    const prev = get().scan
    const sameRound = prev?.scan_id === data.scan_id
    set({
      scan: data,
      error: null,
      // 新的一轮扫描（新的 scan_id）自然会再提示；同一轮里关掉的就一直关着
      ...(sameRound ? {} : { slow: false }),
    })
    if (data.state === 'running') {
      if (!slowTimer && !get().slow) {
        const gen = generation
        slowTimer = setTimeout(() => {
          slowTimer = null
          if (gen === generation && get().scan?.state === 'running') set({ slow: true })
        }, SLOW_SCAN_MS)
      }
      schedulePoll()
    } else {
      if (pollTimer) clearTimeout(pollTimer)
      if (slowTimer) clearTimeout(slowTimer)
      pollTimer = null
      slowTimer = null
      pollStep = 0
      if (get().slow) set({ slow: false })
    }
  }

  const schedulePoll = () => {
    if (pollTimer) return
    const gen = generation
    const wait = POLL_MS[Math.min(pollStep, POLL_MS.length - 1)]
    pollStep += 1
    pollTimer = setTimeout(() => {
      pollTimer = null
      if (gen !== generation) return
      void get().refresh()
    }, wait)
  }

  const request = (
    call: () => Promise<ProjectScan>,
    authoritative: boolean,
  ): Promise<ProjectScan | null> => {
    const mine = ++seq
    const pj = currentProjectId()
    const gen = generation
    return call()
      .then((data) => {
        if (!accept(mine, pj, gen, data, authoritative)) return null
        land(data)
        return data
      })
      .catch((err: unknown) => {
        if (gen !== generation || pj !== currentProjectId() || mine < applied) return null
        if (err instanceof ApiError && err.status === 404) {
          // 后端不记得这个扫描（重启 / 项目被重开）：丢掉旧快照，等下一次 start()
          set({ scan: null, error: null })
          return null
        }
        set({ error: err instanceof Error ? err.message : String(err) })
        return null
      })
  }

  return {
    scan: null,
    error: null,
    dismissedScanId: null,
    forced: false,
    slow: false,

    start: (opts) => {
      const pj = currentProjectId()
      if (!opts?.force && startInflight && startInflight.pj === pj) return startInflight.promise
      if (opts?.force) {
        // 重新检查 = 新的一轮：之前关掉的条应当回来
        set({ dismissedScanId: null, forced: false })
      }
      const promise = request(
        () => startProjectScan({ force: opts?.force === true, reason: opts?.reason ?? 'claim' }),
        true,
      ).finally(() => {
        if (startInflight?.promise === promise) startInflight = null
      })
      if (!opts?.force) startInflight = { pj, promise }
      return promise
    },

    refresh: () => request(fetchProjectScan, false),

    cancel: async () => {
      const mine = ++seq
      const pj = currentProjectId()
      const gen = generation
      try {
        const data = await cancelProjectScan()
        if (accept(mine, pj, gen, data, true)) land(data)
      } catch {
        /* 取消赛不过扫描结束不是错误：下一次补拉会拿到终局 */
      }
      void get().refresh()
    },

    hide: () => {
      const scan = get().scan
      set({ dismissedScanId: scan?.scan_id ?? null, forced: false })
    },

    reopen: () => set({ dismissedScanId: null, forced: true }),

    clear: () => {
      generation += 1
      stopTimers()
      startInflight = null
      set({ scan: null, error: null, dismissedScanId: null, forced: false, slow: false })
    },
  }
})

/** 只给测试用：把模块级账本清零（它们活得比一次 `setState()` 长）。 */
export function resetProjectScanBookkeeping(): void {
  seq = 0
  applied = 0
  appliedAuthoritative = 0
  generation += 1
  stopTimers()
  startInflight = null
}
