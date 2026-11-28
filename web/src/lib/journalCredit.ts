/**
 * 署名的称呼（ADR 0213 三期 / API 1.8.0）：用户自填、会展示给别的用户看的一小段字。
 * 与引擎 `contactsend.clean_credit` 同一条规则（服务端、入库工具、本机发送与读取各校验一次）：
 * NFKC + 去首尾空白后 1–32 字；不含控制字符 / 格式字符（含双向控制符）/ 行与段分隔符 / `@`；
 * 不区分大小写地不含 `://`、`www.`。合规回净化后的值，否则 null。
 */
export const CREDIT_MAX = 32

const BAD_CHARS = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u

export function cleanCredit(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const v = raw.normalize('NFKC').trim()
  if (!v || Array.from(v).length > CREDIT_MAX || v.includes('@') || BAD_CHARS.test(v)) return null
  const low = v.toLowerCase()
  if (low.includes('://') || low.includes('www.')) return null
  return v
}
