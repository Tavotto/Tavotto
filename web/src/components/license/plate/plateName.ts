// 铭牌上刻名字的量法：名字最长 24 个字（`PLATE_NAME_MAX`），要完整落在牌面上、不被裁、不溢出。
// 贴图（SVG，1020 宽）与 CSS 回退版用同一把尺：按字形估宽，超出可用宽度就等比缩小字号，到下限再收字距，
// 最后一道保险是 SVG 的 `textLength`（贴图）/ 容器内不换行不裁剪（CSS）。纯函数，不碰 DOM 与 canvas，测试里直接断言。

/** 名字的可用宽度（贴图坐标）：左右各留 76，与 TAVOTTO 字标对齐 */
export const NAME_AVAIL = 1020 - 76 * 2
export const NAME_SIZE = 64
export const NAME_SIZE_MIN = 28
export const NAME_LS = 2.5

const WIDE = /[ᄀ-ᅟ⺀-鿿가-힯豈-﫿＀-｠￠-￦]/
const BROAD = new Set('WMwm@%')

/** 一个字的估宽（单位：em）。偏保守：宁可估宽一点，名字就缩得多一点 */
export function charEm(ch: string): number {
  if (WIDE.test(ch)) return 1
  if (BROAD.has(ch)) return 0.95
  if (/[A-Z]/.test(ch)) return 0.78
  return 0.64
}

/** 整段名字的估宽（em，不含字距） */
export function nameEm(name: string): number {
  let sum = 0
  for (const ch of name) sum += charEm(ch)
  return sum
}

export interface NameFit {
  size: number
  ls: number
  /** 估宽（贴图坐标） */
  width: number
  /** 到了下限、字距也收完还是放不下：贴图上再加 `textLength` 兜底 */
  clamp: boolean
}

export function nameFit(name: string): NameFit {
  const n = [...name].length
  const em = nameEm(name)
  if (!n) return { size: NAME_SIZE, ls: NAME_LS, width: 0, clamp: false }
  const fitSize = (ls: number) => (NAME_AVAIL - n * ls) / em
  let size = Math.min(NAME_SIZE, fitSize(NAME_LS))
  let ls = NAME_LS
  if (size < NAME_SIZE_MIN) {
    size = NAME_SIZE_MIN
    ls = Math.max(0, Math.min(NAME_LS, (NAME_AVAIL - em * size) / n))
  }
  size = Math.floor(size * 10) / 10
  const width = em * size + n * ls
  return { size, ls, width, clamp: width > NAME_AVAIL + 0.5 }
}

/** CSS 回退版的字号：以牌宽（cqw）计。牌面文字区宽 84.8cqw；基准 6.27cqw（官网同值），放不下就等比缩小 */
export function nameCqw(name: string): number {
  const n = [...name].length
  if (!n) return 6.27
  const em = nameEm(name) + n * 0.04 // 字距 0.04em
  return Math.min(6.27, Math.floor((84.8 / em) * 100) / 100)
}
