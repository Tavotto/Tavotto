/**
 * 粘贴命令 → token（T07）：与 Python `shlex.split` 对拍（`tests/golden/argv_paste_vectors.json`，后端那一侧
 * `tests/test_script_args.py` 证明 `words` 就是 shlex 的结果）。被拒的那些给稳定码，界面提示改用逐项填写。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/argv_paste_vectors.json'
import { parsePastedCommand } from './argvPaste'

describe('粘贴命令（golden，posix）', () => {
  it.each(golden.accepted)('接受：$text', (v) => {
    expect(parsePastedCommand(v.text, v.script, 'posix')).toEqual({ ok: true, words: v.words, argv: v.argv })
  })
  it.each(golden.rejected)('拒绝：$text', (v) => {
    expect(parsePastedCommand(v.text, v.script, 'posix')).toEqual({ ok: false, error: v.error })
  })
})

describe('粘贴命令（golden，Windows 命令行规则）', () => {
  const win = [...golden.windows.accepted, ...golden.windows.single_quote]
  it.each(win)('接受：$text', (v) => {
    expect(parsePastedCommand(v.text, v.script, 'windows')).toEqual({ ok: true, words: v.words, argv: v.argv })
  })
  it.each(golden.windows.rejected)('拒绝：$text', (v) => {
    expect(parsePastedCommand(v.text, v.script, 'windows')).toEqual({ ok: false, error: v.error })
  })
  it('同一条 Windows 路径：posix 会吃掉反斜杠，windows 原样保留（用户失败的形状）', () => {
    const text = String.raw`python plot.py --input F:\data\t1\a.xlsx`
    expect(parsePastedCommand(text, 'plot.py', 'posix')).toMatchObject({ argv: ['--input', 'F:datat1a.xlsx'] })
    expect(parsePastedCommand(text, 'plot.py', 'windows')).toMatchObject({ argv: ['--input', String.raw`F:\data\t1\a.xlsx`] })
  })
})
