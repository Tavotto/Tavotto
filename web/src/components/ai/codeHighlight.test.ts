import { describe, expect, it } from 'vitest'
import { highlightPythonLine, isPythonish } from './codeHighlight'

describe('codeHighlight（在 pythonHighlight 之上细分 function / builtin / type）', () => {
  it('拼回去与原行逐字相同', () => {
    const line = 'ax.yaxis.set_major_formatter(LogFormatterMathtext())  # 10 的幂'
    expect(highlightPythonLine(line).map((t) => t.text).join('')).toBe(line)
  })

  it('调用是 function，内置名是 builtin，类名是 type，其余原词法不动', () => {
    const kinds = Object.fromEntries(
      highlightPythonLine('for i in range(3): ax.plot(Path(x), "s", 1.5)').map((t) => [t.text, t.kind]),
    )
    expect(kinds.for).toBe('keyword')
    expect(kinds.range).toBe('builtin')
    expect(kinds.plot).toBe('function')
    expect(kinds.Path).toBe('function') // 紧跟 ( 的类名按调用上色
    expect(kinds['"s"']).toBe('string')
    expect(kinds['1.5']).toBe('number')
    expect(highlightPythonLine('x: Figure = None').find((t) => t.text === 'Figure')?.kind).toBe('type')
  })

  it('没写语言的块按 Python 高亮；别的语言不', () => {
    expect(isPythonish('')).toBe(true)
    expect(isPythonish('python')).toBe(true)
    expect(isPythonish('bash')).toBe(false)
  })
})
