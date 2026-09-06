/**
 * 「问题在界面上怎么说」的**唯一实现**（与 `readinessText.ts`、`profileText.ts`
 * 同一条纪律）。
 *
 * 三条硬规矩：
 *
 * 1. **普通界面里不出现内部标识**。`axes_0.xlabel` / `axes_0.lines_1` /
 *    对象 id / 文件路径一个都不显示——主语说人话（「X 轴标题」「图例」
 *    「标注文字」），精确名词只出现在每行的「技术详情」里，默认收起。
 * 2. **等级不只靠颜色**。图标形状 + 文字标签 + 颜色三重表达；灰度屏与色觉
 *    障碍下同样读得出来。
 * 3. **不存翻译后的字符串**。这里全是现算的读法，问题本身存的是 message key
 *    与结构化参数（`ValidationIssue.message`）。
 */
import { Lightbulb, ShieldQuestion, TriangleAlert } from 'lucide-react'
import { formatMessage, t as translate } from '@/i18n'
import { engineLabel, roleName } from '@/components/inspector/roles/registry'
import type { Severity } from './profile'
import type { ValidationIssue } from './validation'

const pr = (key: string, values?: Record<string, unknown>): string =>
  translate(`problems.${key}`, { ns: 'errors', ...(values ?? {}) })

/** 四个等级共用的图标表。**问题面板与导出面板同一份**（图标一致的看护点）。 */
export const SEVERITY_ICON: Record<Severity, typeof TriangleAlert> = {
  error: TriangleAlert,
  warn: TriangleAlert,
  not_verifiable: ShieldQuestion,
  suggestion: Lightbulb,
}

export const severityLabel = (s: Severity): string =>
  pr(`severity.${s === 'not_verifiable' ? 'notVerifiable' : s}`)

/**
 * 短标题：一眼看出是哪一类问题。**按 rule code 查**——措辞改了不影响判据，
 * 而 rule code 是稳定的（golden vectors 与 proof report 认的就是它）。
 * 没登记的 code 退回完整成文，不显示 code 本身。
 */
export function issueTitle(issue: ValidationIssue): string {
  const title = translate(`problems.title.${issue.ruleCode}`, { ns: 'errors', defaultValue: '' })
  return title || formatMessage(issue.message)
}

/** 完整成文（行内 title、技术详情、导出留档共用）。 */
export const issueDetailText = (issue: ValidationIssue): string => formatMessage(issue.message)

/**
 * 「当前值 → 要求」。两个数字都取自**这一条命中自己的** message 参数
 * （聚合项那份属于最糟的一次，拿来描述别的对象会说出假数字）。
 */
export interface IssueValues {
  current: string | null
  expected: string | null
}

/** 每条规则的当前值 / 要求分别读哪个参数、带什么单位。没登记的就不显示数字。 */
const VALUES: Record<string, { current?: string; expected?: string; unit?: string; cmp?: string }> = {
  'font-too-small': { current: 'effective', expected: 'min', unit: 'pt', cmp: 'atLeast' },
  'font-below-absolute-floor': { current: 'effective', expected: 'floor', unit: 'pt', cmp: 'above' },
  'font-too-large': { current: 'effective', expected: 'max', unit: 'pt', cmp: 'atMost' },
  'legend-font-size': { current: 'effective', expected: 'min', unit: 'pt', cmp: 'atLeast' },
  'line-width-off-preset': { current: 'effective', expected: 'presets', unit: 'pt' },
  'raster-dpi': { current: 'dpi', expected: 'min', unit: 'dpi', cmp: 'atLeast' },
  'page-width': { current: 'actual', expected: 'want', unit: 'mm' },
  'page-aspect': { current: 'ratio', expected: 'allowed' },
  'font-family-substituted': { current: 'family', expected: 'want' },
  'text-weight-policy': { current: 'got', expected: 'want' },
  'tick-direction': { current: 'got', expected: 'want' },
  'tick-label-count': { current: 'count', expected: 'max' },
  'discouraged-colormap': { current: 'cmap', expected: 'recommended' },
  'unapplied-override': { current: 'count' },
  'cjk-fallback-missing': { current: 'family' },
  'axis-label-format': { current: 'label', expected: 'want' },
}

export function issueValues(issue: ValidationIssue): IssueValues {
  const spec = VALUES[issue.ruleCode]
  if (!spec) return { current: null, expected: null }
  // 画布标注那两条用的是 `size` 而不是 `effective`（同一条规则、两种主语）
  const params = (issue.message.values ?? {}) as Record<string, unknown>
  const read = (key: string | undefined): string | null => {
    if (!key) return null
    const raw = params[key] ?? (key === 'effective' ? params.size : undefined)
    return raw == null || raw === '' ? null : String(raw)
  }
  const unit = spec.unit ? pr(`unit.${spec.unit}`) : ''
  const withUnit = (v: string | null) => (v == null ? null : unit ? `${v}${unit}` : v)
  const currentRaw = read(spec.current)
  const expectedRaw = read(spec.expected)
  const expected = withUnit(expectedRaw)
  return {
    current: withUnit(currentRaw),
    expected:
      expected == null
        ? null
        : spec.cmp
          ? pr(`cmp.${comparatorKey(spec.cmp, currentRaw, expectedRaw)}`, { value: expected })
          : expected,
  }
}

/** 有「就在边界上」这一档说法的比较词。其余（没有 cmp 的规则）原样显示。 */
const BOUNDARY_CMP = new Set(['above', 'atLeast', 'atMost'])

