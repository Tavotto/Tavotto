import { useCallback, useEffect, useRef, useState } from 'react'
import '@/lib/aiDraftLedger'
import {
  AI_DRAFT_CHANGED_EVENT,
  fetchDraftState,
  isDraftTooLarge,
  loadProjectPendings,
  type DraftTarget,
} from '@/lib/aiDraft'
import {
  flush as outboxFlush,
  has as outboxHas,
  hasSeen as outboxHasSeen,
  isStalled as outboxIsStalled,
  markUnknown as outboxMarkUnknown,
  noteRead as outboxNoteRead,
  advance as outboxAdvance,
  peek as outboxPeek,
  peekRejected as outboxPeekRejected,
  post as outboxPost,
  subscribe as outboxSubscribe,
} from '@/lib/aiDraftOutbox'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { sameScript, useAiStore } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import type { PanelObject } from '@/types/document'

/** 写后端的防抖 / 失败重试间隔由发件箱持有（`lib/aiDraftOutbox`）；这里转出口，测试与面板从这里取 */
export { RETRY_MS, WRITE_DEBOUNCE_MS } from '@/lib/aiDraftOutbox'
/**
 * 对账里的每一次读（草稿 GET、项目账本 GET）最多等这么久；超时 = 读失败（`null`）。半开连接上的读永远不会自己结束，
 * 不设限的话 `ledgerState` 永远是 `unknown`、发送一直锁着、重试提示也不出现。`draftRead` 的「读失败」也由这同一个结局驱动，不另设计时。
 */
export const DRAFT_LOAD_TIMEOUT_MS = 5000

/** 带超时的读：超时先中止请求（`AbortController`），并不再等它——即使宿主的请求不响应中止也会结束；迟到的结果直接丢弃 */
function readWithTimeout<T>(run: (signal: AbortSignal) => Promise<T | null>): Promise<T | null> {
  const ctl = new AbortController()
  let t: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<null>((resolve) => {
    t = setTimeout(() => {
      ctl.abort()
      resolve(null)
    }, DRAFT_LOAD_TIMEOUT_MS)
  })
  return Promise.race([run(ctl.signal), timeout]).finally(() => clearTimeout(t))
}

/**
 * 账本恢复状态（**与 `reconcile` 共用一个状态源**）：`unknown` = 还没成功读到过这张图的未结提交；`ok` = 至少成功对账过一次
 * （草稿 + 项目级账本都读成功，且 pendings 已还原）；`failed` = 对账读失败。**只有 `ok` 才允许发送**——
 * 刷新 / 重挂后账本还在异步读的那一小段，原任务可能早已结束、后端的忙碌守卫早已失效，这时放行发送会生成新的
 * 请求 id、同一条修改执行两次；读失败是「不知道」，不是「空账本」。
 */
export type LedgerState = 'unknown' | 'ok' | 'failed'

/** 对账的来源。**所有来源走同一个 `reconcile`**，不存在「只取文本」的分支 */
export type ReconcileSource = 'mount' | 'focus' | 'visible' | 'reconnect' | 'accepted' | 'conflict'

/**
 * 助手输入框的草稿（ADR 0132）：绑定 **项目 × 面板文件**，**存后端**（不碰 localStorage）。
 *
 * 为什么要持久化：右栏切页签 / 收起会把 `AssistantPanel` 整个卸载（`Inspector` 条件挂载），刷新、关窗、
 * 桌面端口变化（换 origin）更不用说——这些都不该让写了一半的指令消失。
 *
 * **同步状态机（两条规则，所有入口共用）**
 * 1. **对账只有一个函数 `reconcile()`**：挂载、窗口重新获得焦点、页面回到前台、SSE 重连都只调它。它一次性应用
 *    服务器给的两份状态——待确认提交（`pendings`，含同脚本其它面板、发起面板已不在的，走与挂载时**完全相同**的
 *    还原路径，永远应用）与草稿文本（**有条件**应用，见 2）。不存在只取 `text` 的分支：入口各写各的，就会漏掉
 *    另一半（#1098 一再出的就是这一类）。
 * 2. **写入由发件箱持有**（`lib/aiDraftOutbox.ts`）：本地每次改动 `seq++` 并把这一版 `post` 给发件箱，它按 (项目, 文件) 分槽，
 *    只留最新版本，负责防抖、PUT、重试、stale 处置，**与本 hook 实例的寿命无关**（切目标 / 卸载后没写成的版本照样重试到成功）。
 *    只要发件箱里还留着这个目标的版本（没发出 / 在途 / 失败待重试），对账读回来的**文本一律不覆盖输入框**（只更新 pendings）；
 *    对账请求发出之后本地又改过（`seq` 变了）同样不覆盖。重新挂载同一目标时先显示发件箱里的版本。
 * 写：防抖 600 ms；`pagehide` / 转入后台由发件箱对所有未落定的槽 `keepalive` 冲刷；卸载 / 切目标时冲刷一次；失败（`unavailable`）时
 * 文本留在发件箱、3 秒后重试。
 * 删：**不在这里**——只有后端给出会话 id（请求被接受）之后，store 经账本 `accept` 删，条件是「草稿戳自提交后没变」
 *   （发送前 `draftSnapshot` 先把草稿写成功、拿到戳记进账本；**不比文本**）；之后经 `mm:ai-draft-changed` 对账，
 *   输入框回到后端的状态。
 * 戳的下限：每次读到服务器的戳（对账的 GET、PUT 的响应、stale 的响应）都抬高本页下限，所以墙钟回拨 / 新开页面
 * 从 0 起步，后面发出的戳也一定比库里的大；被判 stale 时本地是用户真实输入的，就以本地为准用更大的戳重写（有界）。
 */
