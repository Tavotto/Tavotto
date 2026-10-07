import { create } from 'zustand'
import {
  answerScriptInput,
  ApiError,
  fetchScriptAnswers,
  forgetScriptAnswer,
  listenScriptInput,
  stopScriptInput,
  updateScriptAnswer,
  type RememberedAnswer,
  type ScriptInputRequest,
} from '@/lib/api'
import { createDismissTimer } from '@/lib/dismissTimer'
import { currentProjectId } from '@/lib/session'

/**
 * 脚本里的 `input()`（ADR 0099）：等作答的问、自动回填的轻提示、记住的答案。
 *
 * - **一次只问一个**：脚本是顺序阻塞的，同一时刻最多一问在等；`queue` 只是防御（两个脚本同时在问）。
 *   对话框显示队首，答完 / 后端说不等了就出队。
 * - **项目代际**：`clear()` 换代并清空；在途响应、旧项目的事件都不落进新项目。事件那道闸由
 *   `handleServerEvent` 按 `pj` 先挡一次，这里按发请求那一刻的代际再挡一次。
 * - 提示与 stdout 片段是用户脚本的文字，组件里**只当纯文本**渲染。
 */

export const AUTOFILL_NOTICE_MS = 12_000
export const autofillDismissTimer = createDismissTimer()

/**
 * 改 / 删答案的结果。`stale` = 请求在飞时换了项目：响应属于旧项目，调用方**什么都不许做**（尤其不许
 * 接着「重新运行」——那会在新项目里跑同名脚本，Codex #680 P1）。
 */
export type AnswerChange = { status: 'ok' } | { status: 'stale' } | { status: 'error'; error: string }

/**
 * 答案改动锁（Codex #821 / 维护者复审）：改 / 删答案**一次只许一件**——批量保存整批算一件、删除一条算一件。
 * 锁归 store、不归对话框组件：关掉再打开答案管理，新挂上的对话框读到的仍是这把锁，删除在飞时照样锁着；
 * 否则两份同项目的整份快照谁后到谁赢（删除的快照盖掉刚显示的保存结果），还会重跑两次。
 *
 * `beginAnswersChange()` 拿锁（已被占 → `null`，调用方什么请求都不许发）；`saveAnswer` / `forgetAnswer`
 * 只认手里这把锁的 token，不是当前持有者就不发请求、回 `stale`；`endAnswersChange(token)` 只在 token
 * 仍是当前持有者时放锁。`clear()` 换代时把锁一并清掉——旧项目那件改动回来时 token 已不是持有者，
 * 既不会放掉新项目里的锁，它的快照也照旧按代际被丢弃。
 */
let answersLockSeq = 0
let answersLockHeld: number | null = null

export interface AutofillNotice {
  script: string
  answer: string
  token: number
}

interface ScriptInputState {
  epoch: number
  queue: ScriptInputRequest[]
  /** 提交 / 停止在途：按钮置灰，防连点 */
  busy: boolean
  error: string | null
  autofilled: AutofillNotice | null
  /** 记住的答案（`null` = 还没取过） */
  answers: Record<string, RememberedAnswer[]> | null
  location: string
  /** 有一件答案改动（批量保存 / 删除）在飞：见上方「答案改动锁」。答案管理据此锁住输入框、保存与删除 */
  answersBusy: boolean
  /** 答案管理对话框正开着的脚本 */
  managing: string | null
  /** 本页那条能答题事件流的 id（`stream.hello`）。**不随项目换代清掉**：事件流跨项目存活 */
  streamId: string | null

  onRequested: (req: ScriptInputRequest) => void
  onClosed: (id: string) => void
  onAutofilled: (script: string, answer: string) => void
  dismissAutofilled: () => void
  submit: (answer: string | null) => Promise<void>
  stop: () => Promise<void>
  loadAnswers: () => Promise<void>
  openManager: (script: string) => void
  closeManager: () => void
  /** 拿答案改动锁；已有一件在飞 → `null`（不许发请求） */
  beginAnswersChange: () => number | null
  /** 放锁；token 已不是持有者（换代清过 / 别人的）就什么都不做 */
  endAnswersChange: (token: number) => void
  saveAnswer: (token: number, script: string, index: number, answer: string) => Promise<AnswerChange>
  forgetAnswer: (token: number, script: string, index: number) => Promise<AnswerChange>
  /** `stream.hello`：记下流 id 并报一次在看哪个项目 */
  onStreamHello: (streamId: string) => void
  /** 报「这条事件流此刻在看 `pj`」；没有流 / 没有项目时什么都不做 */
  announce: (pj: string | null | undefined) => void
  clear: () => void
}

const errorText = (e: unknown): string => (e instanceof Error ? e.message : String(e))

type Get = () => ScriptInputState
type Set = (partial: Partial<ScriptInputState>) => void

/** 改 / 删一条答案：代际与发请求那一刻的项目两道闸都过了才算 `ok`，否则 `stale`。 */
async function changeAnswer(
  get: Get,
  set: Set,
  token: number,
  request: () => Promise<{ scripts: Record<string, RememberedAnswer[]>; location: string }>,
): Promise<AnswerChange> {
  // 不是锁的当前持有者（没拿锁 / 换代清过）：不发请求
  if (answersLockHeld !== token) return { status: 'stale' }
  const epoch = get().epoch
  const pj = currentProjectId()
  const stale = () => get().epoch !== epoch || currentProjectId() !== pj
  try {
    const res = await request()
    if (stale()) return { status: 'stale' }
    set({ answers: res.scripts, location: res.location })
    return { status: 'ok' }
  } catch (e) {
    if (stale()) return { status: 'stale' }
    return { status: 'error', error: errorText(e) }
  }
}

