/**
 * 「改脚本做剩下的」交给改图助手的指令 = 用户原话 + 一段**确定性**的上下文（不经模型）：
 * 哪些已经在图内改好（不必再改）/ 用户还没应用的图内修改（别在脚本里重复）、剩下只需做什么。
 * 只列勾选的组；没有一处图内修改（整条都要改脚本）时就是原话，一个字不加。
 */
import type { ChangePlan, ChangePlanChange } from '@/lib/api'

const MAX_LINES = 20
const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}…` : s)

/** 属性新值的一小段文字（只给人 / Agent 看，不回写） */
export function valueText(v: unknown): string {
  if (typeof v === 'string') return clip(v, 40)
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    return clip(JSON.stringify(v) ?? String(v), 40)
  } catch {
    return '…'
  }
}

export function scriptHandoffPrompt(
  instruction: string,
  plan: Pick<ChangePlan, 'groups'> | undefined,
  opts: { applied: boolean; unchecked: Record<string, boolean>; labelOf: (gid: string) => string | undefined },
): string {
  const changes: ChangePlanChange[] = (plan?.groups ?? []).filter((g) => !opts.unchecked[g.key]).flatMap((g) => g.changes)
  if (!changes.length) return instruction
  const lines = changes.slice(0, MAX_LINES).map((c) => `- ${opts.labelOf(c.target_gid) || c.target_gid} · ${c.prop} → ${valueText(c.after)}`)
  if (changes.length > MAX_LINES) lines.push(`- （另有 ${changes.length - MAX_LINES} 处）`)
  const head = opts.applied
    ? '下面这些已经在图内改好（不必再改）：'
    : '下面这些图内修改用户还没应用，不要在脚本里重复它们（用户应用后就会生效）：'
  return [instruction, '', head, ...lines, '', `只需做：${instruction}（上面这些图内修改已经覆盖的部分除外）`].join('\n')
}
