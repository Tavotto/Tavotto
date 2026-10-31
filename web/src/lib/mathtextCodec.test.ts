import { describe, expect, it } from 'vitest'
import { parseMathtext, serializeMathtext } from './mathtextCodec'
import type { TextRun } from './richText'

const plain = (text: string): TextRun => ({ text, script: '' })
const rt = (s: string) => serializeMathtext(parseMathtext(s))

describe('已有字符串逐字回写', () => {
  const cases = [
    'cm$^{-1}$',
    'H$_{2}$O',
    'Temperature $\\mathit{T}$ (K)',
    '$\\alpha$ (rad)',
    '$10^{-5}$',
    '$\\mathrm{min^{-1}}$',
    'price \\$5',
    'a $b',
    '$x$ and $y$',
    'plain text',
    'line1\nline2',
    '\n',
    '',
    // 按行判定：每行各自的 `$` 奇偶
    '$x\ny$',
    '$x$\n$y$',
    'a $b\n$x$',
    'a \\$ b $c',
    '\\$5 $x$\n\\$6',
    '$a$ b $c$ d $e',
    '$$',
    '$$x$$',
    '\\\\$x$',
    'a\\b $x$',
    '$\\mathit{a\\ b}$',
    '$\\mathit{50%}$', // 非规范（% 没转义）→ 芯片，原样
    '$^{n}$', // 裸字母 = 斜体，非规范 → 芯片
    '$^{\\mathrm{12}}$', // 无字母却写成 mathrm，非规范 → 芯片
  ]
  for (const s of cases) {
    it(JSON.stringify(s), () => expect(rt(s)).toBe(s))
  }
})

describe('parseMathtext 规范段', () => {
  it('空串与纯文字', () => {
    expect(parseMathtext('')).toEqual([])
    expect(parseMathtext('a\nb')).toEqual([plain('a\nb')])
  })
  it('斜体 / 粗体 / 粗斜体', () => {
    expect(parseMathtext('$\\mathit{B}$')).toEqual([{ text: 'B', script: '', italic: true }])
    expect(parseMathtext('$\\mathbf{B}$')).toEqual([{ text: 'B', script: '', bold: true }])
    expect(parseMathtext('$\\mathbfit{B}$')).toEqual([
      { text: 'B', script: '', italic: true, bold: true },
    ])
  })
  it('上下标：裸的无字母正文是直立', () => {
    expect(parseMathtext('cm$^{-1}$')).toEqual([plain('cm'), { text: '-1', script: 'sup' }])
    expect(parseMathtext('H$_{2}$O')).toEqual([
      plain('H'),
      { text: '2', script: 'sub' },
      plain('O'),
    ])
    expect(parseMathtext('$^{+}$')).toEqual([{ text: '+', script: 'sup' }])
  })
  it('上下标：带样式 / 直立含字母', () => {
    expect(parseMathtext('$^{\\mathit{n}}$')).toEqual([
      { text: 'n', script: 'sup', italic: true },
    ])
    expect(parseMathtext('$_{\\mathbf{n}}$')).toEqual([{ text: 'n', script: 'sub', bold: true }])
    expect(parseMathtext('$^{\\mathbfit{n}}$')).toEqual([
      { text: 'n', script: 'sup', italic: true, bold: true },
    ])
    expect(parseMathtext('$^{\\mathrm{max}}$')).toEqual([{ text: 'max', script: 'sup' }])
  })
  it('正文转义：空格与 \\{ \\} \\$ \\% \\_', () => {
    expect(parseMathtext('$\\mathit{a\\ b\\{c\\}\\$\\%\\_}$')).toEqual([
      { text: 'a b{c}$%_', script: '', italic: true },
    ])
  })
  it('其它一律是公式芯片，源码原样', () => {
    expect(parseMathtext('$\\alpha$ (rad)')).toEqual([
      { text: '\\alpha', script: '', math: true },
      plain(' (rad)'),
    ])
    expect(parseMathtext('$10^{-5}$')).toEqual([{ text: '10^{-5}', script: '', math: true }])
    expect(parseMathtext('$\\mathrm{min^{-1}}$')).toEqual([
      { text: '\\mathrm{min^{-1}}', script: '', math: true },
    ])
    // 普通空格会被 mathtext 吞掉，不是规范写法
    expect(parseMathtext('$\\mathit{a b}$')).toEqual([
      { text: '\\mathit{a b}', script: '', math: true },
    ])
    expect(parseMathtext('$^{n}$')).toEqual([{ text: '^{n}', script: '', math: true }])
    expect(parseMathtext('$$')).toEqual([{ text: '', script: '', math: true }])
  })
})

