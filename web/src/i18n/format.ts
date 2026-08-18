/**
 * 与当前界面语言一致的数字 / 日期 / 列表格式化。
 *
 * 全部现取 `currentLocale()`，**不缓存 Intl 实例的 locale**：切语言之后同一个
 * 调用点必须给出新语言的结果。以前散在各处的 `toLocaleTimeString('zh-CN')`
 * 之类硬编码就是在这里收口的——英文界面下再出现中文日期格式是明显的漏网。
 */
import { currentLocale } from './index'

/**
 * 列表连接。中文用顿号、英文用 "a, b and c"。
 * `Intl.ListFormat` 在 Safari 14.1+ / Chrome 72+ 都有；没有就退回逗号。
 */
export function listJoin(parts: string[], type: 'conjunction' | 'disjunction' = 'conjunction'): string {
  if (parts.length <= 1) return parts[0] ?? ''
  try {
    return new Intl.ListFormat(currentLocale(), { style: 'long', type }).format(parts)
  } catch {
    return parts.join(currentLocale() === 'zh-CN' ? '、' : ', ')
  }
}

export function formatNumber(n: number, opts?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat(currentLocale(), opts).format(n)
}

/** 时间戳 → 本地时间（默认「日期 + 时分」）。 */
export function formatDateTime(
  ts: number | Date,
  opts: Intl.DateTimeFormatOptions = { dateStyle: 'short', timeStyle: 'short' },
): string {
  return new Intl.DateTimeFormat(currentLocale(), opts).format(ts)
}

/** 只要时分（版本列表这类同一天内的条目）。 */
export function formatTime(ts: number | Date): string {
  return new Intl.DateTimeFormat(currentLocale(), { timeStyle: 'short' }).format(ts)
}

/** 只要日期。 */
export function formatDate(ts: number | Date): string {
  return new Intl.DateTimeFormat(currentLocale(), { dateStyle: 'medium' }).format(ts)
}
