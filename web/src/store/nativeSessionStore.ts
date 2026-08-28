import { create } from 'zustand'
import {
  ApiError,
  approveNativePending,
  buildNativeSession,
  cancelNativePending,
  continueNativeSession,
  detachNativeSession,
  fetchNativePending,
  fetchNativeSessions,
  isNativeTerminal,
  terminateNativeSession,
  type CapturedFigureDescriptor,
  type NativePending,
  type NativeSessionInfo,
} from '@/lib/api'
import { addRuntimePanel } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'

/**
 * `tavotto run` 的桌面产品面（ADR 0021 §5 / §7 / §9）。
 *
 * 这个 store 端着两件互不相同的东西：
 *
 * 1. **待确认的交接队列**（`pendingQueue`）。CLI 已经起了 relay、写了
 *    descriptor，此刻正阻塞在「Waiting for Tavotto desktop…」上，而**用户的
 *    Python 一行都还没跑**——确认之后 sidecar 才 attach，attach 成功才是 CLI
 *    「可以开跑了」的信号。所以这一屏不是提示，是**闸**。
 *
 *    队列而不是单个：两个终端各跑一条 `tavotto run` 是后端明确支持的形态
 *    （`native_asset_conflict` 的文案就是为它写的）。留一个丢一个的话，被丢
 *    掉的那个终端只会一直挂着，直到 attach 超时，而界面上什么都没发生过。
 *
 * 2. **活着的会话**（`sessions`）。状态闭集十档由后端说了算，前端一档都不
 *    自己推——尤其是 `editable`：它等价于「停在屏障上」，而那是对端报上来的
 *    事实，不是这边按 state 猜出来的。
 *
 * 四条纪律（每条都有用例）：
 *
 * - **迟到的事件按 `sequence` 判**，不按到达顺序。SSE 断线重连之后会补发，
 *   而 EventSource 不保证补发与新事件的相对次序；照单全收的表现是脚本已经
 *   退出了、卡片却又变回「正在运行」。终态不回头是它的推论，另有一条用例。
 * - **项目代际**（`epoch`）：切项目时在途响应一律作废，绝不落进新项目
 *   （与 scriptRunStore / runtimeAssetStore 同一条）。
 * - **每条会话上的动作互斥**（`busy`）：continue / detach / terminate /
 *   build 共用一把闸。连点两次「继续运行脚本」在单 reader 传输上就是两条
 *   排队的请求，第二条的响应没有人等。
 * - **错误存 code + params，不存成品字符串**：这些卡片活得比一次渲染长，
 *   中途切语言时存好的字符串再也换不回来（与 scriptRunStore 同一条）。
 */

/** 后端的结构化错误：稳定 code + params；`message` 只是回退原文。 */
export interface NativeError {
  code: string
  message: string
  params?: Record<string, unknown>
}

const toNativeError = (e: unknown): NativeError => {
  const api = e instanceof ApiError ? e : null
  const body = (api?.body ?? {}) as { code?: string; params?: Record<string, unknown> }
  return {
    code: body.code ?? 'internal_error',
    message: e instanceof Error ? e.message : String(e),
    params: body.params,
  }
}

export interface NativePendingState {
  native_id: string
  info: NativePending | null
  loading: boolean
  /** 已按下「运行并连接」/「取消」，等后端落地 */
  submitting: boolean
  error: NativeError | null
}

interface NativeSessionStore {
  /** 项目代际：`clear()` 递增，在途响应据此作废 */
  epoch: number
  /** 待确认队列；`[0]` 是此刻显示的那一条 */
  pendingQueue: NativePendingState[]
  sessions: Record<string, NativeSessionInfo>
  /** 会话上的动作互斥闸 */
  busy: Record<string, boolean>
  /** 每条会话上最后一次动作/绑定的错误（如实显示，不吞） */
  errors: Record<string, NativeError | null>
  /** 已被另一条活会话占着的 stem（**报出来，不静默抢过来**，ADR 0021 §9.2） */
  conflicts: Record<string, string[]>
  /** 已经为哪个 `sequence` build 过——同一个屏障不重复 build */
  builtSeq: Record<string, number>

  /** 收到一条交接 ID（落地 URL 的 `?native=` / `tavotto:open` 事件） */
  receive: (nativeId: string) => Promise<void>
  /**
   * 批准**指定的那一条**。
   *
   * **`nativeId` 是必填的，这是刻意的。** 上一版按队首批准，而"记住过所以
   * 自动批准"这条判据的主语是**刚取回来的那一条**——两个主语在队列里不止
   * 一条时就分开了：A（没记住、正等用户确认）在队首，B（记住过）后到，
   * 于是**批准的是 A**，而 A 从来没被确认过。那一刻 `tavotto run` 最核心
   * 的那句承诺（"你确认之前一行代码都不会跑"）当场失效。
   */
  approve: (nativeId: string, remember: boolean) => Promise<void>
  /** 取消**指定的那一条**——CLI 当场收摊并退出 3 */
  cancel: (nativeId: string) => Promise<void>
  /** 取不到 / 已过期的那一条：从队列里去掉，不留一个转不动的对话框 */
  dismissPending: () => void

