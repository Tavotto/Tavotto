/**
 * 「✦ 使用你的 AI 来修复」（智能修复主线，ADR 0148）的前端状态：按准备卡条目记「这张卡点过修复之后走到哪了」。
 *
 * 流程（维护者裁决：每次失败都出这颗按钮，点它——）：
 * * 没连 AI：打开设置的 AI 页（`setSettingsOpen(true, 'ai')`），记成 `awaiting_connect`；连好回来
 *   （`aiStore.caps` 里出现可用的 AI）就自动接着进入修复，用户不用再点一次；设置关了还没连上就放弃等待；
 * * 已连 AI：调 `startAiFix`（`POST /api/ai/fix`）。AI 环境修复任务本身在后续 PR；契约先定——后端现在一律回
 *   `{ok:false, reason:'ai_fix_unavailable'}`，capabilities 的 `ai_fix_available` 也是 false。两种情况都落成
 *   `unavailable`：卡片说「这个问题 AI 暂时还不会修」+ 主按钮「发送反馈」，按钮本身不隐藏。
 *
 * 不存任何用户内容：只有条目 key、会话 id、脚本相对路径与阶段名；不持久化。
 */
import { create } from 'zustand'
import { requestAiFix, usableAgents } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'

export type AiFixPhase = 'awaiting_connect' | 'starting' | 'unavailable' | 'running'

export interface AiFixRef {
  session_id: string
  script: string
  /** 点的那一刻报告里当前尝试的 id：之后又跑了一次（新尝试）就不再沿用旧的「暂时不会修」 */
  attemptId: string | null
}

export interface AiFixEntry {
  phase: AiFixPhase
  ref: AiFixRef
}

interface AiFixState {
  entries: Record<string, AiFixEntry>
  /** 点「✦ 使用你的 AI 来修复」：没连 AI 去设置里连，已连就开始修复 */
  request: (key: string, ref: AiFixRef) => Promise<void>
  /** 已连 AI：开始修复（`ai_fix_available === false` 或端点回 `ai_fix_unavailable` 都落成 `unavailable`） */
  startAiFix: (key: string, ref: AiFixRef) => Promise<void>
  clear: (key: string) => void
  reset: () => void
}

export const useAiFixStore = create<AiFixState>((set, get) => ({
  entries: {},

  request: async (key, ref) => {
    let caps = useAiStore.getState().caps
    if (!caps) {
      // 还没探测过本机的 AI：先探测一次再判断（失败当作没连）
      try {
        await useAiStore.getState().loadCaps()
      } catch {
        /* 探测失败：按没连处理 */
      }
      caps = useAiStore.getState().caps
    }
    if (usableAgents(caps).length === 0) {
      set((s) => ({ entries: { ...s.entries, [key]: { phase: 'awaiting_connect', ref } } }))
      useUiStore.getState().setSettingsOpen(true, 'ai')
      return
    }
    await get().startAiFix(key, ref)
  },

  startAiFix: async (key, ref) => {
    const put = (phase: AiFixPhase) => set((s) => ({ entries: { ...s.entries, [key]: { phase, ref } } }))
    put('starting')
    if (useAiStore.getState().caps?.ai_fix_available === false) {
      put('unavailable')
      return
    }
    try {
      const res = await requestAiFix({ session_id: ref.session_id, script: ref.script })
      put(res.ok ? 'running' : 'unavailable')
    } catch {
      // 请求本身失败（断线 / 老后端没有这个端点）：同样落到反馈，不让用户卡在一个转圈的按钮上
      put('unavailable')
    }
  },

  clear: (key) =>
    set((s) => {
      if (!(key in s.entries)) return s
      const { [key]: _drop, ...rest } = s.entries
      return { entries: rest }
    }),

  reset: () => set({ entries: {} }),
}))

/** 此刻还在等用户连 AI 的条目 */
const waiting = () => Object.entries(useAiFixStore.getState().entries).filter(([, e]) => e.phase === 'awaiting_connect')

// 连好 AI 回来自动接着修：caps 里出现可用的 AI，就把所有在等的条目接着走 startAiFix
useAiStore.subscribe((s, prev) => {
  if (s.caps === prev.caps || usableAgents(s.caps).length === 0) return
  for (const [key, e] of waiting()) void useAiFixStore.getState().startAiFix(key, e.ref)
})

// 设置关了还没连上：放弃等待（之后偶然连上 AI 不该冷不丁开始修）。连上了的那条已由上面的订阅接走
useUiStore.subscribe((s, prev) => {
  if (!prev.settingsOpen || s.settingsOpen) return
  for (const [key] of waiting()) useAiFixStore.getState().clear(key)
})