/**
 * 报「在看哪个项目」**串行**：同一时刻只有一条 listen 在路上，其间再报的只留最新的一份，等前一条回来再发。
 * 并发发出去的话后端可能按乱序处理——旧项目那条后到、盖掉新的，于是正在看的项目没人答、立即
 * `script_needs_input`，旧项目的问却白等（Codex #680 P1）。前一条回来了才发下一条，后端就按报的顺序认。
 */
let listenInFlight = false
let listenNext: { streamId: string; pj: string } | null = null

function sendListen(streamId: string, pj: string) {
  listenInFlight = true
  // 报不上（流刚断 / 后端重启）不打扰用户：重连会带来新的 hello，再报一次
  void listenScriptInput(streamId, pj)
    .catch(() => {})
    .finally(() => {
      listenInFlight = false
      const next = listenNext
      listenNext = null
      if (next) sendListen(next.streamId, next.pj)
    })
}

export const useScriptInputStore = create<ScriptInputState>((set, get) => ({
  epoch: 0,
  queue: [],
  busy: false,
  error: null,
  autofilled: null,
  answers: null,
  location: '',
  answersBusy: false,
  managing: null,
  streamId: null,

  onRequested: (req) => {
    if (get().queue.some((q) => q.id === req.id)) return
    set((s) => ({ queue: [...s.queue, req], error: null }))
  },

  onClosed: (id) => {
    const wasHead = get().queue[0]?.id === id
    set((s) => ({
      queue: s.queue.filter((q) => q.id !== id),
      ...(wasHead ? { busy: false, error: null } : {}),
    }))
  },

  onAutofilled: (script, answer) => {
    set((s) => ({ autofilled: { script, answer, token: (s.autofilled?.token ?? 0) + 1 } }))
    autofillDismissTimer.start(AUTOFILL_NOTICE_MS, () => get().dismissAutofilled())
  },

  dismissAutofilled: () => {
    autofillDismissTimer.cancel()
    set({ autofilled: null })
  },

  submit: async (answer) => {
    const head = get().queue[0]
    if (!head || get().busy) return
    const epoch = get().epoch
    set({ busy: true, error: null })
    try {
      await answerScriptInput(head.id, answer)
      if (get().epoch !== epoch) return
      // 后端随后发 `script.input_closed`；这里先出队，界面不必等那条事件
      get().onClosed(head.id)
      if (answer !== null && head.input_kind !== 'getpass') void get().loadAnswers()
    } catch (e) {
      if (get().epoch !== epoch) return
      // 这一问已经不在等了（脚本结束 / 被停）：收起，不留一个答不了的框
      if (e instanceof ApiError && e.status === 404) get().onClosed(head.id)
      else set({ busy: false, error: errorText(e) })
    }
  },

  stop: async () => {
    const head = get().queue[0]
    if (!head || get().busy) return
    const epoch = get().epoch
    set({ busy: true, error: null })
    try {
      await stopScriptInput(head.id)
      if (get().epoch !== epoch) return
      get().onClosed(head.id)
    } catch (e) {
      if (get().epoch !== epoch) return
      if (e instanceof ApiError && e.status === 404) get().onClosed(head.id)
      else set({ busy: false, error: errorText(e) })
    }
  },

  loadAnswers: async () => {
    const epoch = get().epoch
    const pj = currentProjectId()
    try {
      const res = await fetchScriptAnswers()
      if (get().epoch !== epoch || currentProjectId() !== pj) return
      set({ answers: res.scripts, location: res.location })
      // 重连 / 刷新页面时把还在等的问接回来（事件流断开期间发的那条 requested 收不到）
      for (const req of res.pending) get().onRequested(req)
    } catch {
      /* 读不到只是不显示入口；下一次作答 / 重连再取 */
    }
  },

  openManager: (script) => set({ managing: script }),
  closeManager: () => set({ managing: null }),

  beginAnswersChange: () => {
    if (answersLockHeld !== null) return null
    const token = ++answersLockSeq
    answersLockHeld = token
    set({ answersBusy: true })
    return token
  },

  endAnswersChange: (token) => {
    if (answersLockHeld !== token) return
    answersLockHeld = null
    set({ answersBusy: false })
  },

  saveAnswer: (token, script, index, answer) =>
    changeAnswer(get, set, token, () => updateScriptAnswer(script, index, answer)),

  forgetAnswer: (token, script, index) =>
    changeAnswer(get, set, token, () => forgetScriptAnswer(script, index)),

  onStreamHello: (streamId) => {
    set({ streamId })
    get().announce(currentProjectId())
  },

  announce: (pj) => {
    const streamId = get().streamId
    if (!streamId || !pj) return
    if (listenInFlight) listenNext = { streamId, pj }
    else sendListen(streamId, pj)
  },

  clear: () => {
    autofillDismissTimer.cancel()
    answersLockHeld = null
    set((s) => ({
      epoch: s.epoch + 1,
      queue: [],
      busy: false,
      error: null,
      autofilled: null,
      answers: null,
      location: '',
      answersBusy: false,
      managing: null,
    }))
  },
}))
