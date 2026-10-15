import { useCallback, useEffect, useRef, useState } from 'react'
import '@/lib/aiDraftLedger'
import {
  AI_DRAFT_CHANGED_EVENT,
  fetchDraftState,
  isDraftTooLarge,
  loadProjectPendings,
  nextWriteStamp,
  saveDraftStamped,
  type DraftTarget,
} from '@/lib/aiDraft'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { sameScript, useAiStore } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import type { PanelObject } from '@/types/document'

/** 打字停多久才写后端：每个键都发请求没必要，丢的也只是最后半秒（pagehide 会冲刷） */
export const WRITE_DEBOUNCE_MS = 600
/** 写失败（离线 / 后端暂时不可用）后多久重试：文本一直留在内存里，不丢 */
export const RETRY_MS = 3000

/**
 * 账本恢复状态（**与 `reconcile` 共用一个状态源**）：`unknown` = 还没成功读到过这张图的未结提交；`ok` = 至少成功对账过一次
 * （草稿 + 项目级账本都读成功，且 pendings 已还原）；`failed` = 对账读失败。**只有 `ok` 才允许发送**——
 * 刷新 / 重挂后账本还在异步读的那一小段，原任务可能早已结束、后端的忙碌守卫早已失效，这时放行发送会生成新的
 * 请求 id、同一条修改执行两次；读失败是「不知道」，不是「空账本」。
 */
export type LedgerState = 'unknown' | 'ok' | 'failed'

/** 对账的来源。**所有来源走同一个 `reconcile`**，不存在「只取文本」的分支 */
export type ReconcileSource = 'mount' | 'focus' | 'visible' | 'reconnect' | 'accepted'

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
 * 2. **写入带版本**：本地每次改动 `seq++`；`acked` = 已被后端确认的最新序号；`inflight` = 正在途中的 PUT 的序号。
 *    只要 `seq > acked`（有没发出的、在途的、失败待重试的本地写入），对账读回来的**文本一律不覆盖输入框**
 *    （只更新 pendings）；对账请求发出之后本地又改过（`seq` 变了）同样不覆盖。一次 PUT 确认时若本地又有更新的
 *    写入，**以本地为准**再写一次——后端是最后写入为准，所以这就是收敛。
 * 写：防抖 600 ms；`pagehide` / 转入后台 / 卸载 / 切目标时 `keepalive` 立刻冲刷；失败（`unavailable`）时文本留在内存、
 * 3 秒后重试。
 * 删：**不在这里**——只有后端给出会话 id（请求被接受）之后，store 经账本 `accept` 删，条件是「草稿戳自提交后没变」
 *   （发送前 `draftSnapshot` 先把草稿写成功、拿到戳记进账本；**不比文本**）；之后经 `mm:ai-draft-changed` 对账，
 *   输入框回到后端的状态。
 * 戳的下限：每次读到服务器的戳（对账的 GET、PUT 的响应、stale 的响应）都抬高本页下限，所以墙钟回拨 / 新开页面
 * 从 0 起步，后面发出的戳也一定比库里的大；被判 stale 时本地是用户真实输入的，就以本地为准用更大的戳重写（有界）。
 */
