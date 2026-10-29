/**
 * 标注文字的两种写法（旧标记 / LaTeX 子集），以及 Unicode 科学文本的解释。
 *
 * 为什么是标记而不是富文本模型：
 *   * 文档 schema 不用动（`TextObject.text` 仍是一个字符串），旧文档零影响；
 *   * 同一套语法在图内元素那边直接就是 matplotlib mathtext（`cm$^{-1}$`），
 *     用户学一次；
 *   * 只有 `^{`/`_{` 才触发，正文里孤零零的 `^` 或 `_` 原样显示——存量文字
 *     不会因为升级突然变形。
 * 需要字面量的 `^`/`_`/`\` 时写 `\^`、`\_`、`\\`。
 *
 * **第二种写法 `latex`**（ADR 0152，所见即所得编辑框）：对象带 `textFormat === 'latex'`
 * 时，同一个字符串按 LaTeX 子集读——`\textit{}` / `\emph{}` / `\textbf{}` /
 * `\textsuperscript{}` / `\textsubscript{}` / `$…$` 公式。没有这个字段的对象一律按上面
 * 的旧标记读，逐字不变。见本文件「LaTeX 子集」一节。
 *
 * **几何常量与 pdfbackend/../richtext.py 严格同源**（同名注释），改一边必须
 * 同步另一边，否则画布上对齐、导出后错位。
 */

/** 上下标字号 = 正文的这个比例 */
export const SCRIPT_SIZE = 0.62
/** 上标基线抬高 = 正文字号 × 这个比例 */
export const SUP_RISE = 0.42
/** 下标基线下沉 = 正文字号 × 这个比例 */
export const SUB_DROP = 0.18

export type ScriptKind = '' | 'sup' | 'sub'

/** 标注文字的写法。缺省 = 旧标记；闭集，与 `richtext.TEXT_FORMATS` 同源。 */
export const TEXT_FORMATS = ['latex'] as const
export type TextFormat = (typeof TEXT_FORMATS)[number]

export interface TextRun {
  text: string
  script: ScriptKind
  italic?: boolean
  bold?: boolean
  /** true = 这一段是公式源码（`$…$` 里面那串），`text` 原样是用户写的 mathtext */
  math?: boolean
  /** true = 读不懂的命令原文（`\textsc{abc}`）：照字面显示，写回时原样、不转义 */
  raw?: boolean
}

const OPENER: Record<string, ScriptKind> = { '^': 'sup', _: 'sub' }

/**
 * 标记文本 → 片段列表。解析失败的片段（没有配对的 `}`）按字面量原样保留，
 * 绝不吞掉用户的字符。
 */
export function parseRuns(text: string, format?: TextFormat): TextRun[] {
  if (format === 'latex') return parseLatex(text)
  const runs: TextRun[] = []
  let buf = ''
  const flush = (script: ScriptKind = '') => {
    if (buf) runs.push({ text: buf, script })
    buf = ''
  }

  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (ch === '\\' && i + 1 < text.length && '^_\\'.includes(text[i + 1])) {
      buf += text[i + 1] // 转义：\^ \_ \\ → 字面量
      i++
      continue
    }
    const kind = OPENER[ch]
    if (kind && text[i + 1] === '{') {
      const close = matchBrace(text, i + 1)
      if (close > 0) {
        flush()
        const inner = text.slice(i + 2, close)
        if (inner) runs.push({ text: inner, script: kind })
        i = close
        continue
      }
    }
    buf += ch
  }
  flush()
  return runs
}

/** 从 `{` 起找配对的 `}`（支持嵌套）；找不到回 -1。 */
function matchBrace(text: string, open: number): number {
  let depth = 0
  for (let i = open; i < text.length; i++) {
    if (text[i] === '\\') {
      i++
      continue
    }
    if (text[i] === '{') depth++
    else if (text[i] === '}' && --depth === 0) return i
  }
  return -1
}