  /** SSE `native.session`：按 sequence 判后落地 */
  applyEvent: (session: NativeSessionInfo) => void
  /** 在屏障处取一次 stems / descriptors，并把图接进画布 */
  build: (sessionId: string) => Promise<void>
  resume: (sessionId: string) => Promise<void>
  detach: (sessionId: string) => Promise<void>
  terminate: (sessionId: string) => Promise<void>
  /** 重新拉一次清单（重开界面 / SSE 重连之后的对账） */
  refresh: (projectRoot?: string) => Promise<void>
  /** 收起一条终态会话的卡片 */
  dismiss: (sessionId: string) => void
  clear: () => void
}

/**
 * 一个面板与 `tavotto run` 会话的关系（ADR 0021 §9）——面板角标的判据。
 *
 * | 情况 | 结果 | 为什么 |
 * |---|---|---|
 * | 活会话拥有它，且停在屏障上 | `null` | 此刻编辑一切正常，不打扰 |
 * | 活会话拥有它，脚本正在跑 | `'running'` | 现在编辑会撞 409，先说 |
 * | 出自 native，但没有活会话 | `'offline'` | cache 里那张是 last-known preview |
 * | 不是 native | `null` | |
 *
 * **按描述符里的 asset id 认领，不按 stem 猜**：同名 stem（`Fig1`）在两个项目
 * 里到处都是，而 asset id 是后端算出来的那一个。
 *
 * `profile` 未知（老后端不给这个字段）时按 `safe` 走——**未知不等于 native**，
 * 把未知当 native 会给一个普通面板挂上「会话已结束」。
 */
export function nativePanelState(
  sessions: Record<string, NativeSessionInfo>,
  fileId: string,
  profile: 'safe' | 'native' | undefined,
): 'running' | 'offline' | null {
  const live = Object.values(sessions).find(
    (one) =>
      !isNativeTerminal(one.state) && one.descriptors.some((d) => d.asset_id === fileId),
  )
  if (live) return live.editable ? null : 'running'
  return profile === 'native' ? 'offline' : null
}

/** 卡片按开始时间排；活着的排在前面（用户此刻要动的是那些）。 */
export const sortSessions = (all: NativeSessionInfo[]): NativeSessionInfo[] =>
  [...all].sort((a, b) => {
    const at = isNativeTerminal(a.state) ? 1 : 0
    const bt = isNativeTerminal(b.state) ? 1 : 0
    return at !== bt ? at - bt : b.started_at - a.started_at
  })

