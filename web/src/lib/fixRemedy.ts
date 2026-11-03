/**
 * 智能修复的 remedy（ADR 0148）：后端 `engine/fixrouter.py` 按失败类型选出的**唯一**修法，随准备会话报告的 `remedy` 字段来。
 * 前端只翻译它（`prepView` 画主按钮），不自己判「这个失败该怎么修」。
 *
 * 与后端是严格同源对：种类闭集与字段表由 `tests/test_fixrouter_pair.py` 读本文件的 `REMEDY_FIELDS`、
 * `fixRemedy.test.ts` 读 `tests/golden/fix_remedy_vectors.json` 钉住。
 */
export type Remedy =
  /** `silent: true` = 桌面静默回退；`false` = 浏览器模式，失败卡上等用户点一次「允许并运行」（ADR 0127 修订 2026-10-30） */
  | { kind: 'switch_native'; silent: boolean }
  | { kind: 'install_packages'; packages: string[]; ask: boolean }
  /** `env`：后端已发现、体检过、满足 `to` 的现成环境（无路径）；有就是一键「改用 <环境名>」（用户点了才切），没有才进选择器 */
  | { kind: 'change_python'; from: string; to: string | null; env: RemedyEnv | null }
  | { kind: 'convert_to_matplotlib'; source: string }
  /** 缺的包装不了（私有库 / 本地模块 / conda-only）：主按钮是「选择运行环境」，AI 退为次入口（ADR 0148 修订 2026-11-01） */
  | { kind: 'choose_environment' }
  | { kind: 'ai_fix' }
  | { kind: 'connect_ai' }
  | { kind: 'feedback'; tried: string[] }
  /** 脚本自己报错，而它正是改图助手刚改出来、改前跑通过的那一版：撤销那次修改（`session` = 那次 AI 会话 id，TVD-H1M5） */
  | { kind: 'revert_ai_edit'; session: string }

/** `adoptCandidate` 要的身份（id + 环境代）加展示用的名字与完整版本 */
export interface RemedyEnv {
  id: string
  generation: string
  name: string
  version: string
}

export type RemedyKind = Remedy['kind']

/** 每种 remedy 除 `kind` 外的字段名（闭集；顺序 = 后端 `fixrouter.REMEDY_KINDS`） */
export const REMEDY_FIELDS = {
  switch_native: ['silent'],
  install_packages: ['ask', 'packages'],
  change_python: ['env', 'from', 'to'],
  convert_to_matplotlib: ['source'],
  choose_environment: [],
  ai_fix: [],
  connect_ai: [],
  feedback: ['tried'],
  revert_ai_edit: ['session'],
} as const satisfies Record<RemedyKind, readonly string[]>

export const REMEDY_KINDS = Object.keys(REMEDY_FIELDS) as RemedyKind[]

/** 报告里的 `remedy` 是不是认得的形状（老后端没有这个键、新后端加了种类旧前端读到都当没有，不猜） */
export function isRemedy(x: unknown): x is Remedy {
  if (typeof x !== 'object' || x === null) return false
  const rec = x as Record<string, unknown>
  const fields = (REMEDY_FIELDS as Record<string, readonly string[]>)[String(rec.kind)]
  if (!fields) return false
  const keys = Object.keys(rec).filter((k) => k !== 'kind')
  return keys.length === fields.length && fields.every((f) => f in rec)
}