/* -------------------- LaTeX 子集（`textFormat === 'latex'`，ADR 0152） -------------------- */
/**
 * **与 `src/tavotto/richtext.py` 的同名一节严格同源**（同一张命令表、同一套宽进规则）。
 *
 * 宽进：用户从论文里粘贴来的文字尽量读对；读不懂的命令连同它的花括号原样当文字，绝不吞字。
 * 严出：`serializeRuns(…, 'latex')` 写回的永远是规范形。
 *
 * ```text
 * \textit{…} \emph{…}            斜体
 * \textbf{…}                     粗体
 * \textup{…} \textmd{…}          取消斜体 / 取消粗体；\textnormal{…} 两者都取消
 * \textsuperscript{…}            上标；\textsubscript{…} 下标
 * $…$  $$…$$  \(…\)  \[…\]       公式（源码原样进 text，math=true）
 * {…}                            分组，花括号本身不显示
 * \$ \% \& \# \_ \{ \} \^{} \~{} 字面量；\textbackslash{} 是反斜杠
 * ~                              不换行空格；\\ 换行；\␣ 空格
 * 其余                           原样是字（裸 ^ _ % & # 也是字——宽进）
 * ```
 */
type LatexState = { script: ScriptKind; italic: boolean; bold: boolean }

const STYLE_CMDS: Record<string, Partial<LatexState>> = {
  textit: { italic: true },
  emph: { italic: true },
  textup: { italic: false },
  textbf: { bold: true },
  textmd: { bold: false },
  textnormal: { italic: false, bold: false },
  textsuperscript: { script: 'sup' },
  textsubscript: { script: 'sub' },
}
const LITERAL_ESCAPES = '$%&#_{}'
const ACCENT_LITERALS = '^~'
const NBSP = ' '
const isAsciiLetter = (c: string | undefined) => !!c && /^[A-Za-z]$/.test(c)

function parseLatex(text: string): TextRun[] {
  const runs: TextRun[] = []
  latexInto(text, { script: '', italic: false, bold: false }, runs)
  return runs
}

function makeRun(text: string, st: LatexState, math: boolean, raw: boolean): TextRun {
  const r: TextRun = { text, script: st.script }
  if (st.italic) r.italic = true
  if (st.bold) r.bold = true
  if (math) r.math = true
  if (raw) r.raw = true
  return r
}

function emitLatex(runs: TextRun[], text: string, st: LatexState, math = false, raw = false) {
  if (!text) return
  const last = runs[runs.length - 1]
  if (
    last && !math && !last.math && !!last.raw === raw &&
    last.script === st.script && !!last.italic === st.italic && !!last.bold === st.bold
  ) last.text += text
  else runs.push(makeRun(text, st, math, raw))
}

function findUnescaped(text: string, needle: string, start: number): number {
  for (let i = start; i < text.length; i++) {
    if (text[i] === '\\') {
      if (text.startsWith(needle, i)) return i // needle 本身以反斜杠开头（`\)` / `\]`）
      i++
      continue
    }
    if (text.startsWith(needle, i)) return i
  }
  return -1
}

