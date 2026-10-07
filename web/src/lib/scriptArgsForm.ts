/**
 * 参数表单 ↔ token 列表（T07）。
 *
 * **token 列表是唯一权威**（T03 的 `scriptArgvStore` 草稿）。表单不另存一份"意图"：它每次都从 token 读出
 * 字段视图（`readTokens`），用户改一个字段 = 对 token 列表做一次**只动这个参数自己那几个 token** 的编辑
 * （`applyEdit`）。所以：
 *
 * - 表单不认识的 token（动态加的参数、自定义 Action、拼写缩写、`-vx` 这种合并短选项……）原样留在原位，
 *   切换「表单 / 参数列表」两个视图不会重建、不会丢信息；
 * - 默认值只展示，绝不写成 token；"未提供"、空字符串、开关关、用脚本默认四种状态各是各的 token 形状；
 * - 读不准的时候（出现不认识的选项之后，位置参数归谁说不清）对应字段只读，并说明原因——不猜。
 *
 * schema 来自后端纯静态的 `engine/scriptargs.py`；两侧共用 `tests/golden/script_args_form_vectors.json`：
 * Python 那边对"token → 真 argparse 的 Namespace"，这边对"表单编辑 → token"。
 */

export type Arity = number | '?' | '*' | '+' | '...' | 'unknown'

export interface ScriptArgument {
  id: string
  flags: string[]
  positional: boolean
  action: string
  nargs: number | string | null
  arity: Arity
  required: boolean | null
  required_source?: string | null
  default: { kind: 'literal'; display: string } | { kind: 'suppress' | 'dynamic' } | null
  choices: string[] | null
  choices_dynamic?: boolean
  type: string | null
  role: 'input_file' | 'output_file' | null
  help: string | null
  hidden: boolean
  metavar: string | null
  dest: string
  editable: boolean
  conditional: boolean
  group: string | null
  line: number
}

export interface ScriptArgsSchema {
  version: number
  source: string
  framework: string | null
  status: 'none' | 'complete' | 'partial' | 'unknown'
  reasons: string[]
  form_enabled: boolean
  parse_call: string | null
  negative_number_options: boolean
  arguments: ScriptArgument[]
  exclusive_groups: { id: string; required: boolean; members: string[] }[]
  subcommands: { dest: string | null; required: boolean; choices: string[]; dynamic: boolean } | null
}

type Style = 'separate' | 'equals' | 'attached' | 'flag'

interface Occurrence {
  start: number
  end: number
  flag: string
  style: Style
  /** 值 token 的下标（`equals` / `attached` 时值在 `start` 那一项里） */
  values: string[]
  complete: boolean
  /** BooleanOptionalAction：这一次是 `--no-x` */
  negated?: boolean
}

export type FieldState = 'unset' | 'value' | 'on' | 'off'

export interface FieldView {
  id: string
  state: FieldState
  values: string[]
  /** 这个参数在 token 里出现了几次（store 类最后一次生效） */
  occurrences: number
  /** 最后一次出现缺值（`--freq` 后面没有值 / 跟着另一个选项） */
  incomplete: boolean
  /** 读不准（位置参数在不认识的选项之后）：只读 */
  uncertain: boolean
}

export interface TokenView {
  fields: Record<string, FieldView>
  /** 表单没认领的 token 下标（原样保留，展示在"其他参数"里） */
  unattributed: number[]
  positionalsCertain: boolean
  /** 第一个 `--` 的下标；没有 = -1 */
  doubleDash: number
}

export type FormEdit =
  | { op: 'set'; arg: string; values: string[] }
  | { op: 'clear'; arg: string }
  | { op: 'flag'; arg: string; value: 'on' | 'off' | 'default' }

export type EditError =
  | 'form_disabled'
  | 'not_editable'
  | 'positionals_uncertain'
  | 'earlier_positional_missing'
  | 'later_positional_present'
  | 'positional_looks_like_option'
  | 'value_looks_like_option'
  | 'wrong_value_count'

export type EditResult = { ok: true; tokens: string[] } | { ok: false; error: EditError }

