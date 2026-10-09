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
})