describe('parseMathtext 的 $ 规则（matplotlib 3.11 实测）', () => {
  it('奇数个未转义 $：整行纯文本，$ 是 raw', () => {
    expect(parseMathtext('a $b')).toEqual([
      plain('a '),
      { text: '$', script: '', raw: true },
      plain('b'),
    ])
  })
  it('无数学段时 \\$ 显示成 $', () => {
    expect(parseMathtext('price \\$5')).toEqual([plain('price $5')])
  })
  it('有数学段时 \\$ 也是字面 $', () => {
    expect(parseMathtext('\\$5 $x$')).toEqual([
      plain('$5 '),
      { text: 'x', script: '', math: true },
    ])
  })
  it('按行判定：数学段不跨行', () => {
    expect(parseMathtext('$x\ny$')).toEqual([
      { text: '$', script: '', raw: true },
      plain('x\ny'),
      { text: '$', script: '', raw: true },
    ])
    expect(parseMathtext('a $b\n$x$')).toEqual([
      plain('a '),
      { text: '$', script: '', raw: true },
      plain('b\n'),
      { text: 'x', script: '', math: true },
    ])
  })
})

describe('serializeMathtext', () => {
  it('普通文字里的 $ 写成 \\$', () => {
    expect(serializeMathtext([plain('price $5')])).toBe('price \\$5')
    expect(serializeMathtext([plain('a $ b '), { text: 'x', script: '', italic: true }])).toBe(
      'a \\$ b $\\mathit{x}$',
    )
  })
  it('规范写法', () => {
    expect(serializeMathtext([{ text: 'a b', script: '', italic: true }])).toBe(
      '$\\mathit{a\\ b}$',
    )
    expect(serializeMathtext([{ text: '-1', script: 'sup' }])).toBe('$^{-1}$')
    expect(serializeMathtext([{ text: 'max', script: 'sup' }])).toBe('$^{\\mathrm{max}}$')
    expect(serializeMathtext([{ text: 'n', script: 'sub', italic: true, bold: true }])).toBe(
      '$_{\\mathbfit{n}}$',
    )
    expect(serializeMathtext([{ text: 'E=mc^2', script: '', math: true }])).toBe('$E=mc^2$')
    expect(serializeMathtext([{ text: '', script: '', math: true }])).toBe('$$')
  })
  it('不合并相邻 run：一个 run 一段', () => {
    expect(
      serializeMathtext([
        { text: 'a', script: '', italic: true },
        { text: 'b', script: '', italic: true },
      ]),
    ).toBe('$\\mathit{a}$$\\mathit{b}$')
  })
  it('raw 的 $：仅在纯文本行且结果仍是纯文本时原样写', () => {
    const raw: TextRun = { text: '$', script: '', raw: true }
    expect(serializeMathtext([plain('a '), raw, plain('b')])).toBe('a $b')
    // 偶数个会把行翻成数学行 → 改写成 \$
    expect(serializeMathtext([raw, plain('x'), raw])).toBe('\\$x\\$')
    // 该行有规范段 → 不能留未转义的 $
    expect(serializeMathtext([raw, { text: 'x', script: '', italic: true }])).toBe(
      '\\$$\\mathit{x}$',
    )
  })
  it('普通文字以 \\ 结尾、后面紧跟规范段：\\ 写成 $\\backslash$', () => {
    expect(serializeMathtext([plain('a\\'), { text: 'x', script: '', italic: true }])).toBe(
      'a$\\backslash$$\\mathit{x}$',
    )
  })
  it('不能放进 $…$ 的芯片退成普通文字', () => {
    expect(serializeMathtext([{ text: 'a$b', script: '', math: true }])).toBe('a\\$b')
    expect(serializeMathtext([{ text: 'a\\', script: '', math: true }])).toBe('a\\')
  })
})

/** 把相邻、同样式的普通 run 合并，并把带样式 run 在 `\n` 处拆开——parse 的规范形。 */
function normalise(runs: TextRun[]): TextRun[] {
  const out: TextRun[] = []
  const isPlain = (r: TextRun) => !r.italic && !r.bold && !r.math && !r.raw && r.script === ''
  const push = (r: TextRun) => {
    const last = out[out.length - 1]
    if (isPlain(r) && last && isPlain(last)) last.text += r.text
    else out.push({ ...r })
  }
  for (const r of runs) {
    if (isPlain(r) || r.raw) {
      if (r.text) push(r)
      continue
    }
    if (r.math && r.text === '') {
      push(r)
      continue
    }
    r.text.split('\n').forEach((part, k) => {
      if (k > 0) push(plain('\n'))
      if (part) push({ ...r, text: part })
    })
  }
  return out
}