function latexInto(text: string, st: LatexState, runs: TextRun[]): void {
  const n = text.length
  let i = 0
  while (i < n) {
    const ch = text[i]
    if (ch === '\\' && i + 1 < n) {
      const nxt = text[i + 1]
      if (isAsciiLetter(nxt)) {
        let j = i + 1
        while (j < n && isAsciiLetter(text[j])) j++
        const name = text.slice(i + 1, j)
        const close = text[j] === '{' ? matchBrace(text, j) : -1
        const cmd = Object.prototype.hasOwnProperty.call(STYLE_CMDS, name) ? STYLE_CMDS[name] : undefined
        if (cmd && close > 0) {
          latexInto(text.slice(j + 1, close), { ...st, ...cmd }, runs)
          i = close + 1
          continue
        }
        if (name === 'textbackslash') {
          emitLatex(runs, '\\', st)
          if (text.startsWith('{}', j)) i = j + 2
          else {
            while (j < n && text[j] === ' ') j++ // 控制词后面的空格是分隔符（LaTeX 规则）
            i = j
          }
          continue
        }
        // 不认识的命令：连同紧跟的那组花括号原样是字
        const end = close > 0 ? close + 1 : j
        emitLatex(runs, text.slice(i, end), st, false, true)
        i = end
        continue
      }
      if (LITERAL_ESCAPES.includes(nxt)) {
        emitLatex(runs, nxt, st)
        i += 2
        continue
      }
      if (ACCENT_LITERALS.includes(nxt)) {
        emitLatex(runs, nxt, st)
        i += text.startsWith('{}', i + 2) ? 4 : 2
        continue
      }
      if (nxt === '\\') {
        emitLatex(runs, '\n', st)
        i += 2
        continue
      }
      if (nxt === ' ') {
        emitLatex(runs, ' ', st)
        i += 2
        continue
      }
      if (nxt === '(' || nxt === '[') {
        const end = findUnescaped(text, nxt === '(' ? '\\)' : '\\]', i + 2)
        if (end >= 0) {
          emitLatex(runs, text.slice(i + 2, end), st, true)
          i = end + 2
          continue
        }
      }
      emitLatex(runs, ch, st)
      i++
      continue
    }
    if (ch === '$') {
      const delim = text.startsWith('$$', i) ? '$$' : '$'
      const end = findUnescaped(text, delim, i + delim.length)
      if (end >= 0) {
        emitLatex(runs, text.slice(i + delim.length, end), st, true)
        i = end + delim.length
        continue
      }
      emitLatex(runs, ch, st)
      i++
      continue
    }
    if (ch === '{') {
      const close = matchBrace(text, i)
      if (close > 0) {
        latexInto(text.slice(i + 1, close), st, runs)
        i = close + 1
        continue
      }
    }
    emitLatex(runs, ch === '~' ? NBSP : ch, st)
    i++
  }
}

const ESCAPE_OUT: Record<string, string> = {
  '\\': '\\textbackslash{}',
  $: '\\$',
  '%': '\\%',
  '&': '\\&',
  '#': '\\#',
  _: '\\_',
  '{': '\\{',
  '}': '\\}',
  '^': '\\^{}',
  '~': '\\~{}',
  [NBSP]: '~',
}
const LEVELS = ['bold', 'italic', 'script'] as const

/**
 * 片段 → LaTeX 子集的**规范形**（与 `richtext.serialize_latex` 逐字同源）。
 * 相邻片段按「粗体 → 斜体 → 上下标」逐层分组，每组只包一次命令；公式段写回 `$源码$`。
 */
function serializeLatex(runs: TextRun[], level = 0): string {
  if (level === LEVELS.length) {
    return runs
      .map((r) =>
        r.math ? `$${r.text}$` : r.raw ? r.text : [...r.text].map((c) => ESCAPE_OUT[c] ?? c).join(''),
      )
      .join('')
  }
  const field = LEVELS[level]
  const keyOf = (r: TextRun) => (field === 'script' ? r.script : !!r[field])
  let out = ''
  for (let i = 0; i < runs.length; ) {
    const value = keyOf(runs[i])
    let j = i
    while (j < runs.length && keyOf(runs[j]) === value) j++
    let inner = serializeLatex(runs.slice(i, j), level + 1)
    if (field === 'bold' && value) inner = `\\textbf{${inner}}`
    else if (field === 'italic' && value) inner = `\\textit{${inner}}`
    else if (field === 'script' && value) inner = `\\text${value === 'sup' ? 'superscript' : 'subscript'}{${inner}}`
    out += inner
    i = j
  }
  return out
}

/**
 * 一段文字 → 逐行的片段。旧标记照旧「先按 `\n` 切、再逐行解析」（升级前的行为，逐字不变）；
 * LaTeX 写法先整段解析，再按片段里的换行切开——`\textit{a\nb}` 两行都是斜体。
 * 与 `typography._logical_lines` 同一个约定。
 */
export function runLines(text: string, format?: TextFormat): TextRun[][] {
  if (format !== 'latex') return text.split('\n').map((line) => parseRuns(line))
  const lines: TextRun[][] = [[]]
  for (const run of parseRuns(text, format)) {
    if (run.math) {
      lines[lines.length - 1].push(run)
      continue
    }
    run.text.split('\n').forEach((part, k) => {
      if (k) lines.push([])
      if (part) lines[lines.length - 1].push({ ...run, text: part })
    })
  }
  return lines
}

/** 旧标记 → LaTeX 子集（编辑框第一次保存旧标注时用；画出来逐字形相同）。 */
export const legacyToLatex = (text: string): string => serializeLatex(parseRuns(text))

