/**
 * 所见即所得文字编辑框的核心（ADR 0152 PR 2）：ProseMirror 文档 ↔ 片段列表、样式命令与键位。
 *
 * **字符串仍是唯一权威**：编辑框打开时 `codec.parse` 出片段、建文档；用户每改一次就
 * `docToRuns` → `codec.serialize` 写回规范形。打开不编辑不写回（只有 `docChanged` 的事务才出声）。
 * 两种写法各有一个 codec：标注走 `richText.ts` 的 LaTeX 子集（老标注按旧标记读、第一次保存时转换），
 * 图内文字走 `mathtextCodec.ts`。本模块不认写法，只认 `TextRun`。
 *
 * 不含 React；`components/ui/RichTextField.tsx` 把它挂进界面。
 */
import { baseKeymap, toggleMark } from 'prosemirror-commands'
import { history, redo, undo } from 'prosemirror-history'
import { keymap } from 'prosemirror-keymap'
import { Fragment, Schema, type Mark, type MarkType, type Node as PMNode } from 'prosemirror-model'
import { EditorState, type Command, type Plugin } from 'prosemirror-state'
import { parseMathtext, serializeMathtext } from './mathtextCodec'
import {
  parseRuns,
  SCRIPT_SIZE,
  serializeRuns,
  SUB_DROP,
  SUP_RISE,
  type TextFormat,
  type TextRun,
} from './richText'

/** 一种写法 = 一对 parse / serialize。编辑框只认片段，不认写法。 */
export interface RichCodec {
  /** 读**存着的**字符串（它的写法可能是旧的：老标注是旧标记） */
  parse(text: string): TextRun[]
  serialize(runs: TextRun[]): string
  /** 读 `serialize` 写出来的字符串；缺省同 `parse`（写法读写一致时） */
  reparse?(text: string): TextRun[]
}

const ANNOTATION_CODECS = new Map<TextFormat | undefined, RichCodec>()

/**
 * 标注的写法：按对象**现在**的写法读（老标注是旧标记），写回一律是 LaTeX 子集——宿主在
 * `onChange` 里同时把 `textFormat` 设成 `'latex'`（ADR 0152：第一次用新编辑框保存时转换）。
 */
export function annotationCodec(format?: TextFormat): RichCodec {
  let c = ANNOTATION_CODECS.get(format)
  if (!c) {
    c = {
      parse: (text) => parseRuns(text, format),
      serialize: (runs) => serializeRuns(runs, 'latex'),
      reparse: (text) => parseRuns(text, 'latex'),
    }
    ANNOTATION_CODECS.set(format, c)
  }
  return c
}

/** 图内文字（matplotlib 的文字元素）的写法：mathtext，见 `mathtextCodec.ts` */
export const figureTextCodec: RichCodec = { parse: parseMathtext, serialize: serializeMathtext }

/**
 * 上下标的字号与基线用 `--rt-size`（宿主写的正文字号，px）算绝对值，不用 em：`vertical-align`
 * 的长度相对元素自身字号解析，套在缩小后的 span 上会再乘一次比例（与 `TextView` 同一个理由）。
 */
const scriptStyle = (rise: number) =>
  `font-size:calc(var(--rt-size, 1em) * ${SCRIPT_SIZE});` +
  `vertical-align:calc(var(--rt-size, 1em) * ${rise});line-height:0`

export const richSchema = new Schema({
  nodes: {
    doc: { content: 'inline*' },
    text: { group: 'inline' },
    hard_break: {
      inline: true,
      group: 'inline',
      selectable: false,
      parseDOM: [{ tag: 'br' }],
      toDOM: () => ['br'],
    },
    /** 公式块：源码原样保存（PR 4 起换成排版结果），整块选中、整块删除 */
    math: {
      inline: true,
      group: 'inline',
      atom: true,
      attrs: { src: { default: '' } },
      parseDOM: [
        {
          tag: 'span[data-rt-math]',
          getAttrs: (el) => ({ src: (el as HTMLElement).getAttribute('data-rt-math') ?? '' }),
        },
      ],
      toDOM: (n) => [
        'span',
        { 'data-rt-math': n.attrs.src as string, contenteditable: 'false', class: 'rt-math' },
        `$${n.attrs.src as string}$`,
      ],
    },
  },
  marks: {
    italic: {
      parseDOM: [{ tag: 'i' }, { tag: 'em' }, { style: 'font-style=italic' }],
      toDOM: () => ['em', { 'data-rt': 'italic', style: 'font-style:italic' }, 0],
    },
    bold: {
      parseDOM: [
        { tag: 'b' },
        { tag: 'strong' },
        { style: 'font-weight', getAttrs: (v) => (/^(bold|[6-9]\d\d)$/.test(v as string) ? null : false) },
      ],
      toDOM: () => ['strong', { 'data-rt': 'bold', style: 'font-weight:700' }, 0],
    },
    sup: {
      excludes: 'sup sub',
      parseDOM: [{ tag: 'sup' }],
      toDOM: () => ['span', { 'data-rt': 'sup', style: scriptStyle(SUP_RISE) }, 0],
    },
    sub: {
      excludes: 'sup sub',
      parseDOM: [{ tag: 'sub' }],
      toDOM: () => ['span', { 'data-rt': 'sub', style: scriptStyle(-SUB_DROP) }, 0],
    },
    /** 读不懂的命令原文（`\textsc{abc}`）：照字面显示、写回原样；挨着它打的字不继承 */
    raw: {
      inclusive: false,
      toDOM: () => ['span', { 'data-rt': 'raw' }, 0],
    },
  },
})

