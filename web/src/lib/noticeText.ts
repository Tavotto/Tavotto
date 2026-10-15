/**
 * 说明条（`ui/Notice`）的文字分流：一句话放得下就留在正文，放不下的整段进「详情」。
 * 正文只放读得完的一句——错误码、HTTP 状态、后端 detail、请求 id、长路径一律折叠，带「复制」。
 */
import { ApiError, backendErrorText } from '@/lib/api'

/** 正文里一句话的上限（字符）。再长、或者带换行，就算「一段」 */
export const NOTICE_INLINE_MAX = 80

/** 一句话 → 正文；一段 → 详情。空串两边都没有 */
export function splitNoticeText(text: string | null | undefined): { inline: string | null; detail: string | null } {
  const s = (text ?? '').trim()
  if (!s) return { inline: null, detail: null }
  return s.length > NOTICE_INLINE_MAX || s.includes('\n') ? { inline: null, detail: s } : { inline: s, detail: null }
}

const BARE_HTTP = /^HTTP \d{3}$/

/**
 * 一次请求失败 → 「一句原因 + 详情」。
 * 原因：后端给了读得懂的一句就用它；只有裸的「HTTP 500」、空串、过长或多行时换成调用方给的通用原因，
 * 原话进详情。详情永远带上 HTTP 状态、后端稳定码、诊断引用（`X-Tavotto-Diagnostic-Ref`）——用户复制给我们就够查。
 */
export function describeRequestFailure(
  e: unknown,
  genericReason: string,
): { reason: string; detail: string | null } {
  const text = backendErrorText(e).trim()
  const bare = BARE_HTTP.test(text)
  const generic = bare || text === '' || splitNoticeText(text).inline === null
  const lines: string[] = []
  if (e instanceof ApiError) {
    lines.push(`HTTP ${e.status}`)
    const code = e.body?.code
    if (typeof code === 'string' && code) lines.push(code)
  }
  if (generic && text && !bare) lines.push(text)
  if (e instanceof ApiError && e.diagnosticRef) lines.push(`ref ${e.diagnosticRef}`)
  return { reason: generic ? genericReason : text, detail: lines.length > 0 ? lines.join('\n') : null }
}