const NEGATIVE = /^-\d+$|^-\d*\.\d+$/

const byId = (schema: ScriptArgsSchema) => new Map(schema.arguments.map((a) => [a.id, a]))

/** argparse 会不会把这个 token 当成选项（`_parse_optional` 的前几条判据）。 */
export const looksLikeOption = (schema: ScriptArgsSchema, token: string): boolean => {
  if (token.length < 2 || token[0] !== '-') return false
  if (NEGATIVE.test(token) && !schema.negative_number_options) return false
  return true
}

export const negatedFlags = (arg: ScriptArgument) =>
  arg.action === 'boolean_optional'
    ? arg.flags.filter((f) => f.startsWith('--')).map((f) => `--no-${f.slice(2)}`)
    : []

interface OptionIndex {
  exact: Map<string, { arg: ScriptArgument; negated: boolean }>
}

const indexOptions = (schema: ScriptArgsSchema): OptionIndex => {
  const exact = new Map<string, { arg: ScriptArgument; negated: boolean }>()
  for (const arg of schema.arguments) {
    if (arg.positional) continue
    for (const f of arg.flags) exact.set(f, { arg, negated: false })
    for (const f of negatedFlags(arg)) exact.set(f, { arg, negated: true })
  }
  return { exact }
}

interface Scan {
  occurrences: Map<string, Occurrence[]>
  positionalIdx: number[]
  unknown: number[]
  certain: boolean
  doubleDash: number
}

const scan = (schema: ScriptArgsSchema, tokens: string[]): Scan => {
  const { exact } = indexOptions(schema)
  const occurrences = new Map<string, Occurrence[]>()
  const positionalIdx: number[] = []
  const unknown: number[] = []
  let certain = true
  let doubleDash = -1
  const push = (id: string, occ: Occurrence) =>
    occurrences.set(id, [...(occurrences.get(id) ?? []), occ])
  const isValue = (t: string | undefined) =>
    t !== undefined && t !== '--' && !looksLikeOption(schema, t)

  let i = 0
  while (i < tokens.length) {
    const t = tokens[i]
    if (doubleDash >= 0) {
      positionalIdx.push(i)
      i += 1
      continue
    }
    if (t === '--') {
      doubleDash = i
      i += 1
      continue
    }
    if (!looksLikeOption(schema, t)) {
      positionalIdx.push(i)
      i += 1
      continue
    }
    const hit = exact.get(t)
    if (hit) {
      const { arg, negated } = hit
      const arity = arg.arity
      if (arity === 0) {
        push(arg.id, { start: i, end: i + 1, flag: t, style: 'flag', values: [], complete: true, negated })
        i += 1
      } else if (typeof arity === 'number') {
        const values: string[] = []
        let j = i + 1
        while (values.length < arity && isValue(tokens[j])) values.push(tokens[j++])
        push(arg.id, { start: i, end: j, flag: t, style: 'separate', values, complete: values.length === arity })
        i = j
      } else if (arity === '?' || arity === '*' || arity === '+') {
        const values: string[] = []
        let j = i + 1
        while (isValue(tokens[j]) && (arity !== '?' || values.length < 1)) values.push(tokens[j++])
        push(arg.id, {
          start: i,
          end: j,
          flag: t,
          style: 'separate',
          values,
          complete: arity !== '+' || values.length > 0,
        })
        i = j
      } else {
        // 自定义 Action / 动态 nargs：吃几个值说不清——认出它出现过，之后的位置参数归属不再可信
        push(arg.id, { start: i, end: i + 1, flag: t, style: 'flag', values: [], complete: true })
        certain = false
        i += 1
      }
      continue
    }
    const eq = t.indexOf('=')
    if (eq > 0) {
      const named = exact.get(t.slice(0, eq))
      if (named && !named.negated && named.arg.arity === 1) {
        push(named.arg.id, {
          start: i,
          end: i + 1,
          flag: t.slice(0, eq),
          style: 'equals',
          values: [t.slice(eq + 1)],
          complete: true,
        })
        i += 1
        continue
      }
    }
    if (!t.startsWith('--') && t.length > 2) {
      const short = exact.get(t.slice(0, 2))
      if (short && short.arg.arity === 1) {
        push(short.arg.id, {
          start: i,
          end: i + 1,
          flag: t.slice(0, 2),
          style: 'attached',
          values: [t.slice(2)],
          complete: true,
        })
        i += 1
        continue
      }
    }
    if (t.includes(' ')) {
      // argparse：带空格、又对不上任何选项的 token 是位置参数
      positionalIdx.push(i)
      i += 1
      continue
    }
    // 不认识的选项：它吃不吃后面的 token 不知道
    unknown.push(i)
    certain = false
    i += 1
  }
  return { occurrences, positionalIdx, unknown, certain, doubleDash }
}

