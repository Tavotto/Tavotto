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
  /** 声明了像负数的选项名（3.14 之前的文法：`^-\d+$|^-\d*\.\d+$`） */
  negative_number_options: boolean
  /** 同上，按 3.14 起的文法（`-\.?\d`）；老后端没有这个键 = 当作与上一项相同 */
  negative_number_options_extended?: boolean
  /** worker 的 Python 版本（项目记住的事实，如 `3.14.7`）；不知道 = null / 缺省 → 取较窄的旧文法 */
  python_version?: string | null
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

/**
 * argparse 的 `_negative_number_matcher`，按 CPython 源码逐字（本机实测 `ArgumentParser()._negative_number_matcher.pattern`）：
 * - Python <= 3.13（3.13.13 / 3.12.12 / 3.11.14）：`^-\d+$|^-\d*\.\d+$`
 * - Python 3.14（3.14.7）：`-\.?\d`（`.match`，无结尾锚：`-1e3` / `-.5` / `-1.` / `-1_0` / `-1j` / `-1abc` 都算）
 * Python 的 `\d` 是 Unicode 十进制数字（`\p{Nd}`）；`$` 也匹配结尾换行之前。后端同形：`scriptargs.NEGATIVE_NUMBER_*`。
 */
const NEGATIVE_LEGACY = /^-\p{Nd}+(?:\n)?$|^-\p{Nd}*\.\p{Nd}+(?:\n)?$/u
const NEGATIVE_EXTENDED = /^-\.?\p{Nd}/u

/** 3.14 起的文法？版本不明时取旧文法：旧文法认的负数是新文法的子集，不确定时把更多 token 当选项只会让表单更保守
 *（拒绝写入、退回原始 token 编辑），反过来则会写出 3.13 的 parser 当选项吃掉的 token（运行时才报错）。 */
export const usesExtendedNegativeGrammar = (schema: Pick<ScriptArgsSchema, 'python_version'>): boolean => {
  const m = /^\s*(\d+)\.(\d+)/.exec(schema.python_version ?? '')
  if (!m) return false
  const major = Number(m[1])
  return major > 3 || (major === 3 && Number(m[2]) >= 14)
}

export const looksLikeNegativeNumber = (schema: ScriptArgsSchema, token: string): boolean =>
  (usesExtendedNegativeGrammar(schema) ? NEGATIVE_EXTENDED : NEGATIVE_LEGACY).test(token)

const hasNegativeLookingOptions = (schema: ScriptArgsSchema): boolean =>
  usesExtendedNegativeGrammar(schema)
    ? (schema.negative_number_options_extended ?? schema.negative_number_options)
    : schema.negative_number_options

const byId = (schema: ScriptArgsSchema) => new Map(schema.arguments.map((a) => [a.id, a]))

/** argparse 会不会把这个 token 当成选项（`_parse_optional` 的前几条判据）。 */
export const looksLikeOption = (schema: ScriptArgsSchema, token: string): boolean => {
  if (token.length < 2 || token[0] !== '-') return false
  if (looksLikeNegativeNumber(schema, token) && !hasNegativeLookingOptions(schema)) return false
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

/**
 * 必选子命令（`add_subparsers(required=True)`）token 里没选。只在**位置 token**里认（`scan` 已把已知选项的值认走：
 * `--output plot` 里的 `plot` 是值，不算选了子命令；`--` 之后全是位置）。`choices` 里任一名字出现 = 选了；名字不可枚举（动态）
 * 时任何位置 token 都算。没认到、但出现过不认识的选项（它吃不吃后面的 token 说不清）= 拿不准，当选了，不拦用户。
 */
export const subcommandMissing = (schema: ScriptArgsSchema, tokens: string[]): boolean => {
  const sub = schema.subcommands
  if (!sub || !sub.required) return false
  const s = scan(schema, tokens)
  let positional = s.positionalIdx.map((j) => tokens[j])
  // 子命令前面的位置参数先吃位置 token：先预留各自的最少个数（定长 n、`+` 1），`*` `?` 预留 0；其余 token 才可能是子命令名。
  // 变长的可能多吃，所以超出最少个数的部分仍按「拿不准就当选了」处理；arity 说不清（自定义 / REMAINDER）= 不预留
  const reserve = positionalArgs(schema).reduce((n, a) => {
    if (typeof a.arity === 'number') return n + a.arity
    return n + (a.arity === '+' ? 1 : 0)
  }, 0)
  positional = positional.slice(reserve)
  const chosen =
    !sub.dynamic && sub.choices.length > 0 ? positional.some((t) => sub.choices.includes(t)) : positional.length > 0
  if (chosen) return false
  return s.unknown.length === 0
}

export interface MissingRequirements {
  /** 必填但没给的参数 id（都在 `schema.arguments` 里） */
  args: string[]
  /** 必选互斥组一个成员都没给的组 id（都在 `schema.exclusive_groups` 里） */
  groups: string[]
  /** 必选子命令没选（仅在传了 `tokens` 时判） */
  subcommand: boolean
  /** 三类合计：准备卡的运行闸只看这个数 */
  count: number
}

/**
 * 还缺的必填项（default 不算答案），分类型给：消费者各取所需，不会把组 id 当参数 id 去查表。位置参数读不准时不算缺（不知道）。
 * **所有「还缺必填」的判断都走这里**（表单高亮与准备卡的运行闸同源）。
 */
export const missingRequirements = (
  schema: ScriptArgsSchema,
  view: TokenView,
  tokens?: string[],
): MissingRequirements => {
  // 表单关着（子命令 / parents 等）时位置参数读不准、条件式参数也说不清：只认**非位置、非条件**的必填选项与
  // 全由它们组成的必选互斥组；其余当不确定、不拦。这样准备卡的运行闸只有这一个入口
  const reliable = (id: string): boolean => {
    if (schema.form_enabled) return true
    const a = schema.arguments.find((x) => x.id === id)
    return !!a && !a.positional && !a.conditional
  }
  const args: string[] = []
  for (const arg of schema.arguments) {
    const f = view.fields[arg.id]
    if (arg.required !== true || !f || f.uncertain || !reliable(arg.id)) continue
    if (f.state === 'unset' || f.incomplete) args.push(arg.id)
  }
  const groups: string[] = []
  for (const g of schema.exclusive_groups) {
    if (!g.required) continue
    const fs = g.members.map((m) => view.fields[m])
    if (fs.some((f) => !f || f.uncertain) || !g.members.every(reliable)) continue
    // 成员只写了选项名、值还没给（incomplete）不算答案：运行时 argparse 会报缺值
    if (fs.every((f) => f.state === 'unset' || f.incomplete)) groups.push(g.id)
  }
  const subcommand = tokens !== undefined && subcommandMissing(schema, tokens)
  return { args, groups, subcommand, count: args.length + groups.length + (subcommand ? 1 : 0) }
}

/** 只要参数 id 的消费者（表单的「还缺」一行）用这个；组与子命令另有各自的提示（`groupProblems` 等）。 */
export const missingRequired = (schema: ScriptArgsSchema, view: TokenView): string[] =>
  missingRequirements(schema, view).args

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
    else if (g.required && !set.some((m) => !view.fields[m]?.incomplete)) out.push({ id: g.id, problem: 'missing', members: g.members })
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