/**
 * 「要求」那半句用哪个说法（审计 T41）。
 *
 * 边界的包含性**只有一个出处**：上面那张 `VALUES` 表里的 `cmp`，它照抄两侧
 * 求值器的判据形状——`font-below-absolute-floor` 是 `eff <= floor`（所以要求
 * 是**大于**，等于不算通过），`font-too-small` 是 `eff < strict`（所以要求是
 * **≥**）。2026-09-06 用真实样本在 `engine/preflight.run()` 上逐个跑过 7.99 /
 * 7.995 / 8.00 / 8.005：8.00 命中绝对下限、8.005 通过，等号确实不含在内。
 *
 * 剩下的问题是**显示舍入**：图内文字的 `effective` 是 `toFixed(2)` 的结果，
 * 阈值却是 `%g`，于是 7.996pt 与 8.000pt 都显示成 `8.00`，配上「要求 大于 8pt」
 * 读起来像自相矛盾（审计原话：「8.00 需大于 8.00」）。两个数**在屏幕上相等**
 * 时换一句把这层说破的话，措辞对两种成因都成立：
 *
 *   * `above` —— 说的是**规则**（正好等于也不算通过），值究竟是不是正好等于
 *     不影响这句话的真假；
 *   * `atLeast` / `atMost` —— 判据含等号，既然它被判成违规、显示出来又相等，
 *     那真值就必然严格低于 / 高于阈值，只能是显示舍入。
 *
 * 判据一个字没动，动的只是这句话。
 */
function comparatorKey(cmp: string, current: string | null, expected: string | null): string {
  return BOUNDARY_CMP.has(cmp) && looksEqual(current, expected) ? `${cmp}AtBoundary` : cmp
}

/**
 * 这两个数**在界面上长得一样**吗。比的是即将显示出去的那两个字符串解析回来的
 * 数，不是真值——真值这一层根本拿不到（消息参数已经是 `toFixed(2)` / `%g` 的
 * 结果，`technicalDetails` 也只留了两位）。而要判的恰恰就是读者看到的那两个数：
 * `8.00` 与 `8` 是两个不同的字符串、同一个数。
 */
function looksEqual(current: string | null, expected: string | null): boolean {
  if (current == null || expected == null) return false
  const a = Number(current)
  const b = Number(expected)
  return Number.isFinite(a) && Number.isFinite(b) && a === b
}

/**
 * 一条规则的「要求」那半句，给**设置页**复用（审计 T41）：规范里填了 8，检查
 * 时到底是「≥ 8pt」还是「大于 8pt」——边界的包含性只有 `VALUES` 这一张表说了
 * 算，设置页不许照着判据再抄一遍（抄一遍就有了第二份规则，而它会在下一次改
 * 判据时安静地过期）。规则没登记比较词时返回 null，界面那一行就不出现。
 */
export function ruleExpectation(ruleCode: string, value: number | string): string | null {
  const spec = VALUES[ruleCode]
  if (!spec?.cmp) return null
  const unit = spec.unit ? pr(`unit.${spec.unit}`) : ''
  return pr(`cmp.${spec.cmp}`, { value: `${value}${unit}` })
}

/**
 * 主语说人话。
 *
 * 图内元素取引擎给的标签（「X 轴标题」「图例」），过 `engineLabel()` 换成
 * 界面语言；拿不到就退到角色名，再拿不到才说面板名。**任何一档都不吐 gid。**
 */
export function subjectName(issue: ValidationIssue): string {
  const s = issue.subject
  if (s.kind === 'page') return pr('subjectPage')
  if (s.kind === 'element') {
    if (s.elementLabel) return engineLabel(s.elementLabel)
    if (s.elementRole) return roleName(s.elementRole)
    return s.objectName ?? pr('subjectElement')
  }
  if (s.objectName) return s.objectName
  switch (s.objectType) {
    case 'text':
      return pr('subjectText')
    case 'arrow':
      return pr('subjectArrow')
    case 'shape':
      return pr('subjectShape')
    case 'panel':
      return pr('subjectPanel')
    default:
      return pr('subjectObject')
  }
}

/**
 * 技术详情（默认收起，排障用）。**只有这里**出现 gid、对象 id 与量化字段。
 * 每行 `名字：值`，值走 JSON 以免把对象拼成 `[object Object]`。
 */
export function technicalDetailLines(issue: ValidationIssue): string[] {
  const out = [`${pr('techRule')}: ${issue.ruleCode}`]
  if (issue.objectRef.objectId) out.push(`${pr('techObject')}: ${issue.objectRef.objectId}`)
  if (issue.objectRef.gid) out.push(`${pr('techElement')}: ${issue.objectRef.gid}`)
  if (issue.propertyPath) out.push(`${pr('techProperty')}: ${issue.propertyPath}`)
  for (const [k, v] of Object.entries(issue.technicalDetails)) {
    out.push(`${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
  }
  return out
}

/** 「修复」按钮上写什么。`user_choice` 那档要说清还要选一下。 */
export const fixLabel = (issue: ValidationIssue): string =>
  pr(issue.fixKind === 'user_choice' ? 'fixChoose' : 'fix')

/**
 * 屏幕阅读器读到的一整句：**等级 + 对象 + 问题 + 要求 + 这颗按钮会做什么**。
 * 最后一段不能省——这一行是个按钮，光念完问题不说动作，用户不知道按下去会
 * 发生什么（读屏用户看不到那个悬停才出现的「定位」字样）。
 */
export function issueAriaLabel(issue: ValidationIssue): string {
  const v = issueValues(issue)
  return [
    severityLabel(issue.severity),
    subjectName(issue),
    issueTitle(issue),
    v.current && v.expected ? pr('ariaValues', { current: v.current, expected: v.expected }) : null,
    pr('ariaLocate'),
  ]
    .filter(Boolean)
    .join('，')
}
