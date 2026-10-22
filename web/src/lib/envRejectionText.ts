import { t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import type { EnvironmentNotUsed } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'

/**
 * 「为什么没用那个环境」（ADR 0079 修订）：准备报告的 `environment.not_used[]` 每一条的那一句人话。
 *
 * 后端 `userenvs.REJECT_CODES` 是闭集，这里是它的镜像（`tests/test_environment_rejection_copy_pair.py` 钉两侧相等）；
 * 事实在后端，这里只翻译、不判断、不自算「能不能跑」。条目不带路径（ADR 0053 §二），文案只用标签 / 缺的包名 / ROOT 版本。
 * 文案键写成字面量：i18n 的死键门禁按「源码里出现过这个串」判活。
 */
const SENTENCE: Record<string, string> = {
  missing_imports: 'prep.envNotUsed.missing_imports',
  import_failed: 'prep.envNotUsed.import_failed',
  python_out_of_range: 'prep.envNotUsed.python_out_of_range',
  no_matplotlib: 'prep.envNotUsed.no_matplotlib',
  worker_chain_failed: 'prep.envNotUsed.worker_chain_failed',
  activation_failed: 'prep.envNotUsed.activation_failed',
  probe_timeout: 'prep.envNotUsed.probe_timeout',
  unusable: 'prep.envNotUsed.unusable',
  not_checked: 'prep.envNotUsed.not_checked',
  outranked: 'prep.envNotUsed.outranked',
  root_python_mismatch: 'prep.envNotUsed.root_python_mismatch',
  root_needs_msvc: 'prep.envNotUsed.root_needs_msvc',
}

/** 两类带包名的原因在后端没给出名字时（老后端 / 没量到）用的变体 */
const UNNAMED: Record<string, string> = {
  missing_imports: 'prep.envNotUsed.missing_imports_unnamed',
  import_failed: 'prep.envNotUsed.import_failed_unnamed',
}

/** 后端 `REJECT_CODES` 的镜像（顺序也一致） */
export const ENV_REJECT_CODES = Object.keys(SENTENCE)

/** 不值得对用户说的两类：「能跑、只是排在后面」与「运行前没量」——不是「为什么没被用」的答案 */
const QUIET = new Set(['outranked', 'not_checked'])

/** 折叠里最多列几条 */
export const ENV_NOT_USED_SHOWN = 3

/** 要说出口的落选原因：去掉安静的两类、去重（同标签同原因只说一次）、截到上限；老后端 / 不认识的码退回「起不来」那一句 */
export function visibleNotUsed(list: readonly EnvironmentNotUsed[] | null | undefined): EnvironmentNotUsed[] {
  const seen = new Set<string>()
  const out: EnvironmentNotUsed[] = []
  for (const r of list ?? []) {
    if (QUIET.has(r.reason)) continue
    const key = `${r.reason}|${r.label}|${r.root_version ?? ''}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(r)
  }
  return out.slice(0, ENV_NOT_USED_SHOWN)
}

/** 一条落选原因的一句话 */
export function envNotUsedSentence(r: EnvironmentNotUsed): string {
  const code = r.reason in SENTENCE ? r.reason : 'unusable'
  const names = r.reason === 'import_failed' ? r.import_failed : r.missing
  // 缺的包名：没有名字时用 unnamed 变体，不凑名字
  const listed = listJoin(names ?? [])
  const key = !listed && UNNAMED[code] ? UNNAMED[code] : SENTENCE[code]
  return translate(key, {
    ns: 'workspace',
    label: r.label || translate('prep.envNotUsed.fallbackLabel', { ns: 'workspace' }),
    names: listed,
    root: r.root_version ? `ROOT ${r.root_version}` : 'ROOT',
    product: PRODUCT_NAME,
  })
}