/* ------------ 受控科学文本解释：Unicode 上下标字符 → 渲染用片段 ------------- */
/**
 * **与 `src/tavotto/richtext.py` 的同名一节严格同源**（同一张表、同一条判据）。
 *
 * ### 合成是有代价的，所以默认只在「不然就是方框」时才合成
 *
 * 把 `⁵` 画成「62% 的 5 抬高 42%」以后，**PDF 文本层里那个字符就是 `5`**
 * ——实测导出后抽回来的文本从 `×10⁵` 变成 `×105`。审稿人复制走的是 105，
 * 这是语义损坏，比「上标是另一张脸画的」严重。所以两档，各自诚实：
 *
 * ```text
 * auto（默认）  只有这一串里有字符谁都画不出（否则就是方框）时才合成
 * scientific    认得的 Unicode 上下标一律合成：字体统一，代价是文本层降级
 * ```
 *
 * 两档都还要求折出来的基础字符正文脸全画得出，否则白折一场。
 * 「整串一起折」是刻意的：`m⁻²` 里两个字符处境不同，逐字符处理会得到一个
 * 小的合成减号紧挨着一个大的设计上标，比原样还难看。
 */

/** 上标字符 → 基础字符。**闭集**，与 `richtext.SUPERSCRIPT_BASE` 同源。 */
export const SUPERSCRIPT_BASE: Readonly<Record<string, string>> = {
  '⁰': '0',
  '¹': '1',
  '²': '2',
  '³': '3',
  '⁴': '4',
  '⁵': '5',
  '⁶': '6',
  '⁷': '7',
  '⁸': '8',
  '⁹': '9',
  '⁺': '+',
  '⁻': '-',
  '⁼': '=',
  '⁽': '(',
  '⁾': ')',
  'ⁿ': 'n',
  'ⁱ': 'i',
}

/** 下标字符 → 基础字符。**闭集**，与 `richtext.SUBSCRIPT_BASE` 同源。 */
export const SUBSCRIPT_BASE: Readonly<Record<string, string>> = {
  '₀': '0',
  '₁': '1',
  '₂': '2',
  '₃': '3',
  '₄': '4',
  '₅': '5',
  '₆': '6',
  '₇': '7',
  '₈': '8',
  '₉': '9',
  '₊': '+',
  '₋': '-',
  '₌': '=',
  '₍': '(',
  '₎': ')',
  'ₐ': 'a',
  'ₑ': 'e',
  'ₒ': 'o',
  'ₓ': 'x',
  'ₕ': 'h',
  'ₖ': 'k',
  'ₗ': 'l',
  'ₘ': 'm',
  'ₙ': 'n',
  'ₚ': 'p',
  'ₛ': 's',
  'ₜ': 't',
}

/**
 * 解释档位。**没有 `math` 这一档**：画布文字不经 matplotlib，摆一个不存在
 * 的模式等于一句做不到的承诺（见 `typography.ts` 的 `mathTextModeOf`）。
 */
export const TEXT_INTERPRETATIONS = ['auto', 'scientific'] as const
export type TextInterpretation = (typeof TEXT_INTERPRETATIONS)[number]
export const DEFAULT_INTERPRETATION: TextInterpretation = 'auto'

const scriptOf = (ch: string): ScriptKind =>
  ch in SUPERSCRIPT_BASE ? 'sup' : ch in SUBSCRIPT_BASE ? 'sub' : ''

/**
 * 这段文字里有没有认得的 Unicode 上下标字符。界面靠它决定要不要露出
 * 「解释方式」那一行——没有这类字符时那个选择对用户没有任何意义。
 */
export const hasScientificChars = (text: string): boolean =>
  [...text].some((ch) => scriptOf(ch) !== '')

export interface InterpretOptions {
  /** 正文那张脸自己画得出这个码位吗 */
  isPrimary: (cp: number) => boolean
  /** 任何一层画得出吗（false = 导出上是个方框） */
  isDrawable: (cp: number) => boolean
  mode?: TextInterpretation
}