const positionalArgs = (schema: ScriptArgsSchema) => schema.arguments.filter((a) => a.positional)

/** 位置参数能不能按顺序认领：全是定长、没有子命令、前面没有说不清的选项。 */
const positionalsMappable = (schema: ScriptArgsSchema, s: Scan) =>
  s.certain &&
  schema.subcommands === null &&
  positionalArgs(schema).every((a) => typeof a.arity === 'number' && a.arity >= 1)

interface PositionalSlot {
  arg: ScriptArgument
  idx: number[]
}

const mapPositionals = (schema: ScriptArgsSchema, s: Scan): { slots: PositionalSlot[]; extra: number[] } => {
  const slots: PositionalSlot[] = []
  let k = 0
  for (const arg of positionalArgs(schema)) {
    const n = arg.arity as number
    const idx = s.positionalIdx.slice(k, k + n)
    k += idx.length
    slots.push({ arg, idx })
  }
  return { slots, extra: s.positionalIdx.slice(k) }
}

/** token → 字段视图。纯函数。 */
export const readTokens = (schema: ScriptArgsSchema, tokens: string[]): TokenView => {
  const s = scan(schema, tokens)
  const fields: Record<string, FieldView> = {}
  const attributed = new Set<number>()
  if (s.doubleDash >= 0) attributed.add(s.doubleDash)
  for (const arg of schema.arguments) {
    if (arg.positional) continue
    const occs = s.occurrences.get(arg.id) ?? []
    for (const o of occs) for (let j = o.start; j < o.end; j++) attributed.add(j)
    const last = occs[occs.length - 1]
    let state: FieldState = 'unset'
    if (last) {
      if (arg.action === 'boolean_optional') state = last.negated ? 'off' : 'on'
      else if (arg.arity === 0) state = 'on'
      else state = 'value'
    }
    fields[arg.id] = {
      id: arg.id,
      state,
      values: last ? [...last.values] : [],
      occurrences: occs.length,
      incomplete: last ? !last.complete : false,
      uncertain: false,
    }
  }
  const mappable = positionalsMappable(schema, s)
  if (mappable) {
    const { slots } = mapPositionals(schema, s)
    for (const { arg, idx } of slots) {
      for (const j of idx) attributed.add(j)
      fields[arg.id] = {
        id: arg.id,
        state: idx.length > 0 ? 'value' : 'unset',
        values: idx.map((j) => tokens[j]),
        occurrences: idx.length > 0 ? 1 : 0,
        incomplete: idx.length > 0 && idx.length < (arg.arity as number),
        uncertain: false,
      }
    }
  } else {
    for (const arg of positionalArgs(schema)) {
      fields[arg.id] = {
        id: arg.id,
        state: 'unset',
        values: [],
        occurrences: 0,
        incomplete: false,
        uncertain: true,
      }
    }
  }
  const unattributed = tokens.map((_, j) => j).filter((j) => !attributed.has(j))
  return { fields, unattributed, positionalsCertain: mappable, doubleDash: s.doubleDash }
}

/** 必填但 token 里没有的参数（default 不算答案）。位置参数读不准时不算缺（不知道）。 */
export const missingRequired = (schema: ScriptArgsSchema, view: TokenView): string[] => {
  const out: string[] = []
  for (const arg of schema.arguments) {
    const f = view.fields[arg.id]
    if (arg.required !== true || !f || f.uncertain) continue
    if (f.state === 'unset' || f.incomplete) out.push(arg.id)
  }
  return out
}

