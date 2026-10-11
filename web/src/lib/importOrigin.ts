/**
 * Import Origin Resolver 在界面上的**翻译层**（PR6，ADR 0114 / 0115）。
 *
 * 这里没有一条判据：「这个 import 是谁提供的 / 该装哪个包 / 能不能装」全部是后端事实
 * （`depplan.origins[]`、`prepsession` 检查项的 `detail.origins`、`envadvice` 的覆盖度 `detail`、进度终态的 `dist_reasons`），
 * 前端只做三件事：
 *
 * * 读——把后端给的事实读成界面要的形状（`originFactsOf` / `partitionOrigins`），读不出就当没有，不猜；
 * * 译——每个闭集原因码一句人话（`originReasonText` / `coverageCodeText` / `distFailureText`），中英两语，只进折叠的「详情」；
 * * 命名——五种展示态（已就绪 / 发现更合适的环境 / 可以自动修复 / 需要确认 / 尚无法确定）只是卡片与对话框已有状态的**名字**
 *   （`originViewOfCard` / `originViewOfOffer`），用来给 `data-origin-view` 与测试，不另立一套状态机。
 *
 * 绝不出现的东西（`importOrigin.structure.test.ts` 逐条钉着）：stdlib / 第三方 / curated 名单、import 名到包名的映射、
 * 对 import 名的任何字符串切分 / 正则、对 `importlib` / `site-packages` 的描述。预填只来自 `origins[].suggested_distribution`，
 * 是不是「纯粹的表外名」后端已经判过。
 */
import {
  DIST_FAILURE_REASONS,
  ENV_COVERAGE_DETAIL_CODES,
  JOINT_ORIGIN_REASONS,
  type DependencyPreparationOffer,
  type DistFailureReason,
  type EnvCoverageDetail,
  type EnvCoverageDetailCode,
  type JointDependencyPlan,
  type JointOrigin,
  type JointOriginReason,
  type PreparationOriginFacts,
  type PreparationReport,
} from '@/lib/api'
import { t as translate } from '@/i18n'

/** 五种展示态（用户硬性要求的「一句话 + 一个主按钮」各自对应的那一档） */
export const ORIGIN_VIEWS = ['ready', 'better_env', 'auto_fix', 'needs_confirm', 'undetermined'] as const
export type OriginView = (typeof ORIGIN_VIEWS)[number]

//: 文案键写成字面量（`og` 的前缀是 `prep.origin.`）：`importOrigin.text.test.ts` 逐表核对两种语言都有、每个闭集码都有一句
const og = (key: string, v?: Record<string, unknown>) => translate(`prep.origin.${key}`, { ns: 'workspace', ...v })

const REASON_TEXT: Record<JointOriginReason, string> = {
  distribution_unresolved: 'reason.distribution_unresolved',
  module_origin_ambiguous: 'reason.module_origin_ambiguous',
  editable_dependency_not_reproducible: 'reason.editable_dependency_not_reproducible',
  installed_source_not_reproducible: 'reason.installed_source_not_reproducible',
  conda_package_not_pypi: 'reason.conda_package_not_pypi',
  unverified: 'reason.unverified',
  module_import_failed_in_target_environment: 'reason.module_import_failed_in_target_environment',
}

const COVERAGE_TEXT: Record<EnvCoverageDetailCode, string> = {
  module_not_found_in_user_environment: 'coverage.module_not_found_in_user_environment',
  module_import_failed_in_user_environment: 'coverage.module_import_failed_in_user_environment',
  module_missing_in_target_environment: 'coverage.module_missing_in_target_environment',
  module_import_failed_in_target_environment: 'coverage.module_import_failed_in_target_environment',
  environment_not_checked: 'coverage.environment_not_checked',
  environment_unusable: 'coverage.environment_unusable',
}

const DIST_TEXT: Record<DistFailureReason, string> = {
  distribution_not_installed: 'dist.distribution_not_installed',
  distribution_version_mismatch: 'dist.distribution_version_mismatch',
  distribution_does_not_provide_import: 'dist.distribution_does_not_provide_import',
  distribution_unverifiable: 'dist.distribution_unverifiable',
}

const known = <T extends string>(set: readonly T[], code: string): code is T => (set as readonly string[]).includes(code)

/** 一个来源未定的原因 → 一句人话（折叠详情里用）。认不出的新原因码（新后端、旧前端）落到通用的一句，不原样甩代码 */
export function originReasonText(reason: string): string {
  return og(known(JOINT_ORIGIN_REASONS, reason) ? REASON_TEXT[reason] : 'reason.unknown')
}

/** 一个覆盖度 detail 码 → 一句人话。同样，认不出的落到通用的一句 */
export function coverageCodeText(code: string): string {
  return og(known(ENV_COVERAGE_DETAIL_CODES, code) ? COVERAGE_TEXT[code] : 'coverage.unknown')
}

/** 装后核验的一个原因码 → 一句人话 */
export function distFailureText(reason: string): string {
  return og(known(DIST_FAILURE_REASONS, reason) ? DIST_TEXT[reason] : 'dist.unknown')
}

