/**
 * 图内文字（matplotlib 文本元素：轴标签、刻度标签、图例项、标题）的编解码：
 * 一个普通 matplotlib 字符串（可含 mathtext `$…$`）⇄ 所见即所得编辑框用的 `TextRun[]`
 * （ADR 0152）。标注文字的另一套写法见 `richText.ts`，两边共用同一个 `TextRun`。
 *
 * **核心不变量：编辑器绝不改变一个没动过的字符串怎么渲染。** 所以解析只认下面这套
 * 「规范段」，其余任何 `$…$` 都整段当一个公式芯片（`math: true`，`text` 是去掉两端 `$`
 * 的源码，写回时原样 `$<text>$`）：`$10^{-5}$`、`$\alpha$`、`$\mathrm{min^{-1}}$` 都是芯片。
 *
 * 规范段（序列化器只写这些，一段一个 run，不合并）：
 *
 * ```text
 * $\mathit{B}$  $\mathbf{B}$  $\mathbfit{B}$     斜体 / 粗体 / 粗斜体
 * $^{X}$  $_{X}$                                上标 / 下标；X 是以下之一：
 *     \mathit{B} \mathbf{B} \mathbfit{B}          带样式
 *     \mathrm{B}                                  直立，且 B 含字母
 *     P                                           直立，且 P 不含任何字母（-1、2、+）
 * ```
 *
 * 正文 B / P 只由「字面字符」与转义组成：空格写 `\ `（mathtext 会吞掉普通空格），
 * `\{ \} \$ \% \_` 表示对应的字面字符。写法必须唯一，否则 parse→serialize 不能逐字回写，
 * 所以 `$\mathit{a b}$`、`$\mathit{50%}$`、`$^{n}$`（裸字母在 mathtext 里是斜体）、
 * `$^{\mathrm{12}}$`（该写成裸的）全都不是规范段，一律芯片。
 *
 * **`$` 的规则（matplotlib 3.11 实测，`Text._preprocess_math` / `cbook.is_math_text`）**
 *   * 按「行」判定——`Text._get_layout` 先 `split("\n")`，每行单独判；`$x\ny$` 渲染成
 *     字面的 `$x` / `y$`，数学段不能跨行；
 *   * 一行里「未转义的 `$`」（前一个字符不是 `\` 的 `$`）个数 > 0 且为偶数 = 数学行，
 *     整行交给 mathtext 解析：行内 `\$` 渲染成字面 `$`，空格原样保留，`\`、`^`、`_`、
 *     `{`、`%` 等在 `$…$` 之外都是字面字符；
 *   * 否则（0 个或奇数个）整行是纯文本，只做 `\$` → `$` 的替换，其余（含 `\\`）原样。
 *     所以 `price \$5` 显示 `price $5`，`a $b` 显示 `a $b`，两者写法不同、显示相同；
 *   * `\\$x$` 里的 `$` 仍算「被转义」（只看前一个字符），行变成奇数 `$`，按纯文本显示 `\$x$`；
 *   * 数学行里 `$` 紧跟在普通文字的 `\` 后会被吃成 `\$`——序列化器遇到这种情况把那个 `\`
 *     写成 `$\backslash$`（读回是芯片，渲染一样）。
 *
 *   为了让 `price \$5` 与 `a $b` 都逐字回写：纯文本行里「未转义的 `$`」解析成独立的
 *   `{ text: '$', script: '', raw: true }`（`raw` 的含义同 `richText.ts`：写回时原样不转义），
 *   转义的 `\$` 解析成普通文字里的 `$`。序列化器仅在该行没有任何规范段、且结果仍是纯文本行时
 *   才把 raw 的 `$` 原样写出，否则一律写 `\$`，不会把行的类型翻过去。
 *
 * **mathtext 里写不出来的字符（实测）**：正文里 `# & " ' \ ^ ` ~` 要么报错、要么被吞
 * （`^ _ ~` 无声消失，`'` 变成角分号），`\#`、`\&` 也报错；非 ASCII 只有拉丁补充/扩展、
 * 希腊字母与 `– — −` 能原样显示，中日韩、emoji、不换行空格等会变成占位方块。另外 `-`
 * 在 mathtext 里会渲染成减号 `−`（字宽与字形略有差别）。**限制**：run 里含这些写不出的
 * 字符时，该 run 整个退成无样式的纯文字（丢掉斜体/粗体/上下标），宁可丢样式也不改字。
 *
 * 换行：数学段不能跨行，带样式的 run 里的 `\n` 把它拆成若干段，换行写成纯文字。
 */
import type { TextRun } from './richText'

/** 正文里能直接写的字符（实测在 mathit/mathbf/mathbfit/mathrm 里都原样显示）。 */
const LITERAL_CHAR =
  /^[A-Za-z0-9!()*+,\-./:;<=>?@[\]|¡-¬®-ɏΑ-ΡΣ-ω–—−]$/

