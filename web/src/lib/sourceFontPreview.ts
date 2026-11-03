import { previewFamilyOf, sourceFontState } from './sourceFontCache'
import type { SourceFont } from '@/types/document'

/**
 * 预览用的 CSS 字体栈：`[正文脸, CJK 脸, 批准族栈]`——与导出（`plan._user_faces`）同一个顺序与同一个判据。
 * 两张脸**各自独立**判：正文脸不可用不影响 CJK 脸（导出里 `user_primary` / `user_cjk` 也是各自回退）；
 * 某张脸状态不是 `ok` / 字节没到 = 不进栈，那一层按批准族画（正文 = `fontFamily`，CJK = 批准的 Noto），与导出回退一致。
 * 副作用：把还没问过的脸排进状态请求（必须两张都问，不能只问正文脸）。
 */
export function previewStackOf(source: SourceFont | undefined, approvedStack: string): string {
  if (!source) return approvedStack
  const names: string[] = []
  if (sourceFontState(source, source) === 'ok') {
    const p = previewFamilyOf(source)
    if (p) names.push(p)
  }
  if (source.cjk && sourceFontState(source.cjk) === 'ok') {
    const c = previewFamilyOf(source.cjk)
    if (c && !names.includes(c)) names.push(c)
  }
  return names.length ? `${names.map((n) => `"${n}"`).join(', ')}, ${approvedStack}` : approvedStack
}