const M = richSchema.marks
const N = richSchema.nodes

/** 片段 → 编辑框文档。换行变成 `hard_break`。 */
export function runsToDoc(runs: TextRun[]): PMNode {
  const nodes: PMNode[] = []
  for (const r of runs) {
    if (r.math) {
      nodes.push(N.math.create({ src: r.text }))
      continue
    }
    const marks: Mark[] = []
    if (r.italic) marks.push(M.italic.create())
    if (r.bold) marks.push(M.bold.create())
    if (r.script === 'sup') marks.push(M.sup.create())
    if (r.script === 'sub') marks.push(M.sub.create())
    if (r.raw) marks.push(M.raw.create())
    r.text.split('\n').forEach((part, k) => {
      if (k) nodes.push(N.hard_break.create())
      if (part) nodes.push(richSchema.text(part, marks))
    })
  }
  return N.doc.create(null, Fragment.from(nodes))
}

/** 编辑框文档 → 片段（相邻同样式的片段合并；换行并进不带样式的片段）。 */
export function docToRuns(doc: PMNode): TextRun[] {
  const out: TextRun[] = []
  const push = (r: TextRun) => {
    const last = out[out.length - 1]
    if (
      last && !last.math && !r.math &&
      last.script === r.script && !!last.italic === !!r.italic &&
      !!last.bold === !!r.bold && !!last.raw === !!r.raw
    ) last.text += r.text
    else out.push(r)
  }
  doc.forEach((node) => {
    if (node.type === N.math) {
      out.push({ text: node.attrs.src as string, script: '', math: true })
      return
    }
    if (node.type === N.hard_break) {
      push({ text: '\n', script: '' })
      return
    }
    const has = (t: MarkType) => t.isInSet(node.marks) !== undefined
    const r: TextRun = { text: node.text ?? '', script: has(M.sup) ? 'sup' : has(M.sub) ? 'sub' : '' }
    if (has(M.italic)) r.italic = true
    if (has(M.bold)) r.bold = true
    if (has(M.raw)) r.raw = true
    push(r)
  })
  return out
}

export type RichMark = 'italic' | 'bold' | 'sup' | 'sub'

/** 选区（或光标处将要打的字）是否带着这个样式——工具条按钮的「按下」态。 */
export function markActive(state: EditorState, mark: RichMark): boolean {
  const type = M[mark]
  const { from, to, empty, $from } = state.selection
  if (empty) return !!type.isInSet(state.storedMarks ?? $from.marks())
  return state.doc.rangeHasMark(from, to, type)
}

/** 切换一种样式；上下标互斥（schema 的 `excludes`）。 */
export const toggleRichMark = (mark: RichMark): Command => toggleMark(M[mark])

/** 在光标处插入换行 */
export const insertBreak: Command = (state, dispatch) => {
  dispatch?.(state.tr.replaceSelectionWith(N.hard_break.create()).scrollIntoView())
  return true
}

export interface RichKeyOptions {
  /**
   * 裸 Enter 的语义：返回 true = 宿主处理了（画布上提交、快捷编辑里关闭）；
   * 缺省或返回 false = 插入换行（属性栏里的多行文字框）。
   */
  onEnter?: () => boolean
  onEscape?: () => boolean
}

/**
 * 键位：⌘B / ⌘I 粗斜体，⌘↑ / ⌘↓ 上下标（沿用文字框原有键位），⌥/⌘/Ctrl+Enter 一律换行，
 * ⌘Z / ⇧⌘Z 撤销重做——编辑期内的撤销归编辑框自己的历史，失焦后整段编辑在文档历史里是一条。
 */
export function richPlugins(opts: RichKeyOptions = {}): Plugin[] {
  return [
    history(),
    keymap({
      'Mod-b': toggleRichMark('bold'),
      'Mod-i': toggleRichMark('italic'),
      'Mod-ArrowUp': toggleRichMark('sup'),
      'Mod-ArrowDown': toggleRichMark('sub'),
      'Mod-z': undo,
      'Shift-Mod-z': redo,
      'Mod-y': redo,
      Enter: (state, dispatch) => (opts.onEnter?.() ? true : insertBreak(state, dispatch)),
      'Alt-Enter': insertBreak,
      'Mod-Enter': insertBreak,
      'Ctrl-Enter': insertBreak,
      'Shift-Enter': insertBreak,
      Escape: () => opts.onEscape?.() ?? false,
    }),
    keymap(baseKeymap),
  ]
}

export function createRichState(runs: TextRun[], opts: RichKeyOptions = {}): EditorState {
  return EditorState.create({ doc: runsToDoc(runs), plugins: richPlugins(opts) })
}
