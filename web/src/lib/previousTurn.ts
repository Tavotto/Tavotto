/**
 * 助手来源的方案请求带上「本图对话的上一轮」（ADR 0214 §二）：用户原话 + 助手的回答（或上一张建议卡的回答与待办一行一条），
 * 让「按你的建议改」「第二条不要」落得成具体待办。纯函数；后端还会再截断与转义，这里先按总长封顶。
 *
 * 上一轮 = 这张图上**最近一次已完成**的对话轮：普通助手会话（`AiSession`，取最后一段助手文字）与方案卡（`PlanEntry`）里更新的那个。
 */
import type { PlanEntry } from '@/store/changePlanStore'
import type { AiSession } from '@/store/aiStore'

/** 客户端先封顶的总字数（后端 `MAX_PREVIOUS_TURN_CHARS` 是 2,000，这里留余量不替它判） */
export const MAX_PREVIOUS_CHARS = 4000

export interface PreviousTurn {
  user: string
  assistant: string
}

/** 一轮会话里助手说的话：最后一串连续的正文（之后没有过程行的那一段）；没有 = 空串 */
export function sessionReply(s: Pick<AiSession, 'entries'>): string {
  const tail: string[] = []
  for (let i = s.entries.length - 1; i >= 0; i--) {
    const e = s.entries[i]
    if (e.kind === 'message') tail.unshift(e.text)
    else if (tail.length > 0) break
  }
  return tail.join('\n\n').trim()
}

/** 一张已落定的方案卡对应的「助手说的话」：回答 + 待办标签（一行一条）；没有待办就列各组的标签 */
export function planReply(e: Pick<PlanEntry, 'plan'>): string {
  const p = e.plan
  if (!p) return ''
  const rows = p.todos?.length ? p.todos.map((t) => t.label) : p.groups.map((g) => g.label)
  return [p.reply ?? '', ...rows].filter(Boolean).join('\n')
}

const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n) : s)

/** 这张图上最近一次完成的对话轮；一轮都没有 = undefined（请求里不带这个字段） */
export function previousTurnOf(panelId: string, sessions: readonly AiSession[], plans: readonly PlanEntry[]): PreviousTurn | undefined {
  type Cand = { at: number; user: string; assistant: string }
  const cands: Cand[] = []
  for (const s of sessions) {
    if (s.panelId !== panelId || s.status !== 'done') continue
    const assistant = sessionReply(s)
    if (assistant) cands.push({ at: s.finishedAt ?? s.startedAt, user: s.prompt, assistant })
  }
  for (const e of plans) {
    if (e.panelId !== panelId || e.from !== 'assistant' || !e.instruction) continue
    if (e.status === 'generating' || e.status === 'failed' || e.status === 'handoff') continue
    const assistant = planReply(e)
    if (assistant) cands.push({ at: e.createdAt, user: e.instruction, assistant })
  }
  const last = cands.sort((a, b) => b.at - a.at)[0]
  if (!last) return undefined
  const user = clip(last.user, 1000)
  return { user, assistant: clip(last.assistant, MAX_PREVIOUS_CHARS - user.length) }
}