export const useNativeSessionStore = create<NativeSessionStore>((set, get) => ({
  epoch: 0,
  pendingQueue: [],
  sessions: {},
  busy: {},
  errors: {},
  conflicts: {},
  builtSeq: {},

  receive: async (nativeId) => {
    if (!nativeId) return
    // 同一条 ID 来两次（首启 URL + 单实例事件都送到了）不排两遍队
    if (get().pendingQueue.some((p) => p.native_id === nativeId)) return
    set((s) => ({
      pendingQueue: [
        ...s.pendingQueue,
        { native_id: nativeId, info: null, loading: true, submitting: false, error: null },
      ],
    }))
    // **不按项目代际作废**：待确认的交接不属于任何一个界面项目（见 `clear()`）。
    // 该作废它的只有一件事——它已经不在队列里了（用户关掉 / 取消 / 批准过）。
    const patch = (next: Partial<NativePendingState>) => {
      set((s) => ({
        pendingQueue: s.pendingQueue.map((p) =>
          p.native_id === nativeId ? { ...p, ...next } : p,
        ),
      }))
    }
    try {
      const res = await fetchNativePending(nativeId)
      patch({ info: res.pending, loading: false, error: null })
      // **记住过就不再问**（ADR 0021 §7.1）：许可绑的是项目 × 解释器 ×
      // schema，解释器换了 / 项目搬了 / schema 升了都会让它失效并重新问。
      // 这不是「允许 AI 自动执行」——用户仍然是自己在终端里敲的那条命令。
      if (res.pending.remembered) await get().approve(nativeId, false)
    } catch (e) {
      // 过期 / 已被处理 / ID 不对：如实说，不留一个转圈的对话框
      patch({ loading: false, error: toNativeError(e) })
    }
  },

  approve: async (id, remember) => {
    const one = get().pendingQueue.find((p) => p.native_id === id)
    if (!one || one.submitting || !one.info) return
    set((s) => ({
      pendingQueue: s.pendingQueue.map((p) =>
        p.native_id === id ? { ...p, submitting: true, error: null } : p,
      ),
    }))
    try {
      const res = await approveNativePending(id, remember)
      set((s) => ({
        pendingQueue: s.pendingQueue.filter((p) => p.native_id !== id),
        sessions: { ...s.sessions, [res.session.session_id]: res.session },
      }))
    } catch (e) {
      // 连不上 / 环境被占 / 已被处理：**留在队列里**并显示原因。
      // 悄悄关掉对话框等于让那个终端继续挂着而用户不知道为什么。
      set((s) => ({
        pendingQueue: s.pendingQueue.map((p) =>
          p.native_id === id ? { ...p, submitting: false, error: toNativeError(e) } : p,
        ),
      }))
    }
  },

  cancel: async (id) => {
    const one = get().pendingQueue.find((p) => p.native_id === id)
    if (!one || one.submitting) return
    set((s) => ({
      pendingQueue: s.pendingQueue.map((p) =>
        p.native_id === id ? { ...p, submitting: true } : p,
      ),
    }))
    // 取消请求本身失败也照样出队：descriptor 有 TTL，CLI 那边最迟在
    // attach 超时时收摊，而把一个点过取消的对话框留在屏幕上更坏。
    try {
      await cancelNativePending(id)
    } catch {
      /* 见上 */
    }
    set((s) => ({ pendingQueue: s.pendingQueue.filter((p) => p.native_id !== id) }))
  },

  dismissPending: () =>
    set((s) => ({ pendingQueue: s.pendingQueue.slice(1) })),

  applyEvent: (session) => {
    const prev = get().sessions[session.session_id]
    // **按 sequence 判，不按到达顺序。** 断线重连时补发的旧事件与新事件之
    // 间没有次序保证；照单全收的表现是脚本已经退出了、卡片又变回"正在
    // 运行"。终态不回头是这条的推论（后端进终态之后 sequence 只会更大）。
    if (prev && session.sequence <= prev.sequence) return
    set((s) => ({ sessions: { ...s.sessions, [session.session_id]: session } }))
    // 屏障是"可以编辑了"那一刻，图要在这时候进画布。**由界面显式发 build**
    // ——后端不在 reader 线程里自己发，那是自己等自己（ADR 0021 §5.2）。
    if (session.state === 'barrier' && get().builtSeq[session.session_id] !== session.sequence) {
      void get().build(session.session_id)
    }
  },

  build: async (sessionId) => {
    const session = get().sessions[sessionId]
    if (!session || get().busy[sessionId]) return
    // **只在屏障处**：别处 build 后端会回 `native_session_not_at_barrier`，
    // 而那是一条会吓到用户的错误——它描述的是正常状态，不是故障。
    if (session.state !== 'barrier') return
    const epoch = get().epoch
    const seq = session.sequence
    set((s) => ({
      busy: { ...s.busy, [sessionId]: true },
      builtSeq: { ...s.builtSeq, [sessionId]: seq },
    }))
    try {
      const res = await buildNativeSession(sessionId)
      if (get().epoch !== epoch) return
      set((s) => ({
        sessions: { ...s.sessions, [sessionId]: res.session },
        errors: { ...s.errors, [sessionId]: null },
        conflicts: { ...s.conflicts, [sessionId]: res.conflicts?.stems ?? [] },
      }))
      placeNativeFigures(res.descriptors)
    } catch (e) {
      if (get().epoch !== epoch) return
      // build 失败不改变会话状态——那由 SSE 说了算。这里只记一条原因。
      set((s) => ({
        errors: { ...s.errors, [sessionId]: toNativeError(e) },
        // 这一轮没成，下一次事件还要再试
        builtSeq: { ...s.builtSeq, [sessionId]: -1 },
      }))
    } finally {
      if (get().epoch === epoch) set((s) => ({ busy: { ...s.busy, [sessionId]: false } }))
    }
  },

  resume: (sessionId) => runAction(sessionId, continueNativeSession, set, get),
  detach: (sessionId) => runAction(sessionId, detachNativeSession, set, get),
  terminate: (sessionId) => runAction(sessionId, terminateNativeSession, set, get),

  refresh: async (projectRoot) => {
    const epoch = get().epoch
    try {
      const res = await fetchNativeSessions(projectRoot)
      if (get().epoch !== epoch) return
      set((s) => {
        const sessions = { ...s.sessions }
        for (const one of res.sessions) {
          const prev = sessions[one.session_id]
          // 对账同样按 sequence：清单是"现在"的快照，但一次慢响应回来时
          // SSE 可能已经把更新的状态送到了
          if (!prev || one.sequence > prev.sequence) sessions[one.session_id] = one
        }
        return { sessions }
      })
    } catch {
      /* 清单取不到不清空已知会话：SSE 仍在，下一条事件会补上 */
    }
  },

  dismiss: (sessionId) =>
    set((s) => {
      const one = s.sessions[sessionId]
      // 只收终态的卡片：活着的会话收起来就再也找不回来了
      if (!one || !isNativeTerminal(one.state)) return {}
      const sessions = { ...s.sessions }
      delete sessions[sessionId]
      const errors = { ...s.errors }
      delete errors[sessionId]
      const conflicts = { ...s.conflicts }
      delete conflicts[sessionId]
      return { sessions, errors, conflicts }
    }),

  clear: () =>
    set((s) => ({
      epoch: s.epoch + 1,
      // **`pendingQueue` 刻意不清。** 待确认的交接不属于任何一个界面项目：
      // 它自带 project / interpreter / cwd，attach 也不看界面此刻开着哪个
      // 项目（这正是 `applyOpenRequest` 里"每条出口都要排队"的理由）。
      //
      // 跟着清的表现是：终端 1 的确认屏还开着，终端 2 起了另一个项目的
      // `tavotto run` → 换项目 → 第一条**既没批准也没取消**地消失，终端 1
      // 白等满 5 分钟的 attach 超时。
      //
      // live 会话跟着清是对的（它们是这个项目的渲染状态），pending 不是。
      sessions: {},
      busy: {},
      errors: {},
      conflicts: {},
      builtSeq: {},
    })),
}))

