/**
 * 导出文件名的规则 —— **与 `src/tavotto/engine/exportreq.py` 严格同源**。
 *
 * 为什么两份：名字的合法性要在**输入的那一刻**给出就地提示（§六），
 * 等一次网络往返再说"这个名字不行"太晚了；而真正落盘的是后端，它不能相信
 * 客户端替它做过校验。两份实现由 `tests/golden/filename_vectors.json` 对齐，
 * pytest 与 vitest 各跑一遍同一份向量——与 `preflight` ↔ `preflight.ts`
 * 完全一样的纪律。
 *
 * **判据按最严的平台写（Windows），不按当前浏览器所在的平台写**：项目会被
 * 拷到另一台电脑上，一个在 macOS 上导出成功的 `Fig?1.pdf` 到了 Windows 上
 * 根本创建不出来。
 */

/** 文件名基名长度上限（Windows MAX_PATH 扣掉目录、扩展名与去重后缀的余量） */
export const FILENAME_MAX = 120

/** Windows 上非法的字符 + 两个平台的路径分隔符 */
const ILLEGAL_CHARS = '<>:"/\\|?*'

const RESERVED_NAMES = new Set([
  'CON',
  'PRN',
  'AUX',
  'NUL',
  ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
  ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
])

/**
 * 首尾空白的判定集合。**写死一份，不用 `String.trim()`**：JS 的 `trim()` 与
 * Python 的 `str.strip()` 认的字符集**不一样**（`\ufeff` 只有 JS 认，
 * `\x1c`–`\x1f` 只有 Python 认）。靠各自的内建函数，两侧对 `"\ufeffFig"`
 * 会给出不同的答案，而那正是 golden 向量存在的意义。
 */
const EDGE_WHITESPACE = new Set([
  ...' \t\n\r\v\f\u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff',
  ...Array.from({ length: 11 }, (_, i) => String.fromCharCode(0x2000 + i)),
])

/** 可以被识别并剥掉的扩展名。**只有我们自己会产出的那几个** */
const STRIPPABLE_EXTS = ['pdf', 'png', 'svg', 'tif', 'tiff', 'jpg', 'jpeg', 'eps']

/**
 * 文件名不合法的原因。**闭集**——界面按它取一句本地化的话，
 * 不接受自由文本（共享规则 §12：存 key 与枚举，不存翻译后的字符串）。
 */
export type FilenameReason =
  | 'whitespace_edge'
  | 'empty'
  | 'too_long'
  | 'control_char'
  | 'illegal_char'
  | 'trailing_dot'
  | 'dot_only'
  | 'reserved_name'

/**
 * 基名合不合法。合法回 `null`，否则回原因码。
 *
 * **判据的顺序是判据的一部分**：同一个名字可能同时犯两条，两侧必须报同一条，
 * 否则 golden 向量会当场红。
 */
export function checkFilename(name: string): FilenameReason | null {
  if (!name) return 'empty'
  if (EDGE_WHITESPACE.has(name[0]) || EDGE_WHITESPACE.has(name[name.length - 1])) {
    return 'whitespace_edge'
  }
  if (name.length > FILENAME_MAX) return 'too_long'
  for (const ch of name) {
    const code = ch.codePointAt(0) ?? 0
    if (code < 32 || code === 127) return 'control_char'
  }
  for (const ch of name) if (ILLEGAL_CHARS.includes(ch)) return 'illegal_char'
  if ([...name].every((c) => c === '.')) return 'dot_only'
  if (name.endsWith('.')) return 'trailing_dot'
  const stem = name.split('.', 1)[0].toUpperCase()
  if (RESERVED_NAMES.has(stem)) return 'reserved_name'
  return null
}

/**
 * 把用户顺手打上的输出扩展名剥掉，避免 `Fig 1.pdf.pdf`。
 * 一次只剥一层：剥到底的话 `data.tar.gz.png` 会变成 `data.tar`。
 */
export function stripOutputExtension(name: string, formats: readonly string[] = ['pdf', 'png']) {
  const lowered = name.toLowerCase()
  const known = new Set([...STRIPPABLE_EXTS, ...formats.map((f) => f.toLowerCase())])
  for (const ext of known) {
    const suffix = `.${ext}`
    if (lowered.endsWith(suffix) && name.length > suffix.length) {
      return name.slice(0, -suffix.length)
    }
  }
  return name
}

/** 基名 + 格式 → 文件名。**补扩展名只有这一处** */
export function outputName(base: string, fmt: string): string {
  return `${base}.${fmt}`
}

/** 这次导出会写出哪几个文件名（不含样式检查报告） */
export function outputNames(base: string, formats: readonly string[]): string[] {
  return formats.map((f) => outputName(base, f))
}

/**
 * `rename` 策略下的去重名：`Fig 1 (2).pdf`、`Fig 1 (3).pdf`…
 *
 * **真正的去重发生在后端**（只有它看得见磁盘）；这一份的存在是为了让
 * golden 向量把两侧的编号规则钉在一起——两边各编各的号，用户会看到
 * 界面预览的名字与磁盘上的名字对不上。
 */
export function dedupeCheck(base: string, fmt: string, taken: (name: string) => boolean): string {
  const first = outputName(base, fmt)
  if (!taken(first)) return first
  for (let n = 2; n <= 9999; n++) {
    const candidate = outputName(`${base} (${n})`, fmt)
    if (!taken(candidate)) return candidate
  }
  throw new Error('name_exhausted')
}
