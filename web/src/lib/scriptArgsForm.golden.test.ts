/**
 * 表单编辑 → token 与后端严格同源（T07）：两侧各读 `tests/golden/script_args_form_vectors.json`，不读对方源码。
 * 后端那一侧（`tests/test_script_args.py`）把每条 `after` 交给**那份脚本自己的 argparse** 原生跑、比 Namespace；
 * 这一侧证明表单编辑恰好产出 `after`、读回的视图说得出缺哪些必填 / 哪些 token 原样保留。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/script_args_form_vectors.json'
import {
  applyEdit,
  groupProblems,
  missingRequired,
  missingRequirements,
  readTokens,
  type FormEdit,
  type ScriptArgsSchema,
} from './scriptArgsForm'

const schemas = golden.schemas as unknown as Record<string, ScriptArgsSchema>

interface Case {
  name: string
  script: string
  before: string[]
  edits: FormEdit[]
  after: string[]
  missing: string[]
  conflicts?: string[]
  unattributed?: number[]
}

const run = (schema: ScriptArgsSchema, before: string[], edits: FormEdit[]) =>
  edits.reduce((tokens, edit) => {
    const r = applyEdit(schema, tokens, edit)
    if (!r.ok) throw new Error(`${edit.op} ${edit.arg}: ${r.error}`)
    return r.tokens
  }, before)

describe('表单编辑 → token（golden）', () => {
  it.each(golden.cases as unknown as Case[])('$name', (c) => {
    const schema = schemas[c.script]
    const after = run(schema, c.before, c.edits)
    expect(after).toEqual(c.after)
    const view = readTokens(schema, after)
    expect(missingRequired(schema, view)).toEqual(c.missing)
    if (c.conflicts) {
      expect(groupProblems(schema, view).filter((g) => g.problem === 'conflict').map((g) => g.id)).toEqual(
        c.conflicts,
      )
    }
    if (c.unattributed) expect(view.unattributed).toEqual(c.unattributed)
  })

  it.each(golden.errors as unknown as { name: string; script: string; before: string[]; edit: FormEdit; error: string }[])(
    '拒绝：$name',
    (c) => {
      const r = applyEdit(schemas[c.script], c.before, c.edit)
      expect(r).toEqual({ ok: false, error: c.error })
    },
  )
})

describe('只动自己的 token', () => {
  const schema = schemas.mixed

  it('short equals values round-trip without swallowing the separator or selecting a colliding option', () => {
    const view = readTokens(schemas.short_options, ['-k=-x'])
    expect(view.fields.a0).toMatchObject({ state: 'value', values: ['-x'], incomplete: false })
    expect(view.fields.a1.state).toBe('unset')
    expect(view.unattributed).toEqual([])
  })

  it('不认识的 token 内容与相对顺序在任意编辑序列后不变（切视图不重建）', () => {
    const before = ['in.csv', '3', '--mystery=1', '-vx', '--scale', '2', '--zz']
    const after = run(schema, before, [
      { op: 'set', arg: 'a2', values: ['9'] },
      { op: 'flag', arg: 'a4', value: 'on' },
      { op: 'flag', arg: 'a5', value: 'off' },
      { op: 'clear', arg: 'a4' },
    ])
    const keep = ['--mystery=1', '-vx', '--zz']
    expect(after.filter((t) => keep.includes(t))).toEqual(keep)
    // 读回：出现了不认识的选项，位置参数归谁说不清——它们也不认领（只读），连同那三项原样留着
    const view = readTokens(schema, after)
    expect(view.positionalsCertain).toBe(false)
    expect(view.fields.a0.uncertain).toBe(true)
    expect(view.unattributed.map((j) => after[j])).toEqual(['in.csv', '3', ...keep])
  })

  it('默认值从不写成 token：只读一遍、不编辑，token 不变', () => {
    const view = readTokens(schema, [])
    expect(view.fields.a2.state).toBe('unset') // --scale default=1.0
    expect(view.fields.a6.state).toBe('unset') // --level default=2
    expect(missingRequired(schema, view)).toEqual(['a0', 'a1'])
  })

  it('空字符串是一个值，不是"未提供"', () => {
    const view = readTokens(schema, ['in.csv', '3', '--k-value', ''])
    expect(view.fields.a3).toMatchObject({ state: 'value', values: [''] })
    expect(readTokens(schema, ['in.csv', '3']).fields.a3.state).toBe('unset')
  })

  it('BooleanOptional 三态：开 / 关 / 用脚本默认各是各的 token', () => {
    expect(readTokens(schema, ['--color']).fields.a5.state).toBe('on')
    expect(readTokens(schema, ['--no-color']).fields.a5.state).toBe('off')
    expect(readTokens(schema, []).fields.a5.state).toBe('unset')
  })

  it('缺值的选项如实标 incomplete，必填仍算缺', () => {
    const fft = schemas.fft6
    const view = readTokens(fft, ['--freq', '--amp', '2'])
    expect(view.fields.a0).toMatchObject({ state: 'value', incomplete: true })
    expect(missingRequired(fft, view)).toContain('a0')
  })
})

describe('missingRequired：必选互斥组与必选子命令（#820 r4232531822）', () => {
  const base = schemas.fft6
  const arg = (id: string) => ({ ...base.arguments[0], id, flags: [`--${id}`], required: false, positional: false, arity: 1 as const, nargs: null, action: 'store', group: 'gx' })
  const grp = {
    ...base,
    arguments: [arg('csv'), arg('json')],
    exclusive_groups: [{ id: 'gx', required: true, members: ['csv', 'json'] }],
    subcommands: null,
  } as ScriptArgsSchema

  it('一个成员都没给 = 缺 1；给了一个 = 0；组不必选 = 0', () => {
    expect(missingRequirements(grp, readTokens(grp, [])).groups).toEqual(['gx'])
    expect(missingRequirements(grp, readTokens(grp, [])).count).toBe(1)
    expect(missingRequired(grp, readTokens(grp, []))).toEqual([]) // 组 id 不混进参数 id
    expect(missingRequirements(grp, readTokens(grp, ['--csv', 'a'])).count).toBe(0)
    const optional = { ...grp, exclusive_groups: [{ id: 'gx', required: false, members: ['csv', 'json'] }] }
    expect(missingRequirements(optional, readTokens(optional, [])).count).toBe(0)
  })

  it('必选子命令：没选 = subcommand；选了（或不必选）= 0；不传 tokens 不判', () => {
    const sub = {
      ...base,
      arguments: [],
      exclusive_groups: [],
      subcommands: { dest: 'cmd', required: true, choices: ['plot', 'stats'], dynamic: false },
    } as ScriptArgsSchema
    expect(missingRequirements(sub, readTokens(sub, []), []).subcommand).toBe(true)
    expect(missingRequirements(sub, readTokens(sub, []), ['--v']).subcommand).toBe(false) // 不认识的选项：拿不准
    expect(missingRequirements(sub, readTokens(sub, []), ['-v', 'plot']).count).toBe(0)
    expect(missingRequirements(sub, readTokens(sub, [])).count).toBe(0)
    const notRequired = { ...sub, subcommands: { ...sub.subcommands!, required: false } }
    expect(missingRequirements(notRequired, readTokens(notRequired, []), []).count).toBe(0)
  })

  it('选项的值不算子命令：`--output plot` 仍是没选（r4232594148）', () => {
    const out = { ...base.arguments[0], id: 'output', flags: ['--output'], required: false, positional: false, arity: 1 as const, nargs: null, action: 'store', group: null }
    const sub = {
      ...base,
      arguments: [out],
      exclusive_groups: [],
      subcommands: { dest: 'cmd', required: true, choices: ['plot', 'stats'], dynamic: false },
    } as ScriptArgsSchema
    const miss = (t: string[]) => missingRequirements(sub, readTokens(sub, t), t).subcommand
    expect(miss(['--output', 'plot'])).toBe(true)
    expect(miss(['--output=plot'])).toBe(true)
    expect(miss(['--output', 'x', 'plot'])).toBe(false)
    expect(miss(['--', 'plot'])).toBe(false)
    expect(miss(['--mystery', 'plot'])).toBe(false) // 不认识的选项：拿不准，当选了
  })
  it('子命令前的位置参数先吃位置 token：`name plot` 才算选了（r4232654895）', () => {
    const name = { ...base.arguments[0], id: 'name', flags: [], required: true, positional: true, arity: 1 as const, nargs: null, action: 'store', group: null }
    const sub = {
      ...base,
      arguments: [name],
      exclusive_groups: [],
      subcommands: { dest: 'cmd', required: true, choices: ['plot', 'stats'], dynamic: false },
    } as ScriptArgsSchema
    const miss = (t: string[]) => missingRequirements(sub, readTokens(sub, t), t).subcommand
    expect(miss(['plot'])).toBe(true) // `plot` 是 name，子命令还没选
    expect(miss(['x'])).toBe(true)
    expect(miss(['x', 'plot'])).toBe(false)
    // 前面有变长位置参数：说不清吃几个，拿不准当选了
    const rest = { ...name, arity: null, nargs: '*' }
    const loose = { ...sub, arguments: [rest] } as unknown as ScriptArgsSchema
    expect(missingRequirements(loose, readTokens(loose, ['plot']), ['plot']).subcommand).toBe(false)
  })

  it('必选互斥组：成员只写了选项名没给值（incomplete）不算满足（r4232654901）', () => {
    const grp = {
      ...base,
      arguments: [
        { ...base.arguments[0], id: 'output', flags: ['--output'], required: false, positional: false, arity: 1 as const, nargs: null, action: 'store', group: 'gx' },
        { ...base.arguments[0], id: 'json', flags: ['--json'], required: false, positional: false, arity: 1 as const, nargs: null, action: 'store', group: 'gx' },
      ],
      exclusive_groups: [{ id: 'gx', required: true, members: ['output', 'json'] }],
      subcommands: null,
    } as ScriptArgsSchema
    const view = readTokens(grp, ['--output'])
    expect(view.fields.output).toMatchObject({ state: 'value', incomplete: true })
    expect(missingRequirements(grp, view).groups).toEqual(['gx'])
    expect(groupProblems(grp, view).map((p) => p.problem)).toEqual(['missing'])
    expect(missingRequirements(grp, readTokens(grp, ['--output', 'a'])).groups).toEqual([])
  })
  it("前面是 nargs='+' 的位置参数：先预留 1 个 token 再认子命令（r4232790912）", () => {
    const files = { ...base.arguments[0], id: 'files', flags: [], required: true, positional: true, arity: '+' as const, nargs: '+', action: 'store', group: null }
    const sub = {
      ...base,
      arguments: [files],
      exclusive_groups: [],
      subcommands: { dest: 'cmd', required: true, choices: ['plot', 'stats'], dynamic: false },
    } as unknown as ScriptArgsSchema
    const miss = (t: string[]) => missingRequirements(sub, readTokens(sub, t), t).subcommand
    expect(miss(['plot'])).toBe(true) // 唯一的 token 归 `+`
    expect(miss(['a.csv', 'plot'])).toBe(false)
    const star = { ...sub, arguments: [{ ...files, arity: '*' as const, nargs: '*' }] } as unknown as ScriptArgsSchema
    expect(missingRequirements(star, readTokens(star, ['plot']), ['plot']).subcommand).toBe(false) // `*` 预留 0
  })

  it('表单关着：只认读得准的必填选项与互斥组，位置 / 条件式参数不拦（r4232790899）', () => {
    const opt = { ...base.arguments[0], id: 'input', flags: ['--input'], required: true, positional: false, arity: 1 as const, nargs: null, action: 'store', group: null, conditional: false }
    const pos = { ...opt, id: 'pos', flags: [], positional: true }
    const cond = { ...opt, id: 'cond', flags: ['--cond'], conditional: true }
    const sub = {
      ...base,
      form_enabled: false,
      arguments: [opt, pos, cond],
      exclusive_groups: [],
      subcommands: { dest: 'cmd', required: true, choices: ['plot', 'stats'], dynamic: false },
    } as unknown as ScriptArgsSchema
    const r = (t: string[]) => missingRequirements(sub, readTokens(sub, t), t)
    expect(r(['plot']).args).toEqual(['input'])
    expect(r(['--input', 'a', 'x', 'plot']).count).toBe(0)
  })
})