/* ------------------------------------------------------------------ 读后端事实 */

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/**
 * 准备会话报告里的来源事实（`checks_of` 的 `dependencies` 项的 `detail.origins`）：只有计数与闭集原因码，没有名字。
 * 没有 / 形状不对 / 计数为 0 → null（与「没有来源问题」同一个意思，界面不说话）。
 */
export function originFactsOf(report: PreparationReport | null | undefined): PreparationOriginFacts | null {
  const detail = report?.checks.find((c) => c.id === 'dependencies')?.detail
  const raw = isObject(detail) ? detail.origins : undefined
  if (!isObject(raw)) return null
  const count = typeof raw.count === 'number' && raw.count > 0 ? raw.count : 0
  if (count === 0) return null
  const byReason: Record<string, number> = {}
  if (isObject(raw.by_reason)) {
    for (const [reason, n] of Object.entries(raw.by_reason)) if (typeof n === 'number' && n > 0) byReason[reason] = n
  }
  const confirmable = typeof raw.confirmable === 'number' && raw.confirmable > 0 ? Math.min(raw.confirmable, count) : 0
  return { count, by_reason: byReason, ...(confirmable ? { confirmable } : {}) }
}

/**
 * 联合计划里来源未定的条目，按**后端有没有给预填**分两堆：带 `suggested_distribution` 的才有输入框（后端只收这一类的 `user_specified`，
 * 别的提交会被 `dependency_install_not_allowed` 拒绝），其余只解释。这里只读那个字段，不看 import 名。
 */
export function partitionOrigins(plan: Pick<JointDependencyPlan, 'origins'> | null | undefined): {
  specifiable: JointOrigin[]
  explainOnly: JointOrigin[]
} {
  const specifiable: JointOrigin[] = []
  const explainOnly: JointOrigin[] = []
  for (const o of plan?.origins ?? []) (o.suggested_distribution ? specifiable : explainOnly).push(o)
  return { specifiable, explainOnly }
}

/** 覆盖度 detail 里要说出口的那些（有码、形状对） */
export function coverageDetails(detail: EnvCoverageDetail[] | undefined): EnvCoverageDetail[] {
  return (detail ?? []).filter((d) => isObject(d) && typeof d.code === 'string')
}

/* ------------------------------------------------------------------ 提交形状 */

/** 对话框里的草稿：从哪个脚本的计划来、哪些 import 有输入框（顺序 = 后端给的顺序）、当前输入 */
export interface OriginDraft {
  script: string
  names: string[]
  values: Record<string, string>
}

/** 草稿的初始值 = 后端的预填（原样，不改写、不补全） */
export function draftFromOffer(offer: DependencyPreparationOffer): OriginDraft {
  const { specifiable } = partitionOrigins(offer.plan)
  return {
    script: offer.script,
    names: specifiable.map((o) => o.import_name),
    values: Object.fromEntries(specifiable.map((o) => [o.import_name, o.suggested_distribution ?? ''])),
  }
}

/**
 * `createJointDependencyPlan` 的 `user_specified`：只收有输入框、且用户没清空的那些，值只去掉首尾空白，其余原样交给后端
 * （语法 / 版本约束 / 资格都归 `depresolve.from_user_input` 与 `depplan`，前端不预判）。
 */
export function userSpecifiedBody(draft: OriginDraft): Record<string, string> {
  const out: Record<string, string> = {}
  for (const name of draft.names) {
    const value = (draft.values[name] ?? '').trim()
    if (value) out[name] = value
  }
  return out
}

/* ------------------------------------------------------------------ 五种展示态的名字 */

/**
 * 准备卡的状态（`prepView().state`）→ 五种展示态里的哪一档；不属于这五档的（运行中 / 画好了 / 要选运行目录…）回 null。
 * 这只是命名：卡片的状态由 `preparationText.ts` 按后端报告选，这里不重新判。
 */
export function originViewOfCard(state: string): OriginView | null {
  switch (state) {
    case 'ready':
      return 'ready'
    case 'env_choice':
      return 'better_env'
    case 'deps':
    case 'deps_runtime':
    case 'env_blocked':
      return 'auto_fix'
    case 'origin_confirm':
      return 'needs_confirm'
    case 'origin_unknown':
    case 'deps_unknown':
      return 'undetermined'
    default:
      return null
  }
}

/**
 * 依赖授权框的载荷 → 五种展示态里的哪一档（只读后端字段）：有装齐的用户环境 = 发现更合适的环境；有要装的 = 可以自动修复；
 * 有带预填的来源未定条目 = 需要确认；还有别的来源未定 = 尚无法确定；都没有 = 已就绪。
 */
export function originViewOfOffer(offer: DependencyPreparationOffer): OriginView {
  if ((offer.user_environments ?? []).some((e) => e.satisfies)) return 'better_env'
  if (offer.plan.requirements.length > 0) return 'auto_fix'
  const { specifiable, explainOnly } = partitionOrigins(offer.plan)
  if (specifiable.length > 0) return 'needs_confirm'
  if (explainOnly.length > 0) return 'undetermined'
  return 'ready'
}
