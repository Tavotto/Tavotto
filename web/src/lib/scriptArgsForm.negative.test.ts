/**
 * 负数 token 的文法随 worker 的 Python 版本变（Codex r4220829672）：
 * - Python <= 3.13：`^-\d+$|^-\d*\.\d+$`；- Python 3.14：`-\.?\d`（`.match`，无结尾锚）。
 * 后端同一份判据在 `tests/test_script_args.py`（按 CPython 的 `_negative_number_matcher` 逐字对拍）。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/script_args_form_vectors.json'
import {
  applyEdit,
  type FormEdit,
  looksLikeOption,
  readTokens,
  usesExtendedNegativeGrammar,
  type ScriptArgsSchema,
} from './scriptArgsForm'

const base = golden.schemas.fft6 as unknown as ScriptArgsSchema
const withVersion = (python_version: string | null | undefined): ScriptArgsSchema => ({
  ...base,
  python_version,
})

// token → [3.13 及更早是否算选项, 3.14 是否算选项]
const TABLE: [string, boolean, boolean][] = [
  ['-1', false, false],
  ['-1.5', false, false],
  ['-.5', false, false],
  ['-1.', true, false],
  ['-1e3', true, false],
  ['-1.5E-3', true, false],
  ['-1_0', true, false],
  ['-1j', true, false],
  ['-1abc', true, false],
  ['-inf', true, true],
  ['-x', true, true],
  ['-', false, false],
]

describe('负数 token 文法按 worker Python 版本选择', () => {
  it.each(TABLE)('%s', (token, optionOn313, optionOn314) => {
    expect(looksLikeOption(withVersion('3.13.13'), token)).toBe(optionOn313)
    expect(looksLikeOption(withVersion('3.12.12'), token)).toBe(optionOn313)
    expect(looksLikeOption(withVersion('3.14.7'), token)).toBe(optionOn314)
    expect(looksLikeOption(withVersion('3.14'), token)).toBe(optionOn314)
    expect(looksLikeOption(withVersion('3.15.0'), token)).toBe(optionOn314)
  })

  it('版本不明时取较窄的旧文法（更保守：把更多 token 当选项）', () => {
    for (const v of [undefined, null, '', 'garbage']) {
      expect(usesExtendedNegativeGrammar(withVersion(v))).toBe(false)
      expect(looksLikeOption(withVersion(v), '-1e3')).toBe(true)
    }
  })

  it('3.14 起声明了像负数的选项名时，负数 token 仍是选项（与旧文法同一条规则，各用各的标志）', () => {
    const legacyOnly = { ...base, negative_number_options: false, negative_number_options_extended: true }
    expect(looksLikeOption({ ...legacyOnly, python_version: '3.13.1' }, '-1')).toBe(false)
    expect(looksLikeOption({ ...legacyOnly, python_version: '3.14.1' }, '-1')).toBe(true)
    // 老后端没有 extended 键：3.14 沿用旧标志
    const old = { ...base, negative_number_options: true }
    delete (old as Partial<ScriptArgsSchema>).negative_number_options_extended
    expect(looksLikeOption({ ...old, python_version: '3.14.1' }, '-1')).toBe(true)
  })

  it('读回 `--freq -1e3`：3.14 当作值，3.13 当作缺值 + 另一个选项（3.13 的 parser 确实如此）', () => {
    const flag = base.arguments.find((a) => a.flags.includes('--freq'))!
    const tokens = ['--freq', '-1e3']
    const on314 = readTokens(withVersion('3.14.7'), tokens)
    expect(on314.fields[flag.id]).toMatchObject({ state: 'value', values: ['-1e3'], incomplete: false })
    for (const v of ['3.13.13', null]) {
      const view = readTokens(withVersion(v), tokens)
      expect(view.fields[flag.id].incomplete).toBe(true)
      expect(view.fields[flag.id].values).toEqual([])
    }
  })

  it('表单写 `-1e3`：3.14 是分开的两个 token，3.13 / 版本不明用 `--freq=-1e3`（两种都是各自 parser 认的）', () => {
    const flag = base.arguments.find((a) => a.flags.includes('--freq'))!
    const edit: FormEdit = { op: 'set', arg: flag.id, values: ['-1e3'] }
    expect(applyEdit(withVersion('3.14.7'), [], edit)).toEqual({ ok: true, tokens: ['--freq', '-1e3'] })
    for (const v of ['3.13.13', null]) {
      expect(applyEdit(withVersion(v), [], edit)).toEqual({ ok: true, tokens: ['--freq=-1e3'] })
    }
  })
})
