/**
 * 短选项值的等号分隔符（Codex r4221224229）：argparse 对 `-k=v` 与 `-kv` 都得到 v，
 * 等号不属于值。生成端（applyEdit）为避开 `-k-x` 这类撞车写成 `-k=-x`，扫描端必须剥掉分隔符。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/script_args_form_vectors.json'
import { applyEdit, readTokens, type ScriptArgsSchema } from './scriptArgsForm'

const schema = golden.schemas.short_options as unknown as ScriptArgsSchema
const k = schema.arguments.find((a) => a.flags.includes('-k'))!

const read = (tokens: string[]) => readTokens(schema, tokens).fields[k.id]

describe('短选项值：等号分隔符不进值', () => {
  it.each([
    [['-k=-x'], '-x'],
    [['-k=x'], 'x'],
    [['-kx'], 'x'],
    [['-k==x'], '=x'],
    [['-k='], ''],
  ])('读回 %j', (tokens, value) => {
    expect(read(tokens)).toMatchObject({ state: 'value', values: [value], incomplete: false })
  })

  it.each(['-x', 'x', '=x', '', '-', '--', '-1', 'a=b', ' sp ace'])('applyEdit → readTokens 往返：%j', (value) => {
    for (const start of [[], ['-k=old'], ['-kold'], ['-k', 'old']]) {
      const r = applyEdit(schema, start, { op: 'set', arg: k.id, values: [value] })
      if (!r.ok) continue
      expect(read(r.tokens)).toMatchObject({ state: 'value', values: [value], incomplete: false })
    }
  })

  it('choices=[-x] 的往返：写成 -k=-x，读回 -x，不是 =-x', () => {
    const r = applyEdit(schema, [], { op: 'set', arg: k.id, values: ['-x'] })
    expect(r).toEqual({ ok: true, tokens: ['-k=-x'] })
    expect(read((r as { tokens: string[] }).tokens).values).toEqual(['-x'])
  })
})
