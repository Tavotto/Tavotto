/**
 * 智能修复的 remedy（ADR 0148）：后端 `engine/fixrouter.py` 按失败类型选出的**唯一**修法，随准备会话报告的 `remedy` 字段来。
 * 前端只翻译它（`prepView` 画主按钮），不自己判「这个失败该怎么修」。
 *
 * 与后端是严格同源对：种类闭集与字段表由 `tests/test_fixrouter_pair.py` 读本文件的 `REMEDY_FIELDS`、
 * `fixRemedy.test.ts` 读 `tests/golden/fix_remedy_vectors.json` 钉住。
 */
export type Remedy =
  | { kind: 'switch_native'; silent: true }
  | { kind: 'install_packages'; packages: string[]; ask: boolean }
  | { kind: 'change_python'; from: string; to: string | null }
  | { kind: 'convert_to_matplotlib'; source: string }
  | { kind: 'ai_fix' }
  | { kind: 'connect_ai' }
  | { kind: 'feedback'; tried: string[] }

export type RemedyKind = Remedy['kind']

/** 每种 remedy 除 `kind` 外的字段名（闭集；顺序 = 后端 `fixrouter.REMEDY_KINDS`） */
export const REMEDY_FIELDS = {
  switch_native: ['silent'],
  install_packages: ['ask', 'packages'],
  change_python: ['from', 'to'],
  convert_to_matplotlib: ['source'],
  ai_fix: [],
  connect_ai: [],
  feedback: ['tried'],
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
