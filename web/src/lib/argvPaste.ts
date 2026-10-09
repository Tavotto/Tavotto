/**
 * 「粘贴一条命令」→ token 列表（T07）。只支持**一条简单的 POSIX sh 调用**，别的一律拒绝、提示改用逐项填写。
 *
 * 支持：空白分词、单引号（原样）、双引号（只认 `\"` 与 `\\` 两个转义，其余反斜杠原样）、引号外的反斜杠转义、
 * `""` 空串、`a"b c"d` 拼接。拒绝（不猜 token 边界、不当 shell 执行）：管道 / 重定向 / 命令替换 / 变量展开 /
 * 多条命令 / 通配符 / `~` / 注释 / 多行 / 前置环境变量赋值 / 没闭合的引号。真值是 Python 的 `shlex.split`：
 * 共用 `tests/golden/argv_paste_vectors.json`，被接受的那些两侧逐项相同。
 *
 * 去掉开头的启动器：`python` / `python3` / `python3.12` / `py`（可带路径与 .exe）后面跟本脚本；或者直接以本脚本
 * 开头。启动器后面跟着解释器选项（`-u`、`-m`……）或别的脚本，拒绝——那不是这个脚本的参数。
 */

export type PasteError =
  | 'empty'
  | 'unsupported_syntax'
  | 'unbalanced_quote'
  | 'multiline'
  | 'env_assignment'
  | 'interpreter_options'
  | 'different_script'
  | 'unrecognized_launcher'

export type PasteResult =
  | { ok: true; words: string[]; argv: string[] }
  | { ok: false; error: PasteError }

/** 引号外出现就拒绝的字符（sh 会把它们解释成别的东西）。 */
const UNQUOTED_META = new Set(['|', '&', ';', '<', '>', '(', ')', '$', '`', '*', '?', '[', ']', '{', '}', '!'])
/** 只在一个词开头才有特殊含义的字符。 */
const WORD_START_META = new Set(['#', '~'])
const SPACE = new Set([' ', '\t'])
const LAUNCHER = /^(python(\d+(\.\d+)?)?|py)(\.exe)?$/i
const ENV_ASSIGNMENT = /^\s*[A-Za-z_][A-Za-z0-9_]*=/

const basename = (path: string) => path.split(/[\\/]/).pop() ?? path

export const splitWords = (text: string): { ok: true; words: string[] } | { ok: false; error: PasteError } => {
  const trimmed = text.replace(/[\r\n]+$/, '')
  if (trimmed.trim() === '') return { ok: false, error: 'empty' }
  if (ENV_ASSIGNMENT.test(trimmed)) return { ok: false, error: 'env_assignment' }
  const words: string[] = []
  let cur = ''
  let inWord = false
  let i = 0
  while (i < trimmed.length) {
    const c = trimmed[i]
    if (c === '\n' || c === '\r') return { ok: false, error: 'multiline' }
    if (SPACE.has(c)) {
      if (inWord) {
        words.push(cur)
        cur = ''
        inWord = false
      }
      i += 1
      continue
    }
    if (!inWord && WORD_START_META.has(c)) return { ok: false, error: 'unsupported_syntax' }
    if (UNQUOTED_META.has(c)) return { ok: false, error: 'unsupported_syntax' }
    if (c === "'") {
      const end = trimmed.indexOf("'", i + 1)
      if (end < 0) return { ok: false, error: 'unbalanced_quote' }
      cur += trimmed.slice(i + 1, end)
      inWord = true
      i = end + 1
      continue
    }
    if (c === '"') {
      let j = i + 1
      let closed = false
      while (j < trimmed.length) {
        const d = trimmed[j]
        if (d === '"') {
          closed = true
          break
        }
        if (d === '$' || d === '`') return { ok: false, error: 'unsupported_syntax' }
        if (d === '\\') {
          const nxt = trimmed[j + 1]
          if (nxt === '"' || nxt === '\\') {
            cur += nxt
            j += 2
            continue
          }
          if (nxt === '$' || nxt === '`' || nxt === '\n' || nxt === undefined) {
            return { ok: false, error: 'unsupported_syntax' }
          }
          cur += d
          j += 1
          continue
        }
        cur += d
        j += 1
      }
      if (!closed) return { ok: false, error: 'unbalanced_quote' }
      inWord = true
      i = j + 1
      continue
    }
    if (c === '\\') {
      const nxt = trimmed[i + 1]
      if (nxt === undefined) return { ok: false, error: 'unbalanced_quote' }
      if (nxt === '\n' || nxt === '\r') return { ok: false, error: 'multiline' }
      cur += nxt
      inWord = true
      i += 2
      continue
    }
    cur += c
    inWord = true
    i += 1
  }
  if (inWord) words.push(cur)
  if (words.length === 0) return { ok: false, error: 'empty' }
  return { ok: true, words }
}

/** 一条粘贴的命令 → 本脚本的 argv（`script` 是项目相对路径；只比较文件名）。 */
export const parsePastedCommand = (text: string, script: string): PasteResult => {
  const split = splitWords(text)
  if (!split.ok) return split
  const { words } = split
  const name = basename(script)
  let rest = words
  if (LAUNCHER.test(basename(words[0]))) {
    const next = words[1]
    if (next === undefined) return { ok: false, error: 'different_script' }
    if (next.startsWith('-')) return { ok: false, error: 'interpreter_options' }
    if (basename(next) !== name) return { ok: false, error: 'different_script' }
    rest = words.slice(2)
  } else if (basename(words[0]) === name) {
    rest = words.slice(1)
  } else if (/\.pyw?$/i.test(basename(words[0])) && !words[0].startsWith('-')) {
    return { ok: false, error: 'different_script' }
  } else if (words.slice(1).some((w) => basename(w) === name)) {
    // `uv run x.py …` / `conda run …`：启动器不认识，不猜它的参数边界
    return { ok: false, error: 'unrecognized_launcher' }
  }
  return { ok: true, words, argv: rest }
}