describe('编辑器能产出的 run 列表：serialize 再 parse 回到自身', () => {
  const it_: TextRun = { text: 'T', script: '', italic: true }
  const table: Record<string, TextRun[]> = {
    斜体: [plain('Temperature '), it_, plain(' (K)')],
    粗体: [{ text: 'Bold', script: '', bold: true }],
    粗斜体: [{ text: 'Both', script: '', bold: true, italic: true }],
    上标无字母: [plain('cm'), { text: '-1', script: 'sup' }],
    下标无字母: [plain('H'), { text: '2', script: 'sub' }, plain('O')],
    上标有字母直立: [plain('x'), { text: 'max', script: 'sup' }],
    下标有字母直立: [plain('x'), { text: 'eff', script: 'sub' }],
    上标斜体: [plain('x'), { text: 'n', script: 'sup', italic: true }],
    下标粗斜体: [plain('x'), { text: 'n', script: 'sub', bold: true, italic: true }],
    上标粗体: [plain('x'), { text: 'n', script: 'sup', bold: true }],
    带空格和特殊符号: [{ text: 'a b {c} $d% e_f', script: '', italic: true }],
    上标里有空格: [{ text: '1 2', script: 'sup' }],
    上标全是空格: [{ text: ' ', script: 'sup' }],
    希腊与带音符: [{ text: 'αβ é', script: '', bold: true }],
    样式内换行: [plain('a'), { text: 'x\ny', script: '', italic: true }, plain('b')],
    样式首尾换行: [{ text: '\nx\n', script: 'sub', italic: true }],
    公式芯片: [plain('Energy '), { text: '\\alpha^{2}', script: '', math: true }, plain(' (eV)')],
    空公式芯片: [{ text: '', script: '', math: true }],
    普通文字含美元: [plain('price $5 and \\$ done')],
    普通文字含美元加段: [plain('$5 '), it_],
    普通文字含反斜杠: [plain('a\\b '), it_],
    多行混合: [plain('line1\n'), it_, plain('\nline3 '), { text: '2', script: 'sup' }],
    相邻同样式普通run: [plain('a'), plain('b'), it_, plain('c'), plain('d')],
    相邻不同样式段: [
      { text: 'a', script: '', italic: true },
      { text: 'b', script: '', bold: true },
      { text: '1', script: 'sup' },
    ],
    混合: [
      plain('Rate ('),
      { text: 'k', script: '', italic: true },
      { text: 'B', script: 'sub' },
      plain(') / s'),
      { text: '-1', script: 'sup' },
      plain(' '),
      { text: '\\sigma', script: '', math: true },
    ],
  }
  for (const [name, runs] of Object.entries(table)) {
    it(name, () => {
      const s = serializeMathtext(runs)
      expect(parseMathtext(s)).toEqual(normalise(runs))
      // 再写一次是不动点
      expect(rt(s)).toBe(s)
    })
  }
})

describe('限制：mathtext 写不出的字符', () => {
  // 实测：`^ ~ _` 在 \mathit{} 里被无声吞掉，`\` `#` `&` `"` `'` `` ` `` 报错或变形
  for (const ch of ['^', '\\', '~', '#', '&', '"', "'", '`', '中']) {
    it(`含 ${ch} 的斜体 run 退成无样式纯文字`, () => {
      const s = serializeMathtext([{ text: `a${ch}b`, script: '', italic: true }])
      expect(s).toBe(`a${ch}b`)
      expect(parseMathtext(s)).toEqual([plain(`a${ch}b`)])
    })
  }
  it('上标含 ^ 也丢上标', () => {
    expect(serializeMathtext([{ text: '2^3', script: 'sup' }])).toBe('2^3')
  })
  it('只影响含这些字符的那个 run', () => {
    expect(
      serializeMathtext([
        { text: 'a^b', script: '', italic: true },
        { text: 'c', script: '', italic: true },
      ]),
    ).toBe('a^b$\\mathit{c}$')
  })
})

describe('反斜杠在公式段外原样（matplotlib 只认 \\$ 一种转义）', () => {
  it('字面反斜杠、反斜杠 + 美元、反斜杠结尾都原样往返', () => {
    for (const text of ['C:\\data', 'a\\$b', 'end\\']) {
      const runs = [{ text, script: '' as const }]
      expect(parseMathtext(serializeMathtext(runs))).toEqual(runs)
    }
  })
})