/** 标记片段 → **渲染用**片段。判据缺席时一条都不折——不猜一张默认覆盖表。 */
export function interpretRuns(runs: TextRun[], opts?: InterpretOptions): TextRun[] {
  if (!opts) return [...runs]
  const { isPrimary, isDrawable } = opts
  const scientific = (opts.mode ?? DEFAULT_INTERPRETATION) === 'scientific'
  const out: TextRun[] = []
  const emit = (text: string, script: ScriptKind, like: TextRun) => {
    if (!text) return
    const last = out[out.length - 1]
    if (last && !last.math && last.script === script && !!last.italic === !!like.italic && !!last.bold === !!like.bold) {
      last.text += text
    } else {
      const r: TextRun = { text, script }
      if (like.italic) r.italic = true
      if (like.bold) r.bold = true
      out.push(r)
    }
  }
  for (const run of runs) {
    if (run.math) {
      out.push({ ...run }) // 公式源码不是字，不折
      continue
    }
    if (run.script) {
      emit(run.text, run.script, run)
      continue
    }
    const chars = [...run.text]
    let i = 0
    while (i < chars.length) {
      const script = scriptOf(chars[i])
      if (!script) {
        emit(chars[i], '', run)
        i++
        continue
      }
      let j = i
      while (j < chars.length && scriptOf(chars[j]) === script) j++
      const chunk = chars.slice(i, j)
      const table = script === 'sup' ? SUPERSCRIPT_BASE : SUBSCRIPT_BASE
      const base = chunk.map((c) => table[c]).join('')
      const cp = (c: string) => c.codePointAt(0) as number
      const worth = chunk.some((c) => (scientific ? !isPrimary(cp(c)) : !isDrawable(cp(c))))
      const possible = [...base].every((c) => isPrimary(cp(c)))
      if (worth && possible) emit(base, script, run)
      else emit(chunk.join(''), '', run)
      i = j
    }
  }
  return out
}

/** 片段列表 → 标记文本（大小写转换等改完内容后写回用）。`format` 与读它时的写法一致。 */
export function serializeRuns(runs: TextRun[], format?: TextFormat): string {
  if (format === 'latex') return serializeLatex(runs)
  return runs
    .map((r, i) => {
      const body = r.script ? r.text : escapeLiteral(r.text, i < runs.length - 1)
      return r.script === 'sup' ? `^{${body}}` : r.script === 'sub' ? `_{${body}}` : body
    })
    .join('')
}

/**
 * 只在**真会被误读成标记**时才转义，绝不无脑加反斜杠。
 *
 * 无脑转义的后果：`a^b`、`100%` 这类正文里的孤立符号，用户点一次「大小写」
 * 就会凭空多出 `\`——文本被工具改成了自己没写过的样子。
 *   * `^`/`_` 只有紧跟 `{` 时才是标记开头，其余原样；
 *   * `\` 只有当它后面是 `\ ^ _`、或位于片段末尾**且后面还有片段**（拼接后
 *     紧跟的就是 `^{`/`_{`，`\^` 会被读成转义）时才需要成对写出。
 *
 * `followedByRun` 这个参数不能省：正则里的 `$` 匹配的是**这一段自己**的末尾，
 * 而整段文本的最后一段后面什么都没有，那个孤立的 `\` 不可能被误读。省掉它
 * 的后果是用户每点一次「大小写」，以反斜杠结尾的文本（粘一段 Windows 路径
 * 就够了）末尾就多一个 `\`——`serializeRuns(parseRuns(s)) === s` 这条往返
 * 不变式当场破掉，而且是每点一次多一个。
 */
