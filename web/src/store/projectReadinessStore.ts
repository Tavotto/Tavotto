/**
 * 项目接入就绪度的**前端持有者**（Prompt 08）。
 *
 * 它只做三件事：把后端算好的那份事实取回来、记住"用户已经看过哪一版"、
 * 记住"接入中心此刻聚焦在哪张图"。
 *
 * **不判状态。** 六个状态与十个 reason code 的唯一出处是后端
 * `engine/readiness.py`（Session 07 的 T-31）。这里连一个 `!!script` 都不许
 * 有——改造前正是三个界面各判一遍，同一张图得到三个互相矛盾的答案。
 *
 * **不存翻译后的字符串。** 存的是 status / reason_code / fingerprint 这些
 * 枚举与结构化值；句子由组件在渲染那一刻按当前语言查出来。
 *
 * 开合标志**不在这里**：所有对话框的开关都住在 `uiStore`
 * （`registryOpen`），再加一个布尔值等于给同一件事两个出处。这里只管
 * 「聚焦哪一张」，`focusPanel()` 顺手把那个既有标志打开。
 */
import { create } from 'zustand'
import { fetchReadiness, type ReadinessReport } from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { useUiStore } from './uiStore'

/** 已关闭的横幅：项目 id → 那一版报告的 fingerprint */
const DISMISS_KEY = 'tavotto.readinessDismissed'
/** 本机只留最近这么多个项目的关闭记录；再多就是无用的历史 */
const DISMISS_MAX = 20

/* -------------------------------------------------------------------------- */
/*  并发治理（与 assetStore 同一条纪律，理由见那边的长注释）                     */
/*                                                                            */
/*  就绪度的触发点：项目打开、切项目、统一刷新之后的那一批事件、接入中心里的     */
/*  每一次动作。「同一批事件里被调好几次」同样是常态。                          */
/*                                                                            */
/*   1. 旧响应不覆盖新的 —— 判据是请求序号；                                   */
/*   2. 不落进另一个项目 —— 判据是「发请求那一刻本标签页认领的是哪个项目」。     */
/* -------------------------------------------------------------------------- */
let seq = 0
let applied = 0
let inflight: { pj: string | null; promise: Promise<ReadinessReport | null> } | null = null

function readDismissed(): Record<string, string> {
  try {
    const raw = localStorage.getItem(DISMISS_KEY)
    const v = raw ? (JSON.parse(raw) as unknown) : null
    if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
    // 逐条校验：手工改过 / 半截写入的 blob 里混着非字符串时，只丢掉坏的那几条
    return Object.fromEntries(
      Object.entries(v as Record<string, unknown>).filter(
        ([k, val]) => typeof k === 'string' && typeof val === 'string',
      ),
    ) as Record<string, string>
  } catch {
    // 读不回来 = 谁都没关过。**不是错误**：横幅多显示一次远好过因为一个坏
    // blob 就再也不提示
    return {}
  }
}

function writeDismissed(map: Record<string, string>): void {
  try {
    const entries = Object.entries(map)
    const kept = entries.length > DISMISS_MAX ? entries.slice(-DISMISS_MAX) : entries
    localStorage.setItem(DISMISS_KEY, JSON.stringify(Object.fromEntries(kept)))
  } catch {
    /* 存不下就只在本次会话里有效 */
  }
}

interface ReadinessState {
  /** 最后一次**成功**取回的报告。后台失败不清它——旧事实好过没有事实 */
  report: ReadinessReport | null
  loading: boolean
  /** 最近一次失败的原文；成功一次就清掉 */
  error: string | null
  /** 接入中心此刻要滚到 / 高亮哪一张图（`ReadinessPanel.id`）；null = 不聚焦 */
  focusId: string | null
  /** 当前项目已经关掉的那一版 fingerprint；null = 没关过 */
  dismissed: string | null

  load: (opts?: { force?: boolean }) => Promise<ReadinessReport | null>
  /** 事件驱动的刷新入口：与同一批里的其它调用合并成一次请求 */
  refresh: () => Promise<ReadinessReport | null>
  /**
   * 打开接入中心（可带聚焦目标）。
   *
   * **关闭后的焦点归位不在这里**：`ui/Dialog` 已经在 `onOpenAutoFocus` 里记下
   * 打开前的焦点、在 `onCloseAutoFocus` 里还回去（还带着「节点被重渲染换掉」
   * 的兜底）。这里再记一份的话，同一条保证有两个实现，删掉任意一个都还有
   * 另一个兜着——那正是 T-36 里变异永远杀不死的形状。
   */
  openCenter: (opts?: { focus?: string | null }) => void
  /** 「为什么不能编辑？」的落点：打开中心并聚焦到这张图 */
  focusPanel: (id: string) => void
  /** 滚动与高亮已经做完了，清掉聚焦标记（否则重开还会再高亮一次） */
  clearFocus: () => void
  closeCenter: () => void
  /** 关掉横幅：按 `项目 id + 当前 fingerprint` 记，事实一变就会再出现 */
  dismissBanner: () => void
  /** 换项目：属于旧项目的一切原地丢掉 */
  clear: () => void
}