export function useAiDraft(panel: PanelObject | null) {
  const fileId = panel?.fileId ?? null
  // 项目认领是异步到的（刷新后挂载那一刻可能还是 null）：跟着它走，到了就读
  const [project, setProject] = useState(currentProjectId())
  useEffect(() => onCurrentProjectChange(setProject), [])

  const [prompt, setPromptState] = useState('')
  const [restored, setRestored] = useState(false)
  const [tooLarge, setTooLarge] = useState(false)
  const [ledgerState, setLedgerState] = useState<LedgerState>('unknown')

  const key = useRef<{ project: string | null; fileId: string | null; epoch: number }>({
    project: null,
    fileId: null,
    epoch: 0,
  })
  const text = useRef('')
  /** 写入版本：本地改动序号 / 已确认序号 / 在途序号（null = 没有在途的 PUT） */
  const seq = useRef(0)
  const acked = useRef(0)
  const inflight = useRef<number | null>(null)
  /** 最近一次启动的 PUT（`draftSnapshot` 等它落地） */
  const inflightP = useRef<Promise<void> | null>(null)
  /** 服务器上**与本地文本一致**的那一行的写入戳（PUT 被接受 / 对账读到时更新）；发送前拿它记进账本 */
  const draftStamp = useRef<number | null>(null)
  /**
   * 当前本地版本的写入戳：**每次本地编辑（`seq++`）时分配**，这一版的所有 PUT / 重试 / keepalive 都带它，发送时不重新取。
   * 所以旧版本无论什么时候重试，戳都比新版本小，服务器一定拒绝；只有「本页用户真实输入的最新版本」被判 stale 时才给它换更大的戳。
   */
  const versionStamp = useRef<number>(0)
  /** 本实例已退役（卸载）：退役后的回调不再安排任何重试 / 重写，免得拿着旧文本去覆盖重新挂载后保存的新草稿 */
  const retired = useRef(false)
  /** 同一段本地内容连续被判 stale 的次数（墙钟回拨 / 并发写时有界重写，免得两端互相覆盖没完） */
  const staleRetries = useRef(0)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const retry = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const reconcileToken = useRef(0)
  /** 最新的 panel：`reconcile` 里读（不把它放进依赖，免得每次渲染都重建各个入口） */
  const panelRef = useRef(panel)
  panelRef.current = panel

  /** 有没落定的本地写入（没发出 / 在途 / 失败待重试）：此刻对账读回的文本不许覆盖输入框 */
  const hasUnsettledWrite = () => seq.current > acked.current

  /** 把当前文本写到后端。`keepalive`：页面正在关。同一时刻只有一个普通 PUT 在途，落地后若本地又有更新的写入就再写一次 */
  const persist = useCallback((keepalive: boolean): Promise<void> => {
    clearTimeout(timer.current)
    timer.current = undefined
    if (seq.current === acked.current) return Promise.resolve()
    if (retired.current && !keepalive) return Promise.resolve() // 退役实例不再发起任何写入
    if (inflight.current !== null && !keepalive) return inflightP.current ?? Promise.resolve() // 在途的落地时会检查并补写
    const mySeq = seq.current
    const { project: pj, fileId: f, epoch } = key.current
    const body = text.current
    const myStamp = versionStamp.current // 这一版的戳（编辑时分配）：重试、keepalive 都用它
    inflight.current = mySeq
    const p = saveDraftStamped(pj, f, body, keepalive, myStamp).then(async ({ result: r, stamp }) => {
      if (key.current.epoch !== epoch) return // 目标已换：那一边的收尾由切目标时的冲刷负责，不动当前目标的版本
      inflight.current = null
      if (r === 'stale') {
        // 服务端说：它那里有比这次写入**更新的戳**，这次没被采纳（`saveDraft` 已按服务端回的戳抬高了本页下限，
        // 再写一次的戳一定更大）。两种成因：同页内并发写（B 先落地、A 后落地——B 的写入会自己推进 acked）、
        // 或墙钟回拨 / 另一个标签页的戳更新。本地内容是用户在本页**真实输入**的，所以以本地为准：还没被确认就重写
        if (seq.current > acked.current) {
          if (++staleRetries.current > 5) {
            // 有界：两端互相压戳时别没完没了，隔一会儿再试
            clearTimeout(retry.current)
            retry.current = setTimeout(() => persist(false), RETRY_MS)
            return
          }
          // 被拒的这一版**仍是本页最新的内容**才给它换一个更大的戳（`saveDraftStamped` 已按服务端回的戳抬高了下限）；
          // 已经有更新的版本时，那一版带着自己的戳，旧版本不换戳、不重写——旧版本的戳永远小
          if (seq.current === mySeq) versionStamp.current = nextWriteStamp()
          await persist(false)
        }
        return
      }
      if (r === 'unavailable') {
        // 没写成：文本还在内存里，序号没确认——3 秒后重试（用户又改了就走新的防抖）
        clearTimeout(retry.current)
        retry.current = setTimeout(() => persist(false), RETRY_MS)
        return
      }
      staleRetries.current = 0
      acked.current = Math.max(acked.current, mySeq)
      if (r === 'too_large') setTooLarge(true)
      // 服务器上这一行现在就是本地这一版：记下它的戳（只有「本地没有更新的写入」时才算与本地一致）
      if (seq.current === mySeq && stamp !== undefined) draftStamp.current = stamp
      // 落地的这段确认之前，本地又有更新的写入：以本地为准再写一次
      if (seq.current > acked.current) await persist(false)
    })
    inflightP.current = p
    return p
  }, [])

  const setPrompt = useCallback(
    (next: string | ((prev: string) => string)) => {
      const value = typeof next === 'function' ? next(text.current) : next
      text.current = value
      seq.current++
      versionStamp.current = nextWriteStamp() // 这一版内容的戳：编辑发生的时刻
      setPromptState(value)
      setRestored(false)
      setTooLarge(isDraftTooLarge(value))
      clearTimeout(retry.current)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => persist(false), WRITE_DEBOUNCE_MS)
    },
    [persist],
  )

  /**
   * **唯一的对账入口**：把服务器此刻的状态应用到本地。
   * - 待确认提交（`pendings`）：**永远**应用，走同一条还原路径——本面板这个文件的、同一份脚本其它面板的、
   *   发起面板已不在的（项目级列表按 script 匹配），一条不落；
   * - 草稿文本：只有在没有任何未落定的本地写入、且对账期间本地没再改过时才应用（挂载来源额外亮「已恢复」提示）。
   * 读失败（`null`）= 不知道，什么都不动。
   */
  const reconcile = useCallback(async (source: ReconcileSource) => {
    const { project: pj, fileId: f, epoch } = key.current
    if (!pj || !f) return
    const token = ++reconcileToken.current
    const seqAtStart = seq.current
    const [state, projectList] = await Promise.all([fetchDraftState(pj, f), loadProjectPendings(pj)])
    // 本地非终态的会话也按后端快照收尾（`accept` 之后任务在 SSE 断线期间结束，事件流不重放）——和 pendings 一样，每个入口都做
    void useAiStore.getState().settleRunningSessions()
    // 过期：又有更新的一次对账、或目标已经换了——不动
    if (token !== reconcileToken.current || key.current.epoch !== epoch) return
    // 读失败 = 不知道：账本状态没成功过就记 failed（继续锁发送、给重试），成功过的不降级；草稿 / pendings 一概不动
    if (!state || !projectList) {
      setLedgerState((cur) => (cur === 'ok' ? cur : 'failed'))
      return
    }

    const p = panelRef.current
    if (p) {
      const objects = useDocumentStore.getState().doc.objects
      const seen = new Set<string>()
      const apply = (item: { file: string; rid: string; text: string; sent: DraftTarget; script: string | null }) => {
        if (seen.has(item.rid)) return
        seen.add(item.rid)
        const sameFile = item.file === p.fileId
        if (!sameFile && !(p.script && item.script && sameScript(item.script, p.script))) return
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
      for (const item of projectList) apply(item)
      // 本文件自己的账本（随草稿一起回来）同样还原（项目级列表理论上已含，这里兜底、按 rid 去重）
      for (const own of state.pendings ?? [])
        apply({ file: f, rid: own.rid, text: own.text, sent: own.sent, script: p.script ?? null })
    }

    setLedgerState('ok') // pendings 已还原：账本恢复完成，可以放开发送
    if (hasUnsettledWrite() || seq.current !== seqAtStart) return // 本地写入没落定：文本以本地为准，不覆盖
    if (state.text === text.current) {
      draftStamp.current = state.stamp ?? null // 服务器这一行与本地一致：记下它的戳
      return
    }
    text.current = state.text
    draftStamp.current = state.stamp ?? null
    setPromptState(state.text)
    setTooLarge(false)
    setRestored(source === 'mount' && state.text !== '')
  }, [])

  // 目标（项目 × 面板文件）变了：旧的在 cleanup 里冲刷，新的对账一次
  useEffect(() => {
    key.current = { project, fileId, epoch: key.current.epoch + 1 }
    text.current = ''
    seq.current = 0
    acked.current = 0
    inflight.current = null
    inflightP.current = null
    draftStamp.current = null
    staleRetries.current = 0
    setPromptState('')
    setRestored(false)
    setTooLarge(false)
    setLedgerState('unknown') // 换目标 / 重挂：这张图的账本还没读过
    if (!project || !fileId) return
    void reconcile('mount')
    return () => {
      reconcileToken.current++
      persist(true)
      clearTimeout(retry.current)
    }
  }, [project, fileId, persist, reconcile])

  // 页面离开 / 转入后台：keepalive 冲刷；回到前台 / 重新获得焦点 / SSE 重连：对账（多标签页以后端为准）
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') persist(true)
      else void reconcile('visible')
    }
    const onFocus = () => {
      if (document.visibilityState !== 'hidden') void reconcile('focus')
    }
    const onReconnect = () => void reconcile('reconnect')
    // 确认之后后端按「草稿戳没变」清了草稿（或没清）：对账一次，输入框以后端为准
    const onAccepted = () => void reconcile('accepted')
    const flush = () => persist(true)
    window.addEventListener('pagehide', flush)
    window.addEventListener('focus', onFocus)
    window.addEventListener('mm:sse-open', onReconnect)
    window.addEventListener(AI_DRAFT_CHANGED_EVENT, onAccepted)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.removeEventListener('pagehide', flush)
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('mm:sse-open', onReconnect)
      window.removeEventListener(AI_DRAFT_CHANGED_EVENT, onAccepted)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [persist, reconcile])

  /**
   * **发送前的草稿快照**：把没写出去的文本先写成功（拿到服务器接受的写入戳），再回 `{ seq, stamp }`。
   * `stamp` 记进账本，确认之后后端按「草稿自提交后没被改过（戳没变）」清草稿——**不比文本**（提交文本是 trim 过的、
   * 草稿是原样，文本比较对不上，已被接受的指令会留在输入框里再被发一次）。没写成（后端不可用）`stamp` 为 null =
   * 不知道，确认时不动草稿。
   */
  const draftSnapshot = useCallback(async (): Promise<{ seq: number; stamp: number | null }> => {
    clearTimeout(timer.current)
    for (let i = 0; i < 4 && seq.current > acked.current; i++) await persist(false)
    const settled = seq.current === acked.current
    return { seq: seq.current, stamp: settled ? draftStamp.current : null }
  }, [persist])

  /**
   * 提交被接受之后清输入框：**只看本地有没有再动过（`seq` 没变），不比文本**。仅在发送时拿到了草稿戳
   * （后端会据此删草稿）才调用；之后后端的清理结果经 `mm:ai-draft-changed` 的对账回到输入框。
   */
  const clearIfUnchanged = useCallback((seqAtSubmit: number) => {
    if (seq.current !== seqAtSubmit) return
    text.current = ''
    draftStamp.current = null
    clearTimeout(timer.current)
    clearTimeout(retry.current)
    setPromptState('')
    setTooLarge(false)
  }, [])

  // 卸载 = 本实例退役：之后任何迟到的回调（keepalive 失败的回调、重试定时器）都不得再写。声明在其他 effect 之后，
  // 所以卸载时它们的 cleanup（含最后一次 keepalive 冲刷）先跑、这个标志最后才置位
  useEffect(() => {
    retired.current = false
    return () => {
      retired.current = true
    }
  }, [])

  /** 账本读失败后的「重试」：就是再对账一次（同一个入口） */
  const retryLedger = useCallback(() => void reconcile('focus'), [reconcile])

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
  }
}