/** 需要反斜杠转义才能显示的字符（`\#`、`\&` 在 mathtext 里报错，不在此列）。 */
const ESCAPABLE = '{}$%_'

type Style = 'mathit' | 'mathbf' | 'mathbfit'

const hasLetter = (s: string): boolean => /\p{L}/u.test(s)

/** matplotlib `cbook.is_math_text`：未转义 `$` 个数 > 0 且为偶数。按单行用。 */
function isMathLine(line: string): boolean {
  let n = 0
  for (let i = 0; i < line.length; i++) {
    if (line[i] === '$' && line[i - 1] !== '\\') n++
  }
  return n > 0 && n % 2 === 0
}

const isPlainRun = (r: TextRun): boolean =>
  !r.italic && !r.bold && !r.math && !r.raw && r.script === ''

/** 往 runs 末尾追加普通文字；上一个也是普通文字就并进去。 */
function pushPlain(runs: TextRun[], text: string): void {
  if (!text) return
  const last = runs[runs.length - 1]
  if (last && isPlainRun(last)) last.text += text
  else runs.push({ text, script: '' })
}

/* -------------------- 解析 -------------------- */

/** 规范正文 → 字面文字；不规范（或空）返回 null。 */
function decodeBody(body: string): string | null {
  let out = ''
  for (let i = 0; i < body.length; i++) {
    const ch = body[i]
    if (ch === '\\') {
      const next = body[i + 1]
      if (next === ' ') out += ' '
      else if (next !== undefined && ESCAPABLE.includes(next)) out += next
      else return null
      i++
    } else if (LITERAL_CHAR.test(ch)) out += ch
    else return null
  }
  return out || null
}

const TOP_STYLED = /^\\(mathit|mathbfit|mathbf)\{([\s\S]*)\}$/
const SCRIPTED = /^([\^_])\{([\s\S]*)\}$/
const SCRIPT_STYLED = /^\\(mathit|mathbfit|mathbf|mathrm)\{([\s\S]*)\}$/

function styleFlags(style: string): Pick<TextRun, 'italic' | 'bold'> {
  const f: Pick<TextRun, 'italic' | 'bold'> = {}
  if (style === 'mathit' || style === 'mathbfit') f.italic = true
  if (style === 'mathbf' || style === 'mathbfit') f.bold = true
  return f
}

/** `$…$` 的内部源码 → 规范段对应的 run；不是规范段返回 null。 */
function parseCanonical(inner: string): TextRun | null {
  const top = TOP_STYLED.exec(inner)
  if (top) {
    const text = decodeBody(top[2])
    return text === null ? null : { text, script: '', ...styleFlags(top[1]) }
  }
  const sc = SCRIPTED.exec(inner)
  if (!sc) return null
  const script = sc[1] === '^' ? 'sup' : 'sub'
  const x = sc[2]
  const styled = SCRIPT_STYLED.exec(x)
  if (styled) {
    const text = decodeBody(styled[2])
    if (text === null) return null
    if (styled[1] === 'mathrm') {
      // 直立只在「含字母」时才写成 \mathrm；不含字母的规范写法是裸的
      return hasLetter(text) ? { text, script } : null
    }
    return { text, script, ...styleFlags(styled[1]) }
  }
  const text = decodeBody(x)
  return text !== null && !hasLetter(text) ? { text, script } : null
}

/** 从 `from` 起找闭合的 `$`（跳过 `\$`）；找不到回 -1。 */
function findClose(line: string, from: number): number {
  for (let j = from; j < line.length; j++) {
    if (line[j] === '\\' && line[j + 1] === '$') j++
    else if (line[j] === '$') return j
  }
  return -1
}

function parseLine(line: string): TextRun[] {
  const runs: TextRun[] = []
  const math = isMathLine(line)
  let buf = ''
  const flush = () => {
    pushPlain(runs, buf)
    buf = ''
  }
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (ch === '\\' && line[i + 1] === '$') {
      buf += '$'
      i++
    } else if (ch === '$') {
      if (!math) {
        // 纯文本行里未转义的 `$`：raw，写回时原样（见文件头）
        flush()
        runs.push({ text: '$', script: '', raw: true })
        continue
      }
      const close = findClose(line, i + 1)
      if (close < 0) {
        // mathtext 自己也会报错的残缺串：剩下的按字面保留，不吞字
        buf += line.slice(i)
        break
      }
      flush()
      const inner = line.slice(i + 1, close)
      runs.push(parseCanonical(inner) ?? { text: inner, script: '', math: true })
      i = close
    } else buf += ch
  }
  flush()
  return runs
}