const escapeLiteral = (s: string, followedByRun: boolean) =>
  s
    .replace(followedByRun ? /\\(?=[\\^_]|$)/g : /\\(?=[\\^_])/g, '\\\\')
    .replace(/([\^_])(?=\{)/g, '\\$1')

/** 去掉全部标记，只留可读文本（搜索、导出元数据、无障碍标签用）。 */
export const plainText = (text: string, format?: TextFormat) =>
  parseRuns(text, format).map((r) => r.text).join('')

/** 文本里是否真的用到了上下标——没用到就不必走富文本渲染路径。 */
export const hasScripts = (text: string, format?: TextFormat) =>
  parseRuns(text, format).some((r) => r.script !== '')

/**
 * 给选区套上/去掉上下标标记，返回新文本与新的选区位置。
 *
 * 已经整段是该类型时再点一次就是取消（与加粗/斜体一致的切换语义）；
 * 没有选区时插入一对空标记并把光标放进去，可以直接开始打字。
 */
export function toggleScript(
  text: string,
  start: number,
  end: number,
  kind: 'sup' | 'sub',
): { text: string; start: number; end: number } {
  const open = kind === 'sup' ? '^{' : '_{'
  const before = text.slice(0, start)
  const sel = text.slice(start, end)
  const after = text.slice(end)

  // 取消：选区正好被一对同类标记包着
  if (before.endsWith(open) && after.startsWith('}')) {
    const next = before.slice(0, -open.length) + sel + after.slice(1)
    return { text: next, start: start - open.length, end: end - open.length }
  }
  // 取消：选区自身就是 `^{…}`
  if (sel.startsWith(open) && sel.endsWith('}')) {
    const inner = sel.slice(open.length, -1)
    return { text: before + inner + after, start, end: start + inner.length }
  }
  const next = `${before}${open}${sel}}${after}`
  return { text: next, start: start + open.length, end: end + open.length }
}

/**
 * 图内元素（matplotlib）的上下标：走 mathtext（`cm$^{-1}$`），不是我们的标记。
 *
 * 引擎那边把 override 的字符串原样交给 matplotlib，`$…$` 里的内容由它自己
 * 排版——所以这里只负责把选区包成 mathtext 片段，不需要引擎改任何东西。
 */
export function toggleMathScript(
  text: string,
  start: number,
  end: number,
  kind: 'sup' | 'sub',
): { text: string; start: number; end: number } {
  const open = kind === 'sup' ? '$^{' : '$_{'
  const before = text.slice(0, start)
  const sel = text.slice(start, end)
  const after = text.slice(end)

  if (before.endsWith(open) && after.startsWith('}$')) {
    const next = before.slice(0, -open.length) + sel + after.slice(2)
    return { text: next, start: start - open.length, end: end - open.length }
  }
  if (sel.startsWith(open) && sel.endsWith('}$')) {
    const inner = sel.slice(open.length, -2)
    return { text: before + inner + after, start, end: start + inner.length }
  }
  const next = `${before}${open}${sel}}$${after}`
  return { text: next, start: start + open.length, end: end + open.length }
}

export type CaseMode = 'upper' | 'lower' | 'title' | 'sentence'

/**
 * 大小写转换。只动片段内容，标记本身不受影响——不然 `^{-1}` 里的花括号
 * 会被首字母大写之类的规则算成一个「词」。CJK 无大小写，天然不受影响。
 *
 * `protectMath` 用于图内元素文字：`$…$` 之间是 matplotlib mathtext，
 * 里面的 `\alpha`、`\mathrm` 是命令，改大小写会直接把公式弄坏。
 */
export function transformCase(
  text: string,
  mode: CaseMode,
  protectMath = false,
  format?: TextFormat,
): string {
  if (protectMath) {
    // 以 $…$ 为界切开，奇数段（公式内部）原样保留
    return text
      .split(/(\$[^$]*\$)/g)
      .map((part) => (part.startsWith('$') && part.endsWith('$') && part.length > 1
        ? part
        : transformCase(part, mode)))
      .join('')
  }
  // 公式段是源码（`\alpha` 改了大小写就废），原样保留
  return serializeRuns(
    parseRuns(text, format).map((r) => (r.math ? r : { ...r, text: applyCase(r.text, mode) })),
    format,
  )
}

function applyCase(s: string, mode: CaseMode): string {
  switch (mode) {
    case 'upper':
      return s.toUpperCase()
    case 'lower':
      return s.toLowerCase()
    case 'title':
      return s.replace(/\p{L}[\p{L}\p{M}'’]*/gu, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase())
    case 'sentence':
      // 每句首字母大写；其余小写。句末标点后跟空白才算换句。
      return s
        .toLowerCase()
        .replace(/(^|[.!?。！？]\s*)(\p{L})/gu, (_m, lead: string, c: string) => lead + c.toUpperCase())
  }
}
