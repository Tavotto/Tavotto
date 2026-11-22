/**
 * 一套样式的「一行摘要」（2026-11-20）：样式下拉的每一项、画布层名字下面那一行共用。
 *
 * 只读 `StyleProfileData` 里已有的键（不造新键、不判规范）：字体、轴标题 / 刻度字号、轴标题加粗、
 * 线宽。样式没管的项直接不说，**不拿示例默认值冒充**；一项都没有就是 null（调用方不摆那一行）。
 * 示例：`Times New Roman · 9/8 pt · 轴标题加粗 · 线 0.75 pt`。
 */
import { t as translate } from '@/i18n'
import { figureFontChoice } from './figureFontChoice'
import { optionLabel } from '@/store/actions'
import { weightIsBold } from './typography'

const sm = (key: string, values?: Record<string, unknown>) =>
  translate(`stylePanel.summary.${key}`, { ns: 'workspace', ...(values ?? {}) })

const read = (data: unknown, path: string[]): unknown => {
  let cur: unknown = data
  for (const k of path) {
    if (!cur || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[k]
  }
  return cur
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** 数字去掉多余的 0：9 → 「9」，0.75 → 「0.75」，12.3456 → 「12.35」 */
const fmt = (n: number): string => String(Math.round(n * 100) / 100)

export function styleSummaryText(data: Record<string, unknown> | null | undefined): string | null {
  if (!data) return null
  const parts: string[] = []

  // 字体：轴标题 → 刻度 → 标题 → 图例里第一个写了的；候选列表取第一个（matplotlib 也先试它）
  for (const role of ['axis_label', 'ticks', 'title', 'legend']) {
    const raw = read(data, ['element', role, 'fontfamily'])
    const primary = figureFontChoice(raw)?.primary ?? raw
    const first = Array.isArray(primary) ? primary[0] : primary
    if (typeof first === 'string' && first.trim()) {
      parts.push(optionLabel('fontfamily', first.trim()))
      break
    }
  }

  // 字号：轴标题 / 刻度（相同就只写一个）；都没写才退到标题 / 图例
  const sizeOf = (role: string) => num(read(data, ['element', role, 'fontsize']))
  let sizes = [sizeOf('axis_label'), sizeOf('ticks')].filter((n): n is number => n !== null)
  if (!sizes.length) sizes = [sizeOf('title'), sizeOf('legend')].filter((n): n is number => n !== null)
  const uniq = sizes.filter((n, i) => sizes.indexOf(n) === i)
  if (uniq.length) parts.push(sm('size', { sizes: uniq.map(fmt).join('/') }))

  // 加粗：轴标题 → 标题
  if (weightIsBold(read(data, ['element', 'axis_label', 'weight']))) parts.push(sm('axisBold'))
  else if (weightIsBold(read(data, ['element', 'title', 'weight']))) parts.push(sm('titleBold'))

  // 线：数据线宽，没写就用边框线宽（同一个「线」字：默认样式只管边框线宽，摘要仍读作「线 0.75 pt」）
  const line = num(read(data, ['element', 'line', 'linewidth']))
  const frame = num(read(data, ['element', 'axes', 'spine_linewidth']))
  if (line !== null) parts.push(sm('line', { width: fmt(line) }))
  else if (frame !== null) parts.push(sm('frame', { width: fmt(frame) }))

  return parts.length ? parts.join(' · ') : null
}
