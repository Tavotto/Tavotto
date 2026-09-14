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

/** 数字与单位之间要不要留空：字母单位留一个空格，`%` / `°` 贴着数字（SI 的写法） */
const TIGHT_UNITS = new Set(['%', '°'])

/**
 * 「数值 + 单位」全站一个写法（2026-09-14 审计 S11）。
 *
 * 此前两套并存：i18n 里 `{{x}}pt` 21 处无空格、`{{x}} mm` 22 处有空格，问题面板同一屏
 * 「7.33pt」与「1.87 mm」；en-US 又是「7.33 pt」。字母单位（pt / mm / px / ppi / cm）与
 * 数字之间一个普通空格（不用 U+00A0：用例、搜索、复制都认普通空格），`%` / `°` 不空。
 * 数字框里的单位是框内独立的一列（`NumberField unit`），不经这里。
 */
export function formatQuantity(value: string | number, unit: string): string {
  const v = typeof value === 'number' ? String(value) : value
  if (!unit) return v
  return TIGHT_UNITS.has(unit) ? `${v}${unit}` : `${v} ${unit}`
}

/**
 * 「宽 × 高 + 单位」全站一个写法（2026-09-14 二审 A8，S11 的兄弟）：`×` 两侧各一个空格，
 * 单位前一个空格——与 Finder / 预览 的「1890 × 1361」同一读法。此前 `{{w}}×{{h}} mm` 十处、
 * `{{w}} × {{h}} mm` 五处、`{{w}}×{{h}}cm` 一处并存，同一屏里素材卡与导出对话框两种写法。
 * i18n 字串里直接写 `{{w}} × {{h}} mm`（resources.test 守着），代码里拼字符串走这里。
 */
export function formatSize(w: string | number, h: string | number, unit: string): string {
  return formatQuantity(`${w} × ${h}`, unit)
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
