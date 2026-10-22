/**
 * R / MATLAB 脚本的后缀粗判（ADR 0143 修订「R / MATLAB 来源」）。
 *
 * 只看最后一段的后缀：`.R` / `.r` → R，`.m` → MATLAB。**这不是结论**——`.m` 也是 Objective-C 的后缀，最终由后端
 * `sourcelang.detect` 看内容复核（`POST /api/ai/convert` 对不上回 `convert_source_language_mismatch`）。
 * 与后端 `sourcelang.extension_hint` 严格同源（`tests/test_sourcelang.py` ↔ `foreignScript.test.ts` 各比同一组用例）。
 */

export type ForeignLanguage = 'r' | 'matlab'

const baseName = (path: string) => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? path

/** 文件名 / 路径 → 来源语言；不是 R / MATLAB 后缀（或以分隔符结尾 = 目录）就 null */
export function foreignLanguageOf(path: string): ForeignLanguage | null {
  if (/[\\/]$/.test(path)) return null
  const name = baseName(path)
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return null // `.R` 这样的隐藏文件没有扩展名
  const ext = name.slice(dot)
  if (ext === '.R' || ext === '.r') return 'r'
  if (ext === '.m') return 'matlab'
  return null
}