export const useProjectReadinessStore = create<ReadinessState>((set, get) => ({
  report: null,
  loading: false,
  error: null,
  focusId: null,
  dismissed: null,

  load: (opts) => {
    if (!opts?.force && inflight && inflight.pj === currentProjectId()) return inflight.promise

    const mine = ++seq
    const pj = currentProjectId()
    set({ loading: true })
    const promise = fetchReadiness()
      .then((data) => {
        if (pj !== currentProjectId()) return null
        if (mine < applied) return null
        applied = mine

        const prev = get().report
        // fingerprint 没变 = 同一份事实。**报告对象一个字节都不换**：换了
        // 引用，订阅它的每个组件都会重渲染一遍，而屏幕上不会有任何变化。
        // （错误状态仍要清：那一位说的是"上一次取回来了没有"。）
        //
        // 这里**不再比一次 `project_id`**：它就在被哈希的那份 body 里
        // （`readiness.fingerprint()` 的定义是"报告的内容哈希"），两个项目
        // 给不出同一个 fingerprint；换项目那条路又必然先 `clear()`。多加一句
        // 等于同一条保证有两个实现，而删掉任意一个都不会有用例红。
        if (prev && prev.fingerprint === data.fingerprint) {
          if (get().error !== null) set({ error: null })
          return prev
        }
        set({
          report: data,
          error: null,
          // 关闭记录按项目存，切回来仍然作数；换了 fingerprint 自然不再匹配
          dismissed: readDismissed()[data.project_id] ?? null,
        })
        return data
      })
      .catch((err: unknown) => {
        if (pj !== currentProjectId() || mine < applied) return null
        // **report 不清**：后台刷新失败时清空等于让横幅与接入中心当场空掉，
        // 而磁盘上的事实一个字都没变。首次失败时 report 本来就是 null，
        // 界面照旧显示错误态。
        set({ error: err instanceof Error ? err.message : String(err) })
        return null
      })
      .finally(() => {
        if (inflight?.promise === promise) inflight = null
        if (mine === seq) set({ loading: false })
      })

    if (!opts?.force) inflight = { pj, promise }
    return promise
  },

  refresh: () => get().load(),

  openCenter: (opts) => {
    set({ focusId: opts?.focus ?? null })
    useUiStore.getState().setRegistryOpen(true)
    // 打开的那一刻取一次：入口可能是从没加载过就绪度的地方（项目菜单、设置页）
    void get().load()
  },

  focusPanel: (id) => get().openCenter({ focus: id }),

  clearFocus: () => {
    if (get().focusId !== null) set({ focusId: null })
  },

  closeCenter: () => {
    useUiStore.getState().setRegistryOpen(false)
    set({ focusId: null })
  },

  dismissBanner: () => {
    const report = get().report
    if (!report) return
    const map = readDismissed()
    map[report.project_id] = report.fingerprint
    writeDismissed(map)
    set({ dismissed: report.fingerprint })
  },

  clear: () => {
    // 在途响应由 `pj` 判据挡住，这里只清已经落地的
    set({ report: null, error: null, loading: false, focusId: null, dismissed: null })
  },
}))

/**
 * 横幅该不该出现，以及它要显示哪一份报告。
 *
 * 五个条件全部成立才给：报告到了、项目里有图、有任何一张不是 `editable`、
 * 这一版还没被关掉。全部 editable 的项目**没有横幅**——那时它只是噪音。
 */
export function bannerReport(s: {
  report: ReadinessReport | null
  dismissed: string | null
}): ReadinessReport | null {
  const r = s.report
  if (!r) return null
  if (r.summary.total <= 0) return null
  if (r.summary.editable >= r.summary.total) return null
  if (s.dismissed === r.fingerprint) return null
  return r
}

/**
 * 只给测试用：把模块级的并发账本清零。理由与 `resetAssetLoadBookkeeping`
 * 逐字相同——它们活得比一次 `setState()` 长。
 */
export function resetReadinessBookkeeping(): void {
  seq = 0
  applied = 0
  inflight = null
}
