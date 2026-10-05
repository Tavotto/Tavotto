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