type SetFn = (fn: (s: NativeSessionStore) => Partial<NativeSessionStore>) => void
type GetFn = () => NativeSessionStore

/** continue / detach / terminate 三个动作同一个形状：互斥闸 + 代际 + 如实记错。 */
function runAction(
  sessionId: string,
  call: (id: string) => Promise<{ session: NativeSessionInfo }>,
  set: SetFn,
  get: GetFn,
): Promise<void> {
  const session = get().sessions[sessionId]
  if (!session || get().busy[sessionId] || isNativeTerminal(session.state)) {
    return Promise.resolve()
  }
  const epoch = get().epoch
  set((s) => ({ busy: { ...s.busy, [sessionId]: true }, errors: { ...s.errors, [sessionId]: null } }))
  return call(sessionId)
    .then((res) => {
      if (get().epoch !== epoch) return
      const prev = get().sessions[sessionId]
      // 响应里带的是这一刻的快照；SSE 可能已经送来更新的了
      if (!prev || res.session.sequence >= prev.sequence) {
        set((s) => ({ sessions: { ...s.sessions, [sessionId]: res.session } }))
      }
    })
    .catch((e) => {
      if (get().epoch !== epoch) return
      set((s) => ({ errors: { ...s.errors, [sessionId]: toNativeError(e) } }))
    })
    .finally(() => {
      if (get().epoch === epoch) set((s) => ({ busy: { ...s.busy, [sessionId]: false } }))
    })
}

/**
 * 屏障处拿到的图接进画布。
 *
 * - 一张：直接放进画布并选中——用户敲那条命令就是为了看它；
 * - 多张：**打开 Figure 选择器**，绝不静默选第一张（Session 6 的契约，
 *   native 只是换了个数据源）；
 * - 画布上已经有同一个 asset：只选中，不叠第二份（同一条会话的下一个屏障
 *   会再来一次，每次新增会堆出一摞同名面板）。
 *
 * 清单与预览一并换代：这批图刚刚才被物化进 runtime cache。
 */
function placeNativeFigures(descriptors: CapturedFigureDescriptor[]): void {
  if (!descriptors.length) return
  const ids = descriptors.map((d) => d.asset_id)
  try {
    // 清单/预览刷新是**尽力而为**：它失败不该把"图没进画布"一起带走。
    // 这个 try 刻意只包住这四行——把整个放置逻辑一起包进来的话，真正的
    // 缺陷（面板加错了、选择器没开）会变成一条静默的 no-op。
    const runtime = useRuntimeAssetStore.getState()
    runtime.invalidate(ids)
    runtime.bumpPreview(ids)
    void runtime.loadAssets()
    useRenderStore.getState().markStale(ids)
  } catch {
    /* 下一次 SSE / 手动刷新会补上 */
  }

  const objects = useDocumentStore.getState().doc.objects
  const existing = (id: string) =>
    objects.find((o) => o.type === 'panel' && (o as { fileId: string }).fileId === id)

  if (descriptors.length > 1) {
    useFigurePickerStore.getState().open(descriptors[0].script)
    return
  }
  const only = descriptors[0]
  const already = existing(only.asset_id)
  if (already) {
    useSelectionStore.getState().set([already.id])
    return
  }
  addRuntimePanel(only)
}
