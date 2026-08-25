/**
 * Code Sheet 用的轻量 Python 高亮：纯函数、零依赖、构建期打进 bundle。
 *
 * 刻意不引入 Monaco / shiki / highlight.js——只读展示二十几行案例代码，
 * 一个大型运行时高亮器的体积抵得上整个 playground 前端（首屏纪律见
 * PLAYGROUND_V2.md）。词法只求「案例代码读起来舒服」，不求覆盖整门语言：
 * 注释 / 字符串（含 f-string 前缀）/ 数字 / 关键字 / 其余原样。
 */

export type TokenKind = 'comment' | 'string' | 'number' | 'keyword' | 'plain'

export interface PyToken {
  kind: TokenKind
  text: string
}

const KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break',
  'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'finally',
  'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal',
  'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
])

//: 一行里按优先级扫：注释吃到行尾 > 字符串 > 数字 > 标识符（查关键字表）
const TOKEN_RE =
  /(#.*$)|([rbfu]{0,2}(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'))|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\b[A-Za-z_]\w*\b)/gm

/** 把一行源码切成 token 流；案例代码不含跨行字符串，按行切是安全的。 */
export function tokenizePythonLine(line: string): PyToken[] {
  const out: PyToken[] = []
  let last = 0
  TOKEN_RE.lastIndex = 0
  for (let m = TOKEN_RE.exec(line); m; m = TOKEN_RE.exec(line)) {
    if (m.index > last) out.push({ kind: 'plain', text: line.slice(last, m.index) })
    const [, comment, str, num, word] = m
    if (comment != null) out.push({ kind: 'comment', text: comment })
    else if (str != null) out.push({ kind: 'string', text: str })
    else if (num != null) out.push({ kind: 'number', text: num })
    else out.push({ kind: KEYWORDS.has(word!) ? 'keyword' : 'plain', text: word! })
    last = m.index + m[0].length
  }
  if (last < line.length) out.push({ kind: 'plain', text: line.slice(last) })
  return out
}

/** 整段源码 → 每行一组 token（渲染方配行号用）。 */
export function tokenizePython(source: string): PyToken[][] {
  return source.replace(/\n$/, '').split('\n').map(tokenizePythonLine)
}