/** 互斥组：两个以上成员同时给了（冲突）/ 必选组一个都没给（缺）。只提示，不替用户删 token。 */
export const groupProblems = (
  schema: ScriptArgsSchema,
  view: TokenView,
): { id: string; problem: 'conflict' | 'missing'; members: string[] }[] => {
  const out: { id: string; problem: 'conflict' | 'missing'; members: string[] }[] = []
  for (const g of schema.exclusive_groups) {
    const set = g.members.filter((m) => {
      const s = view.fields[m]?.state
      return s !== undefined && s !== 'unset'
    })
    if (set.length > 1) out.push({ id: g.id, problem: 'conflict', members: set })
    else if (g.required && set.length === 0) out.push({ id: g.id, problem: 'missing', members: g.members })
  }
  return out
}

const preferredFlag = (arg: ScriptArgument) => arg.flags.find((f) => f.startsWith('--')) ?? arg.flags[0]

/** 值像选项时用等号，短选项同样如此：`-k-x` 可能是另一个完整选项，`-k=-x` 才是值。 */
const encodeOption = (
  schema: ScriptArgsSchema,
  arg: ScriptArgument,
  values: string[],
): string[] | EditError => {
  const flag = preferredFlag(arg)
  if (values.length === 1 && looksLikeOption(schema, values[0])) {
    return [`${flag}=${values[0]}`]
  }
  if (values.some((v) => looksLikeOption(schema, v) || v === '--')) return 'value_looks_like_option'
  return [flag, ...values]
}

const splice = (tokens: string[], start: number, end: number, insert: string[]) => [
  ...tokens.slice(0, start),
  ...insert,
  ...tokens.slice(end),
]

const optionInsertAt = (tokens: string[], view: TokenView) =>
  view.doubleDash >= 0 ? view.doubleDash : tokens.length

const removeOccurrences = (tokens: string[], occs: Occurrence[]) => {
  let out = tokens
  for (const o of [...occs].sort((a, b) => b.start - a.start)) out = splice(out, o.start, o.end, [])
  return out
}

/** 对 token 列表做一次表单编辑。只动这个参数自己的 token；别的 token 的内容与相对顺序不变。 */
export const applyEdit = (schema: ScriptArgsSchema, tokens: string[], edit: FormEdit): EditResult => {
  if (!schema.form_enabled) return { ok: false, error: 'form_disabled' }
  const arg = byId(schema).get(edit.arg)
  if (!arg || !arg.editable) return { ok: false, error: 'not_editable' }
  const s = scan(schema, tokens)
  const view = readTokens(schema, tokens)
  if (arg.positional) return editPositional(schema, tokens, s, view, arg, edit)
  const occs = s.occurrences.get(arg.id) ?? []

  if (edit.op === 'clear') return { ok: true, tokens: removeOccurrences(tokens, occs) }

  if (edit.op === 'flag') {
    if (arg.arity !== 0) return { ok: false, error: 'not_editable' }
    if (arg.action === 'boolean_optional') {
      const want = edit.value
      const last = occs[occs.length - 1]
      if (want !== 'default' && last && !!last.negated === (want === 'off')) return { ok: true, tokens }
      const cleared = removeOccurrences(tokens, occs)
      if (want === 'default') return { ok: true, tokens: cleared }
      const flag = want === 'on' ? preferredFlag(arg) : negatedFlags(arg)[0]
      // argparse 的短名 BooleanOptionalAction 没有反向选项，不能把“关”冒充成脚本默认。
      if (flag === undefined) return { ok: false, error: 'not_editable' }
      const at = optionInsertAt(cleared, readTokens(schema, cleared))
      return { ok: true, tokens: splice(cleared, at, at, [flag]) }
    }
    if (edit.value === 'on') {
      if (occs.length > 0) return { ok: true, tokens }
      const at = optionInsertAt(tokens, view)
      return { ok: true, tokens: splice(tokens, at, at, [preferredFlag(arg)]) }
    }
    // store_true 一类：「关」与「用脚本默认」都是不出现——这一个开关的两种说法在 token 上相同
    return { ok: true, tokens: removeOccurrences(tokens, occs) }
  }

  // set
  if (typeof arg.arity !== 'number' || arg.arity < 1) return { ok: false, error: 'not_editable' }
  if (edit.values.length !== arg.arity) return { ok: false, error: 'wrong_value_count' }
  const last = occs[occs.length - 1]
  if (last && last.complete) {
    if (last.style === 'equals' || last.style === 'attached') {
      const value = edit.values[0]
      // 保留安全的粘连形状；空值、等号开头或会被认成另一个完整选项的形状必须消歧。
      const needsEquals = last.style === 'equals' || value === '' || value.startsWith('=') ||
        looksLikeOption(schema, value) || indexOptions(schema).exact.has(`${last.flag}${value}`)
      const glue = needsEquals ? '=' : ''
      return { ok: true, tokens: splice(tokens, last.start, last.end, [`${last.flag}${glue}${edit.values[0]}`]) }
    }
    if (edit.values.some((v) => looksLikeOption(schema, v) || v === '--')) {
      if (arg.arity !== 1) return { ok: false, error: 'value_looks_like_option' }
      const glued = `${last.flag}=${edit.values[0]}`
      return { ok: true, tokens: splice(tokens, last.start, last.end, [glued]) }
    }
    return { ok: true, tokens: splice(tokens, last.start, last.end, [last.flag, ...edit.values]) }
  }
  const fresh = encodeOption(schema, arg, edit.values)
  if (typeof fresh === 'string') return { ok: false, error: fresh }
  if (last) return { ok: true, tokens: splice(tokens, last.start, last.end, fresh) }
  const at = optionInsertAt(tokens, view)
  return { ok: true, tokens: splice(tokens, at, at, fresh) }
}

