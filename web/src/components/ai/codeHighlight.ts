import { tokenizePythonLine, type PyToken } from '@/playground/pythonHighlight'

/**
 * 助手代码块的语法高亮（2026-10-07 设计审计 §6.3）：**复用** playground 的 `pythonHighlight`
 * （零依赖、纯函数），在它的五类之上再细分三类——那份词法只认注释 / 字符串 / 数字 / 关键字，
 * 改图助手回答里最常见的是 `ax.set_yscale(...)` 这种调用，不分出「函数」整块代码几乎是一个颜色。
 *
 * 细分只看 `plain` 里的标识符，不改原词法（playground 的 Code Sheet 照旧用它）：
 *   - 后面紧跟 `(` → function（调用 / 定义）
 *   - 内置函数 / 常用内置名 → builtin
 *   - 首字母大写的标识符 → type（类名：`LogFormatterMathtext`、`Path`）
 *
 * 颜色是 `--color-syntax-*` 这一族 token（index.css；宪法第十八节「2026-10-07 重做」），不在这里写色值。
 */
export type SyntaxKind = 'comment' | 'string' | 'number' | 'keyword' | 'function' | 'builtin' | 'type' | 'plain'

export interface SyntaxToken {
  kind: SyntaxKind
  text: string
}

const BUILTINS = new Set([
  'abs', 'all', 'any', 'bool', 'dict', 'enumerate', 'filter', 'float', 'format', 'getattr', 'hasattr',
  'int', 'isinstance', 'len', 'list', 'map', 'max', 'min', 'open', 'print', 'range', 'reversed',
  'round', 'set', 'setattr', 'sorted', 'str', 'sum', 'super', 'tuple', 'type', 'zip', 'self',
])

const IDENT = /^[A-Za-z_]\w*$/

/** 一行 Python → 细分后的 token 流（拼回去与原行逐字相同） */
export function highlightPythonLine(line: string): SyntaxToken[] {
  const raw: PyToken[] = tokenizePythonLine(line)
  return raw.map((tok, i): SyntaxToken => {
    if (tok.kind !== 'plain' || !IDENT.test(tok.text)) return tok
    const next = raw[i + 1]
    if (BUILTINS.has(tok.text)) return { kind: 'builtin', text: tok.text }
    if (next && next.kind === 'plain' && next.text.startsWith('(')) return { kind: 'function', text: tok.text }
    if (/^[A-Z]/.test(tok.text) && /[a-z]/.test(tok.text)) return { kind: 'type', text: tok.text }
    return tok
  })
}

/** 哪些围栏语言按 Python 高亮：显式的 python 一族，以及**没写语言**的块（改图助手改的就是 Python 脚本） */
export function isPythonish(lang: string): boolean {
  return lang === '' || lang === 'python' || lang === 'py' || lang === 'python3' || lang === 'pycon'
}

/** token 种类 → 颜色类。plain 不上色（继承代码块的 ink） */
export const SYNTAX_CLASS: Record<SyntaxKind, string> = {
  comment: 'text-syntax-comment italic',
  string: 'text-syntax-string',
  number: 'text-syntax-number',
  keyword: 'text-syntax-keyword',
  function: 'text-syntax-function',
  builtin: 'text-syntax-builtin',
  type: 'text-syntax-type',
  plain: '',
}
