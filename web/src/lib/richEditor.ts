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
  /** 粘贴进来的纯文字里认得出公式 / 样式命令时给片段；认不出回 null，交给默认粘贴 */
  pasteRuns?(text: string): TextRun[] | null
  /** LaTeX 模式里的源码有没有结构错误（切回所见即所得之前查）；没错回 null */
  validate?(src: string): SourceError | null
}

/** 源码的结构错误：哪一类、在第几个字符（从 1 数，给人看的） */
export interface SourceError {
  code: 'brace' | 'dollar'
  at: number
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
      pasteRuns: (text) => pastedRuns(text, 'annotation'),
      validate: (src) => validateSource(src, 'annotation'),
    }
    ANNOTATION_CODECS.set(format, c)
  }
  return c
}

/** 图内文字（matplotlib 的文字元素）的写法：mathtext，见 `mathtextCodec.ts` */
export const figureTextCodec: RichCodec = {
  parse: parseMathtext,
  serialize: serializeMathtext,
  pasteRuns: (text) => pastedRuns(text, 'mathtext'),
  validate: (src) => validateSource(src, 'mathtext'),
}

/* -------------------- 粘贴识别与源码校验（ADR 0152 PR 3） -------------------- */

/** 一段公式定界符里的内容像不像公式：有命令 / 上下标 / 花括号，或就是一个字母（`$x$`） */
const looksLikeMath = (body: string) => /[\\^_{}]/.test(body) || /^\s*[A-Za-z]\s*$/.test(body)

/** 标注里认得的文字样式命令（与 richText.ts 的 LaTeX 子集同一张表的子集）——有它才按 LaTeX 读整段 */
const TEXT_STYLE_CMD = /\\(?:textit|emph|textbf|textup|textmd|textnormal|textsuperscript|textsubscript)\{/

/**
 * 粘贴进来的纯文字 → 片段。认的是论文里常见的写法：`$…$`、`$$…$$`、`\(…\)`、`\[…\]`
 * 里**像公式**的那几段变成公式块；「$5 and $10」这种成对的 `$` 里没有公式迹象，原样是字。
 * 标注那一侧还认 `\textit{}` 这类文字样式命令。什么都认不出就回 null（交给默认粘贴，一字不改）。
 *
 * 图内文字里的规范段（`$\mathit{T}$`）先变成公式块；写回后编辑框按 mathtext 读回同步，
 * 它就成了斜体字（RichTextField 的读回同步），不必在这里重复一遍规范段的判断。
 */
export function pastedRuns(text: string, dialect: 'annotation' | 'mathtext'): TextRun[] | null {
  // 标注里带文字样式命令的，是整段 LaTeX 源码：整段按 LaTeX 子集读，命令里套着的公式
  // （`\textit{T $x$}`）不被切开（Codex #1492）。不像公式的成对 `$`（钱数）先转义成字
  if (dialect === 'annotation' && TEXT_STYLE_CMD.test(text)) {
    return parseRuns(escapeNonMathDollars(text), 'latex')
  }
  const runs: TextRun[] = []
  let found = false
  const plain = (seg: string) => {
    if (!seg) return
    if (dialect === 'annotation' && TEXT_STYLE_CMD.test(seg)) {
      found = true
      runs.push(...parseRuns(seg, 'latex'))
    } else runs.push({ text: seg, script: '' })
  }
  const re = /\$\$([\s\S]+?)\$\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]|(?<!\\)\$([^$\n]+?)(?<!\\)\$/g
  let last = 0
  for (const m of text.matchAll(re)) {
    const body = m[1] ?? m[2] ?? m[3] ?? m[4] ?? ''
    if (!looksLikeMath(body)) continue
    plain(text.slice(last, m.index))
    runs.push({ text: body.trim(), script: '', math: true })
    found = true
    last = (m.index ?? 0) + m[0].length
  }
  plain(text.slice(last))
  if (!found) return null
  // 相邻的纯文字片段合并（粘贴边界上切开的那几段）
  const out: TextRun[] = []
  for (const r of runs) {
    const prev = out[out.length - 1]
    const isPlain = (x: TextRun) => !x.math && !x.raw && !x.italic && !x.bold && x.script === ''
    if (prev && isPlain(prev) && isPlain(r)) prev.text += r.text
    else out.push({ ...r })
  }
  return out
}

/** `$…$` 里不像公式的那几对 `$` 写成 `\$`（「$5 and $10」留成字），其余原样 */
function escapeNonMathDollars(text: string): string {
  return text.replace(/(?<!\\)\$([^$\n]+?)(?<!\\)\$/g, (m, body: string) =>
    looksLikeMath(body) ? m : `\\$${body}\\$`,
  )
}

/**
 * LaTeX 模式里的源码有没有结构错误。只查两类、都是「切回所见即所得会读歪」的：
 * 未转义的花括号不配对、未转义的 `$` 不成对（图内文字按行查：matplotlib 的公式段不跨行）。
 * 命令拼错不算——宽进的读法会把它原样当字，所见即所得里也看得见。
 */
export function validateSource(src: string, dialect: 'annotation' | 'mathtext'): SourceError | null {
  const lines = dialect === 'mathtext' ? src.split('\n') : [src]
  let offset = 0
  for (const line of lines) {
    const opens: number[] = []
    let dollar = -1
    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (ch === '\\') {
        i++ // 转义：后面那个字符是字面量
        continue
      }
      if (ch === '{') opens.push(i)
      else if (ch === '}') {
        if (!opens.length) return { code: 'brace', at: offset + i + 1 }
        opens.pop()
      } else if (ch === '$') {
        if (line[i + 1] === '$') {
          i++ // `$$` 当一对定界符的一半
        }
        dollar = dollar < 0 ? i : -1
      }
    }
    if (opens.length) return { code: 'brace', at: offset + opens[opens.length - 1] + 1 }
    if (dollar >= 0) return { code: 'dollar', at: offset + dollar + 1 }
    offset += line.length + 1
  }
  return null
}

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
      // 自己复制出去的是 `span[data-rt]`，粘回来要认得（Codex #1489）
      parseDOM: [{ tag: 'sup' }, { tag: 'span[data-rt="sup"]' }],
      toDOM: () => ['span', { 'data-rt': 'sup', style: scriptStyle(SUP_RISE) }, 0],
    },
    sub: {
      excludes: 'sup sub',
      parseDOM: [{ tag: 'sub' }, { tag: 'span[data-rt="sub"]' }],
      toDOM: () => ['span', { 'data-rt': 'sub', style: scriptStyle(-SUB_DROP) }, 0],
    },
    /** 读不懂的命令原文（`\textsc{abc}`）：照字面显示、写回原样；挨着它打的字不继承 */
    raw: {
      inclusive: false,
      parseDOM: [{ tag: 'span[data-rt="raw"]' }],
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
