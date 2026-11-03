/**
 * 中文字体格的短名：属性页字体一行里中文格只有 64px，列表里仍是完整名，收起时只显示短名。
 *
 * 去掉末尾的「简 / 繁」标记（`-简`、`-繁`、`-简体`、`-繁體`）、区域后缀（` SC` / ` TC` / ` HK` / ` JP`…）与字重词
 * （` Regular` / ` Medium`…），可叠着出现（`Noto Sans SC Regular` → `Noto Sans`）。
 * 去完什么都不剩就原样返回；中间的词不动（`Noto Sans` 的 `Sans` 不是后缀）。
 */
const SUFFIXES = [
  /[-\s]?(?:简体|繁體|繁体|简|繁)$/u,
  /[-\s](?:SC|TC|HK|TW|CN|JP|KR)$/iu,
  /[-\s](?:Regular|Normal|Medium|Light|Bold|Thin|Heavy|Black)$/iu,
]

export function cjkFontShortName(name: string): string {
  let out = name.trim()
  for (let changed = true; changed; ) {
    changed = false
    for (const re of SUFFIXES) {
      const next = out.replace(re, '').trim()
      if (next && next !== out) {
        out = next
        changed = true
      }
    }
  }
  return out || name
}
