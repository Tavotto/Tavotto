/** 轻量高亮：token 拼回去必须逐字节等于原文（高亮绝不改内容）。 */
import { describe, expect, it } from 'vitest'
import { EXAMPLES } from './examples'
import { tokenizePython, tokenizePythonLine } from './pythonHighlight'

describe('pythonHighlight', () => {
  it('token 逐行拼回去等于原文（三个案例全量）', () => {
    for (const ex of EXAMPLES) {
      const lines = ex.source.replace(/\n$/, '').split('\n')
      const tokenized = tokenizePython(ex.source)
      expect(tokenized).toHaveLength(lines.length)
      tokenized.forEach((tokens, i) => {
        expect(tokens.map((t) => t.text).join('')).toBe(lines[i])
      })
    }
  })

  it('注释 / 字符串 / 数字 / 关键字各归各类', () => {
    const t = tokenizePythonLine('import numpy as np  # load numpy 2.0')
    expect(t.find((x) => x.text === 'import')?.kind).toBe('keyword')
    expect(t.find((x) => x.text === 'as')?.kind).toBe('keyword')
    expect(t.find((x) => x.kind === 'comment')?.text).toBe('# load numpy 2.0')

    const s = tokenizePythonLine('ax.set_title("Reaction kinetics", fontsize=9)')
    expect(s.find((x) => x.kind === 'string')?.text).toBe('"Reaction kinetics"')
    expect(s.find((x) => x.kind === 'number')?.text).toBe('9')

    const f = tokenizePythonLine('label=f"Fit: y = {k:.2f}x"')
    expect(f.find((x) => x.kind === 'string')?.text).toBe('f"Fit: y = {k:.2f}x"')
  })

  it('井号在字符串里不是注释', () => {
    const t = tokenizePythonLine('color="#b03a2e"')
    expect(t.find((x) => x.kind === 'comment')).toBeUndefined()
    expect(t.find((x) => x.kind === 'string')?.text).toBe('"#b03a2e"')
  })
})
