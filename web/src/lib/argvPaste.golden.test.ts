/**
 * 粘贴命令 → token（T07）：与 Python `shlex.split` 对拍（`tests/golden/argv_paste_vectors.json`，后端那一侧
 * `tests/test_script_args.py` 证明 `words` 就是 shlex 的结果）。被拒的那些给稳定码，界面提示改用逐项填写。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/argv_paste_vectors.json'
import { parsePastedCommand } from './argvPaste'

describe('粘贴命令（golden）', () => {
  it.each(golden.accepted)('接受：$text', (v) => {
    expect(parsePastedCommand(v.text, v.script)).toEqual({ ok: true, words: v.words, argv: v.argv })
  })
  it.each(golden.rejected)('拒绝：$text', (v) => {
    expect(parsePastedCommand(v.text, v.script)).toEqual({ ok: false, error: v.error })
  })
})