type Pending = { file: string; rid: string; text: string; sent: DraftTarget; script: string | null }

export function useAiDraft(panel: PanelObject | null) {
  const fileId = panel?.fileId ?? null
  // 项目认领是异步到的（刷新后挂载那一刻可能还是 null）：跟着它走，到了就读
  const [project, setProject] = useState(currentProjectId())
  useEffect(() => onCurrentProjectChange(setProject), [])

  const [prompt, setPromptState] = useState('')
  const [restored, setRestored] = useState(false)
  const [tooLarge, setTooLarge] = useState(false)
  const [ledgerState, setLedgerState] = useState<LedgerState>('unknown')
  /**
   * 第一次草稿读取的结局：`loading` 期间输入框只读（**结构上**消掉「在没见过服务器版本时输入」的窗口——
   * 每一次本地编辑都一定基于本页「已见」的服务器版本，所以 stale 里「没见过」只可能是别的标签页写的）；
   * 读失败（`failed`）= 不知道服务器上有没有更新的版本：允许编辑（用户的输入不能丢），见发件箱的「基线未知」。
   */
  /** 写入已连续失败超过一分钟（发件箱宣布）：输入框上方一行安静的灰字 */
  const [draftStalled, setDraftStalled] = useState(false)
  /** 跨标签页冲突：本页未落定的版本被放弃、输入框换成了另一个窗口的稿——静默替换会让用户以为自己刚输入的字凭空消失；下一次编辑后消失 */
  const [draftReplaced, setDraftReplaced] = useState(false)
  const [draftRead, setDraftRead] = useState<'loading' | 'ok' | 'failed'>('loading')

  const key = useRef<{ project: string | null; fileId: string | null; epoch: number }>({
    project: null,
    fileId: null,
    epoch: 0,
  })
  const text = useRef('')
  /** 本地改动序号（失败的发送记下它；`clearIfUnchanged` / 对账期间「本地又改过」都靠它）。**写入的落定不在这里**：见发件箱 */
  const seq = useRef(0)
  /** 服务器上**与本地文本一致**的那一行的写入戳（发件箱接受 / 对账读到时更新）；发送前拿它记进账本 */
  const draftStamp = useRef<number | null>(null)
  const reconcileRef = useRef<((source: ReconcileSource) => Promise<void>) | null>(null)
  /**
   * **冲突替换待发生**：发件箱放弃了本页版本（别处写了更新稿）、要用服务器文本替换输入框。这个标记是持久状态，不绑在某一次
   * 对账的回调上——紧接的那次冲突对账失败 / 超时时，之后 focus / 重连的对账仍会用另一窗口的稿覆盖可见文本，那时照样要提示。
   * 只在**一次成功的对账确实应用了不同文本**时消费并提示；应用相同文本则清掉不提示；下一次用户编辑也清掉。
   */
  const replacePending = useRef(false)
  const reconcileToken = useRef(0)
  /** 最新的 panel：`reconcile` 里读（不把它放进依赖，免得每次渲染都重建各个入口） */
  const panelRef = useRef(panel)
  panelRef.current = panel

  /** 有没落定的本地写入（发件箱里还留着这个目标的版本：没发出 / 在途 / 失败待重试）：此刻对账读回的文本不许覆盖输入框 */
  const hasUnsettledWrite = () => {
    const { project: pj, fileId: f } = key.current
    // 被 413 拒绝的那一版文本（服务端只留了空墓碑）也算：它是输入框里用户的字，对账不得用服务器的空文本覆盖它
    return !!pj && !!f && (outboxHas(pj, f) || outboxPeekRejected(pj, f) !== null)
  }

  const setPrompt = useCallback((next: string | ((prev: string) => string)) => {
    const value = typeof next === 'function' ? next(text.current) : next
    text.current = value
    seq.current++
    setPromptState(value)
    setRestored(false)
    setDraftReplaced(false)
    replacePending.current = false
    setTooLarge(isDraftTooLarge(value))
    // 投递给发件箱：它负责防抖、PUT、重试、stale 处置，与本实例的寿命无关
    const { project: pj, fileId: f } = key.current
    if (pj && f) outboxPost(pj, f, value)
  }, [])

  /**
   * **唯一的对账入口**：把服务器此刻的状态应用到本地。
   * - 待确认提交（`pendings`）：**永远**应用，走同一条还原路径——本面板这个文件的、同一份脚本其它面板的、
   *   发起面板已不在的（项目级列表按 script 匹配），一条不落；
   * - 草稿文本：只有在没有任何未落定的本地写入、且对账期间本地没再改过时才应用（挂载来源额外亮「已恢复」提示）。
   * 读失败（`null`）= 不知道，什么都不动。
   */
  // 待确认提交：**永远**应用（走同一条还原路径），按 rid 去重。对账与「后端说还有没落定的」（409 带回的行）共用它
  const restorePendingsFor = useCallback((pj: string, items: Pending[]) => {
    const p = panelRef.current
    if (!p) return
    const objects = useDocumentStore.getState().doc.objects
    const seen = new Set<string>()
    for (const item of items) {
      if (seen.has(item.rid)) continue
      seen.add(item.rid)
      const sameFile = item.file === p.fileId
      if (!sameFile && !(p.script && item.script && sameScript(item.script, p.script))) continue
      const sourcePanel = objects.find((o) => o.type === 'panel' && (o as PanelObject).fileId === item.file) as
        | PanelObject
        | undefined
      useAiStore.getState().restoreSubmission({
        rid: item.rid,
        project: pj,
        fileId: item.file,
        panelId: sourcePanel?.id ?? '',
        script: item.script ?? (sameFile ? (p.script ?? null) : null),
        prompt: item.text,
        target: item.sent,
        canvas: useDocumentStore.getState().activeCanvasId,
        overrides: sameFile ? p.overrides : (sourcePanel?.overrides ?? []),
      })
    }
  }, [])

  const reconcile = useCallback(async (source: ReconcileSource) => {
    const { project: pj, fileId: f, epoch } = key.current
    if (!pj || !f) return
    const token = ++reconcileToken.current
    const seqAtStart = seq.current
    const [state, projectList] = await Promise.all([
      readWithTimeout((signal) => fetchDraftState(pj, f, signal)),
      readWithTimeout((signal) => loadProjectPendings(pj, signal)),
    ])
    if (key.current.epoch === epoch) {
      if (state) {
        outboxNoteRead(pj, f) // 读成功：基线不再未知。是否推进基线要看文本有没有被采用（见下）
        setDraftRead('ok')
      } else {
        // 一次都没读成功过：服务器版本未知，stale 按「已见」处理（用户的输入不能丢）
        if (!outboxHasSeen(pj, f)) outboxMarkUnknown(pj, f)
        // 这次读失败就退出 loading：重开面板时出口槽位还留着上次的 read=true，只挡 markUnknown，否则文本框一直只读
        setDraftRead('failed')
      }
    }
    // 本地非终态的会话也按后端快照收尾（`accept` 之后任务在 SSE 断线期间结束，事件流不重放）——和 pendings 一样，每个入口都做
    void useAiStore.getState().settleRunningSessions()
    // 过期：又有更新的一次对账、或目标已经换了——不动
    if (token !== reconcileToken.current || key.current.epoch !== epoch) return
    const restorePendings = (items: Pending[]) => restorePendingsFor(pj, items)
    // 本文件自己的账本（随草稿一起回来）：项目级列表理论上已含，这里兜底、按 rid 去重
    const ownPendings = (): Pending[] =>
      (state?.pendings ?? []).map((own) => ({
        file: f,
        rid: own.rid,
        text: own.text,
        sent: own.sent,
        script: panelRef.current?.script ?? null,
      }))
    // 草稿文本与待确认账本是两次独立的读，**各自独立应用**：「可写 ⇒ 服务器草稿已经应用」——草稿读成功而项目级账本读失败时，
    // 输入框必须已经显示服务器文本（否则用户看到空框、打的字带着比服务器更高的写入戳，静默盖掉看不见的草稿）
    const applyDraft = () => {
      if (!state) return
      if (hasUnsettledWrite() || seq.current !== seqAtStart) return // 本地写入没落定：文本以本地为准，不覆盖
      if (state.text === text.current) {
        draftStamp.current = state.stamp ?? null // 服务器这一行与本地一致：记下它的戳
        outboxAdvance(pj, f, state.stamp) // 输入框显示的就是这份文本：基线推进到它的戳
        replacePending.current = false // 采纳的是同一份文本：字没变，不提示
        return
      }
      if (replacePending.current) {
        // 冲突放弃之后，这次成功的对账真的把另一个窗口的稿换进了输入框：提示用户，不静默替换
        replacePending.current = false
        setDraftReplaced(true)
      }
      text.current = state.text
      draftStamp.current = state.stamp ?? null
      outboxAdvance(pj, f, state.stamp) // 服务器文本被应用到了输入框：基线推进
      setPromptState(state.text)
      setTooLarge(false)
      setRestored(source === 'mount' && state.text !== '')
    }
    // 项目级待确认读失败：别的标签页可能在同一份脚本的兄弟面板上留了本页看不到的未确认提交，本页此刻发出去，原任务一结束
    // 后端的忙检查就不再拦、同一条修改换新 rid 再执行一次——**无论之前是否成功过**都降回 failed（锁发送、给重试），
    // 直到下一次成功的项目级读取。草稿读成功的部分照常应用。
    if (!projectList) {
      setLedgerState('failed')
      restorePendings(ownPendings())
      applyDraft()
      return
    }
    // 只有草稿读失败（项目级读成功）：项目级列表**照样还原**——别的标签页可能刚在兄弟面板上留了未确认提交，丢掉它本页就能再发一次；
    // 账本结论维持已有的（这次没读到本文件自己那份兜底，不因此新放开发送）
    if (!state) {
      restorePendings(projectList)
      setLedgerState((cur) => (cur === 'ok' ? cur : 'failed'))
      return
    }
    restorePendings([...projectList, ...ownPendings()])
    setLedgerState('ok') // pendings 已还原：账本恢复完成，可以放开发送
    applyDraft()
  },[restorePendingsFor])

  reconcileRef.current = reconcile

  // 目标（项目 × 面板文件）变了：旧的在 cleanup 里冲刷，新的对账一次
  useEffect(() => {
    key.current = { project, fileId, epoch: key.current.epoch + 1 }
    setDraftReplaced(false)
    replacePending.current = false
    text.current = ''
    seq.current = 0
    draftStamp.current = null
    setDraftRead(project && fileId ? 'loading' : 'ok') // 没有可读的目标（无项目认领 / 无面板）= 没有要载入的草稿
    // 发件箱里还留着这个目标没落定的版本（上一个实例退役时没写成）：先显示它，不是服务器上的旧值；对账也不会覆盖它
    const held = project && fileId ? (outboxPeek(project, fileId) ?? outboxPeekRejected(project, fileId)) : null
    text.current = held ?? ''
    setPromptState(held ?? '')
    setRestored(false)
    setTooLarge(held !== null && isDraftTooLarge(held))
    setLedgerState('unknown') // 换目标 / 重挂：这张图的账本还没读过
    if (!project || !fileId) return
    setDraftStalled(outboxIsStalled(project, fileId))
    const off = outboxSubscribe(project, fileId, (e) => {
      if (e.kind === 'stalled') {
        setDraftStalled(e.on)
        return
      }
      if (e.kind === 'abandoned') {
        // 服务器上有本页没见过的更新版本（别的标签页写的）：本版已放弃，对账采纳服务器值——
        // 采纳之后输入框里的字变了，要让用户知道（一句安静的提示），不能静默替换
        replacePending.current = true // 持久标记：这次对账失败也不丢，下一次成功的对账应用不同文本时消费
        void reconcileRef.current?.('conflict')
        return
      }
      // 服务器上这一行现在就是本地最新的这一版（槽里没有更新的了）：记下它的戳
      if (e.stamp !== undefined) draftStamp.current = e.stamp
      if (e.result === 'too_large') setTooLarge(true)
    })
    void reconcile('mount')
    return () => {
      off()
      reconcileToken.current++
      // 切目标 / 卸载：把没落定的版本冲刷一次（keepalive）。没写成也不会丢——它留在发件箱里由发件箱重试，本实例到此为止
      void outboxFlush(project, fileId, true)
    }
  }, [project, fileId, reconcile])

  // 页面离开 / 转入后台：keepalive 冲刷；回到前台 / 重新获得焦点 / SSE 重连：对账（多标签页以后端为准）
  useEffect(() => {
    // 离开页面 / 转入后台的 keepalive 冲刷由发件箱在模块级做（与任何实例的寿命无关），这里只管回到前台的对账
    const onVisibility = () => {
      if (document.visibilityState !== 'hidden') void reconcile('visible')
    }
    const onFocus = () => {
      if (document.visibilityState !== 'hidden') void reconcile('focus')
    }
    const onReconnect = () => void reconcile('reconnect')
    // 确认之后后端按「草稿戳没变」清了草稿（或没清）：对账一次，输入框以后端为准
    const onAccepted = () => void reconcile('accepted')
    window.addEventListener('focus', onFocus)
    window.addEventListener('mm:sse-open', onReconnect)
    window.addEventListener(AI_DRAFT_CHANGED_EVENT, onAccepted)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('mm:sse-open', onReconnect)
      window.removeEventListener(AI_DRAFT_CHANGED_EVENT, onAccepted)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [reconcile])

  /**
   * **发送前的草稿快照**：把没写出去的文本先写成功（拿到服务器接受的写入戳），再回 `{ seq, stamp }`。
   * `stamp` 记进账本，确认之后后端按「草稿自提交后没被改过（戳没变）」清草稿——**不比文本**（提交文本是 trim 过的、
   * 草稿是原样，文本比较对不上，已被接受的指令会留在输入框里再被发一次）。没写成（后端不可用）`stamp` 为 null =
   * 不知道，确认时不动草稿。
   */
  const draftSnapshot = useCallback(async (): Promise<{ seq: number; stamp: number | null }> => {
    const { project: pj, fileId: f } = key.current
    if (pj && f) await outboxFlush(pj, f, false) // 发件箱把没写出去的版本写成功（落地事件先于这里的 await 返回，已更新 draftStamp）
    const settled = !pj || !f || !outboxHas(pj, f)
    return { seq: seq.current, stamp: settled ? draftStamp.current : null }
  }, [])

  /**
   * 提交被接受之后清输入框：**只看本地有没有再动过（`seq` 没变），不比文本**。仅在发送时拿到了草稿戳
   * （后端会据此删草稿）才调用；之后后端的清理结果经 `mm:ai-draft-changed` 的对账回到输入框。
   */
  const clearIfUnchanged = useCallback((seqAtSubmit: number) => {
    if (seq.current !== seqAtSubmit) return
    text.current = ''
    draftStamp.current = null
    setPromptState('')
    setTooLarge(false)
  }, [])

  /** 账本读失败后的「重试」：就是再对账一次（同一个入口） */
  const retryLedger = useCallback(() => void reconcile('focus'), [reconcile])
  /** 立即对账一次、等它做完（后端说「还有没落定的」之后：先把它们还原出来，再逐条查询） */
  const reconcileNow = useCallback(() => reconcile('focus'), [reconcile])
  /**
   * 后端（写账本的事务里）回 409 `ai_pending_unresolved` 时带回的挡路行：**照着它还原**，不靠之后另读一次列表——那次读之前
   * 别的标签页可能已经确认并删掉了这一行，本页就既看不到它、也查不了它，发送随即放开
   */
  const restoreBlockers = useCallback(
    (pj: string | null, items: Pending[]) => {
      // 项目由调用方钉住（发送时固定下来的那个），不读「当前项目」；与当前的不一致（途中切过）就什么都不动
      if (pj && pj === key.current.project) restorePendingsFor(pj, items)
    },
    [restorePendingsFor],
  )

  /** 输入框当前的本地版本号（失败的发送记下它；重试时据此判「输入框自那以后没改过」） */
  const draftSeq = useCallback(() => seq.current, [])

  return {
    prompt,
    setPrompt,
    restored,
    tooLarge,
    clearIfUnchanged,
    draftSnapshot,
    draftSeq,
    project,
    ledgerState,
    retryLedger,
    reconcileNow,
    restoreBlockers,
    draftLoading: draftRead === 'loading',
    draftStalled,
    draftReplaced,
  }
}