const editPositional = (
  schema: ScriptArgsSchema,
  tokens: string[],
  s: Scan,
  view: TokenView,
  arg: ScriptArgument,
  edit: FormEdit,
): EditResult => {
  if (!view.positionalsCertain) return { ok: false, error: 'positionals_uncertain' }
  if (edit.op === 'flag') return { ok: false, error: 'not_editable' }
  const { slots } = mapPositionals(schema, s)
  const at = slots.findIndex((x) => x.arg.id === arg.id)
  const slot = slots[at]
  const later = slots.slice(at + 1).some((x) => x.idx.length > 0)
  if (edit.op === 'clear') {
    if (slot.idx.length === 0) return { ok: true, tokens }
    if (later) return { ok: false, error: 'later_positional_present' }
    return { ok: true, tokens: tokens.filter((_, j) => !slot.idx.includes(j)) }
  }
  if (edit.values.length !== arg.arity) return { ok: false, error: 'wrong_value_count' }
  const afterDashes = (j: number) => s.doubleDash >= 0 && j > s.doubleDash
  if (slot.idx.length === arg.arity) {
    if (edit.values.some((v, k) => looksLikeOption(schema, v) && !afterDashes(slot.idx[k]))) {
      return { ok: false, error: 'positional_looks_like_option' }
    }
    const out = [...tokens]
    slot.idx.forEach((j, k) => (out[j] = edit.values[k]))
    return { ok: true, tokens: out }
  }
  if (slot.idx.length > 0) return { ok: false, error: 'later_positional_present' }
  if (slots.slice(0, at).some((x) => x.idx.length < (x.arg.arity as number))) {
    return { ok: false, error: 'earlier_positional_missing' }
  }
  // 紧跟在最后一个已有的位置参数之后（它前面不是一个等值的选项）；一个都没有就放最前
  const prev = s.positionalIdx[s.positionalIdx.length - 1]
  const insertAt = prev === undefined ? 0 : prev + 1
  if (edit.values.some((v) => looksLikeOption(schema, v)) && !afterDashes(insertAt)) {
    return { ok: false, error: 'positional_looks_like_option' }
  }
  return { ok: true, tokens: splice(tokens, insertAt, insertAt, edit.values) }
}
