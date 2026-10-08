import type { Severity } from '@/lib/profile'

/**
 * 画布等级标记（`IssueOverlay` 的 pin）的配色：底色 + 上面那个 10px 项数的字色，**成对**给出 token 名。
 * 字是要读的（≥4.5:1），所以配对在 `tokenContrast.test` 里逐对量，不按单个 token 记。
 *
 * warn 不用锚点 `warn` 当底：锚点 #b07400 对白字只有 ≈3.9:1（index.css 早就写明「warn 锚点不当字底，字一律
 * 用 warn-content」），换 ink 字也只有 ≈4.4:1。改用 `warn-content` 当底 + 白字（Codex #832）——仍是警告色相，
 * 只是深一档；danger 锚点与 ink-3 对白本来就 ≥4.5:1。
 */
export const PIN_COLORS: Record<Severity, { fill: string; text: string }> = {
  error: { fill: 'danger', text: 'surface' },
  warn: { fill: 'warn-content', text: 'surface' },
  not_verifiable: { fill: 'ink-3', text: 'surface' },
  suggestion: { fill: 'ink-3', text: 'surface' },
}

export const tokenVar = (name: string) => `var(--color-${name})`
