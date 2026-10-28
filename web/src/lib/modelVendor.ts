/**
 * 型号 id → 厂商标志键（`BrandLogo` 的键）。只按型号名的前缀认：认得出才返回，认不出返回 null，
 * 由调用方退到连接图标 / 执行器图标——不猜（`custom-llm-7` 不是任何一家）。
 * 带路由前缀的（`deepseek/deepseek-chat`、`openrouter/anthropic/claude-3`）取最后一段再认。
 */
const RULES: readonly (readonly [RegExp, string])[] = [
  [/^deepseek/, 'deepseek'],
  [/^(claude|sonnet|opus|haiku)(?![a-z])/, 'claude'],
  [/^(gpt|chatgpt|codex|o\d+(?![a-z0-9.]))/, 'openai'],
  [/^(gemini|gemma)/, 'gemini'],
  [/^(kimi|moonshot)/, 'kimi'],
  [/^(glm|chatglm)/, 'zhipu'],
  [/^(qwen|qwq)/, 'qwen'],
  [/^(minimax|abab)/, 'minimax'],
]

export function modelBrandKey(model: string | null | undefined): string | null {
  const id = (model ?? '').trim().toLowerCase().split('/').pop() ?? ''
  if (!id) return null
  for (const [re, key] of RULES) if (re.test(id)) return key
  return null
}
