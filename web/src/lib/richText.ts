/**
 * 标注文字的行内标记：上标 `^{…}`、下标 `_{…}`。
 *
 * 为什么是标记而不是富文本模型：
 *   * 文档 schema 不用动（`TextObject.text` 仍是一个字符串），旧文档零影响；
 *   * 同一套语法在图内元素那边直接就是 matplotlib mathtext（`cm$^{-1}$`），
 *     用户学一次；
 *   * 只有 `^{`/`_{` 才触发，正文里孤零零的 `^` 或 `_` 原样显示——存量文字
 *     不会因为升级突然变形。
 * 需要字面量的 `^`/`_`/`\` 时写 `\^`、`\_`、`\\`。
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

export interface TextRun {
  text: string
  script: ScriptKind
}

const OPENER: Record<string, ScriptKind> = { '^': 'sup', _: 'sub' }

/**
 * 标记文本 → 片段列表。解析失败的片段（没有配对的 `}`）按字面量原样保留，
 * 绝不吞掉用户的字符。
 */
export function parseRuns(text: string): TextRun[] {
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

/** 片段列表 → 标记文本（大小写转换等改完内容后写回用）。 */
export function serializeRuns(runs: TextRun[]): string {
  return runs
    .map((r) => {
      const body = r.script ? r.text : escapeLiteral(r.text)
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
 *   * `\` 只有当它后面是 `\ ^ _`、或位于片段末尾（后面可能立刻接一个标记）
 *     时才需要成对写出。
 */
const escapeLiteral = (s: string) =>
  s.replace(/\\(?=[\\^_]|$)/g, '\\\\').replace(/([\^_])(?=\{)/g, '\\$1')

/** 去掉全部标记，只留可读文本（搜索、导出元数据、无障碍标签用）。 */
export const plainText = (text: string) => parseRuns(text).map((r) => r.text).join('')

/** 文本里是否真的用到了上下标——没用到就不必走富文本渲染路径。 */
export const hasScripts = (text: string) => parseRuns(text).some((r) => r.script !== '')

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
export function transformCase(text: string, mode: CaseMode, protectMath = false): string {
  if (protectMath) {
    // 以 $…$ 为界切开，奇数段（公式内部）原样保留
    return text
      .split(/(\$[^$]*\$)/g)
      .map((part) => (part.startsWith('$') && part.endsWith('$') && part.length > 1
        ? part
        : transformCase(part, mode)))
      .join('')
  }
  return serializeRuns(
    parseRuns(text).map((r) => ({ ...r, text: applyCase(r.text, mode) })),
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
