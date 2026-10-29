import { TextSelection, type EditorState, type Transaction } from 'prosemirror-state'
import { describe, expect, it } from 'vitest'
import {
  annotationCodec,
  createRichState,
  docToRuns,
  insertBreak,
  markActive,
  runsToDoc,
  toggleRichMark,
} from './richEditor'
import type { TextRun } from './richText'

/** 在纯状态上跑一条命令（不挂 DOM），回新状态 */
function apply(state: EditorState, cmd: (s: EditorState, d: (tr: Transaction) => void) => boolean) {
  let next = state
  cmd(state, (tr) => (next = state.apply(tr)))
  return next
}
const select = (state: EditorState, from: number, to: number) =>
  state.apply(state.tr.setSelection(TextSelection.create(state.doc, from, to)))
const latex = annotationCodec('latex')

describe('片段 ↔ 编辑框文档', () => {
  const cases: [string, TextRun[]][] = [
    ['纯文字', [{ text: 'abc', script: '' }]],
    ['斜体', [{ text: 'T ', script: '' }, { text: 'x', script: '', italic: true }]],
    ['粗斜体 + 下标', [{ text: 'x', script: '', italic: true, bold: true }, { text: 'i', script: 'sub', italic: true }]],
    ['换行', [{ text: 'a\nb', script: '' }]],
    ['公式块', [{ text: 'k = ', script: '' }, { text: 'A e^{-E}', script: '', math: true }]],
    ['读不懂的命令', [{ text: '\\textsc{abc}', script: '', raw: true }, { text: ' d', script: '' }]],
    ['空', []],
  ]
  for (const [name, runs] of cases) {
    it(name, () => expect(docToRuns(runsToDoc(runs))).toEqual(runs))
  }

  it('打开不改：标注字符串经编辑框读写不变（规范形）', () => {
    for (const s of ['Temperature \\textit{T} (K)', '\\textbf{a \\textit{b}} c', 'k = $A e^{-E}$', '\\textsc{x} y']) {
      expect(latex.serialize(docToRuns(runsToDoc(latex.parse(s))))).toBe(s)
    }
  })

  it('老标注（旧标记）读进来，写回成 LaTeX 子集，画出来一样', () => {
    const legacy = annotationCodec(undefined)
    expect(legacy.serialize(docToRuns(runsToDoc(legacy.parse('cm^{-1}'))))).toBe('cm\\textsuperscript{-1}')
  })
})

describe('样式命令作用在选区上', () => {
  it('选中 T 点斜体：只有 T 变斜体，再点取消', () => {
    let s = createRichState(latex.parse('Temperature T (K)'))
    s = select(s, 12, 13) // 文档顶层就是行内内容：位置 = 字符偏移
    s = apply(s, toggleRichMark('italic'))
    expect(latex.serialize(docToRuns(s.doc))).toBe('Temperature \\textit{T} (K)')
    expect(markActive(s, 'italic')).toBe(true)
    s = apply(s, toggleRichMark('italic'))
    expect(latex.serialize(docToRuns(s.doc))).toBe('Temperature T (K)')
  })

  it('上下标互斥：给上标的字点下标，变成下标', () => {
    let s = select(createRichState(latex.parse('x2')), 1, 2)
    s = apply(s, toggleRichMark('sup'))
    s = apply(s, toggleRichMark('sub'))
    expect(docToRuns(s.doc)).toEqual([{ text: 'x', script: '' }, { text: '2', script: 'sub' }])
  })

  it('没有选区时点粗体：接下来打的字是粗体', () => {
    let s = select(createRichState(latex.parse('a')), 1, 1)
    s = apply(s, toggleRichMark('bold'))
    expect(markActive(s, 'bold')).toBe(true)
    s = s.apply(s.tr.insertText('b'))
    expect(latex.serialize(docToRuns(s.doc))).toBe('a\\textbf{b}')
  })

  it('插入换行', () => {
    let s = select(createRichState(latex.parse('ab')), 1, 1)
    s = apply(s, insertBreak)
    expect(docToRuns(s.doc)).toEqual([{ text: 'a\nb', script: '' }])
  })

  it('挨着读不懂的命令打字，新字不继承 raw（写回会被转义，不会拼进命令里）', () => {
    let s = createRichState(latex.parse('\\textsc{a}'))
    s = select(s, s.doc.content.size, s.doc.content.size)
    s = s.apply(s.tr.insertText('{'))
    expect(latex.serialize(docToRuns(s.doc))).toBe('\\textsc{a}\\{')
  })
})

describe('写法表达不了的样式', () => {
  it('图内文字里的中文斜体写回时丢样式（mathtext 画不出），读回来也是正体——框要跟着同步', async () => {
    const { figureTextCodec } = await import('./richEditor')
    const runs: TextRun[] = [{ text: '温度', script: '', italic: true }]
    const text = figureTextCodec.serialize(runs)
    expect(figureTextCodec.parse(text)).toEqual([{ text: '温度', script: '' }])
  })
})

describe('codec 的读回', () => {
  it('老标注的 codec：写出 LaTeX 子集，读回也按 LaTeX 子集（不把命令当字）', () => {
    const legacy = annotationCodec(undefined)
    const runs: TextRun[] = [{ text: 'H', script: '', italic: true }, { text: '2O', script: '' }]
    const out = legacy.serialize(runs)
    expect(out).toBe('\\textit{H}2O')
    expect(legacy.reparse!(out)).toEqual(runs)
  })
})

describe('剪贴板（Codex #1489）', () => {
  it('自己复制出去的上下标 / raw 标签粘回来认得', async () => {
    const { DOMParser: PMParser, DOMSerializer } = await import('prosemirror-model')
    const { richSchema } = await import('./richEditor')
    const runs: TextRun[] = [
      { text: 'x', script: '' },
      { text: '2', script: 'sup' },
      { text: 'i', script: 'sub' },
      { text: '\\textsc{a}', script: '', raw: true },
    ]
    const frag = DOMSerializer.fromSchema(richSchema).serializeFragment(runsToDoc(runs).content)
    const div = document.createElement('div')
    div.appendChild(frag)
    expect(docToRuns(PMParser.fromSchema(richSchema).parse(div))).toEqual(runs)
  })
})