/**
 * matplotlib 字符串 → 片段列表。规范段变成带样式/上下标的 run，其它 `$…$` 变成公式芯片，
 * 纯文字（含换行）并成普通 run；空串返回 `[]`。
 */
export function parseMathtext(src: string): TextRun[] {
  const runs: TextRun[] = []
  src.split('\n').forEach((line, k) => {
    if (k > 0) pushPlain(runs, '\n')
    for (const r of parseLine(line)) {
      if (isPlainRun(r)) pushPlain(runs, r.text)
      else runs.push(r)
    }
  })
  return runs
}

/* -------------------- 序列化 -------------------- */

/** 字面文字 → 规范正文；含写不出的字符返回 null。 */
function encodeBody(text: string): string | null {
  let out = ''
  for (const ch of text) {
    if (ch === ' ') out += '\\ '
    else if (ESCAPABLE.includes(ch)) out += '\\' + ch
    else if (LITERAL_CHAR.test(ch)) out += ch
    else return null
  }
  return out
}

/** 带样式 / 上下标的 run（不含换行）→ 规范段；写不出返回 null。 */
function encodeSegment(r: TextRun, text: string): string | null {
  const body = encodeBody(text)
  if (body === null) return null
  const style: Style | null =
    r.bold && r.italic ? 'mathbfit' : r.bold ? 'mathbf' : r.italic ? 'mathit' : null
  if (r.script === '') return style ? `$\\${style}{${body}}$` : null
  const op = r.script === 'sup' ? '^' : '_'
  const x = style ? `\\${style}{${body}}` : hasLetter(text) ? `\\mathrm{${body}}` : body
  return `$${op}{${x}}$`
}

/** 芯片源码能否安全放进 `$…$`：不能含未转义的 `$`，也不能以会吃掉收尾 `$` 的 `\` 结尾。 */
const chipFits = (src: string): boolean => findClose(src + '$', 0) === src.length

type Piece =
  | { kind: 'plain'; text: string }
  | { kind: 'raw'; text: string }
  | { kind: 'seg'; seg: string }

function toPiece(r: TextRun, text: string): Piece {
  if (r.math) {
    return chipFits(text) ? { kind: 'seg', seg: `$${text}$` } : { kind: 'plain', text }
  }
  if (r.raw) return { kind: 'raw', text }
  if (r.script === '' && !r.italic && !r.bold) return { kind: 'plain', text }
  const seg = encodeSegment(r, text)
  return seg === null ? { kind: 'plain', text } : { kind: 'seg', seg }
}

function emitLine(pieces: Piece[], verbatimRaw: boolean): string {
  let out = ''
  for (const p of pieces) {
    // 只转义 `$`、不转义 `\`：matplotlib 在公式段外只认 `\$` 这一种转义，单独的 `\` 原样显示
    // （实测见文件头）。把 `\` 写成 `\\` 反而多显示一个反斜杠。`a\$`（字面反斜杠 + 美元）写成 `a\\$`，
    // matplotlib 把末尾的 `\$` 读成 `$`、前面的 `\` 原样——往返用例钉着。
    if (p.kind === 'plain') out += p.text.replace(/\$/g, '\\$')
    else if (p.kind === 'raw') {
      for (const ch of p.text) {
        if (ch !== '$') out += ch
        else out += verbatimRaw && !out.endsWith('\\') ? '$' : '\\$'
      }
    } else {
      // 段前面的普通文字以 `\` 结尾会让开头的 `$` 变成 `\$`
      if (out.endsWith('\\')) out = out.slice(0, -1) + '$\\backslash$'
      out += p.seg
    }
  }
  return out
}

/**
 * 片段列表 → matplotlib 字符串。每个带样式 / 上下标的 run 写成一段规范段（不合并），
 * 公式芯片写回 `$<text>$`，其余是普通文字（`$` 写成 `\$`）。run 里含 mathtext 写不出
 * 的字符（`\ ^ ~ # & " ' `` ` `` 等，详见文件头）时，该 run 退成无样式纯文字——这是有意的限制。
 */
export function serializeMathtext(runs: TextRun[]): string {
  const lines: Piece[][] = [[]]
  for (const r of runs) {
    r.text.split('\n').forEach((part, k) => {
      if (k > 0) lines.push([])
      // 空片段不写；唯一例外是 text 本身就是空串的公式芯片（`$$`）
      if (part === '' && !(r.math && r.text === '')) return
      lines[lines.length - 1].push(toPiece(r, part))
    })
  }
  return lines
    .map((pieces) => {
      const hasSeg = pieces.some((p) => p.kind === 'seg')
      const line = emitLine(pieces, !hasSeg)
      // 原样写 raw 的 `$` 把纯文本行翻成了数学行（偶数个）：全部改写成 `\$`
      return !hasSeg && isMathLine(line) ? emitLine(pieces, false) : line
    })
    .join('\n')
}
