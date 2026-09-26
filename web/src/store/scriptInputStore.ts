import { create } from 'zustand'
import {
  answerScriptInput,
  ApiError,
  fetchScriptAnswers,
  forgetScriptAnswer,
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
  /** 答案管理对话框正开着的脚本 */
  managing: string | null

  onRequested: (req: ScriptInputRequest) => void
  onClosed: (id: string) => void
  onAutofilled: (script: string, answer: string) => void
  dismissAutofilled: () => void
  submit: (answer: string | null) => Promise<void>
  stop: () => Promise<void>
  loadAnswers: () => Promise<void>
  openManager: (script: string) => void
  closeManager: () => void
  saveAnswer: (script: string, index: number, answer: string) => Promise<string | null>
  forgetAnswer: (script: string, index: number) => Promise<string | null>
  clear: () => void
}

const errorText = (e: unknown): string => (e instanceof Error ? e.message : String(e))

export const useScriptInputStore = create<ScriptInputState>((set, get) => ({
  epoch: 0,
  queue: [],
  busy: false,
  error: null,
  autofilled: null,
  answers: null,
  location: '',
  managing: null,

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

  saveAnswer: async (script, index, answer) => {
    const epoch = get().epoch
    try {
      const res = await updateScriptAnswer(script, index, answer)
      if (get().epoch === epoch) set({ answers: res.scripts, location: res.location })
      return null
    } catch (e) {
      return errorText(e)
    }
  },

  forgetAnswer: async (script, index) => {
    const epoch = get().epoch
    try {
      const res = await forgetScriptAnswer(script, index)
      if (get().epoch === epoch) set({ answers: res.scripts, location: res.location })
      return null
    } catch (e) {
      return errorText(e)
    }
  },

  clear: () => {
    autofillDismissTimer.cancel()
    set((s) => ({
      epoch: s.epoch + 1,
      queue: [],
      busy: false,
      error: null,
      autofilled: null,
      answers: null,
      location: '',
      managing: null,
    }))
  },
}))
