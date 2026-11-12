import type { CanvasObject, TextObject } from '@/types/document'
import { MM_PER_PT } from '@/lib/units'
import { WRITING_MODE_VERTICAL } from '@/lib/typography'
import { useViewportStore, worldToMm } from '@/store/viewportStore'

/**
 * 单行文字的「贴字宽」框（ADR 0163）：开始旋转前把框收到字宽，旋转才绕字的中心转。
 *
 * 量法是**预览自己的排版**——直接在画布上那个文字 DOM（`data-text-body`，与导出对齐的那份
 * 预览）里量：临时让它 `width: max-content; white-space: pre`，读 border-box 宽（已含内边距与描边），
 * 同一帧还原，不留痕迹、不触发绘制。没有第二套测宽。
 *
 * 只收「不会换行」的文字：正文含换行，或自然宽度超过当前框宽（= 现在就在折行）都返回 null，
 * 保持原框宽。收框时按 `align` 保持字的落位：左对齐锚住左边、居中锚住中线、右对齐锚住右边，
 * 所以字在页面上一动不动，只是多余空白没了。
 */
/**
 * 收框余量：定额 0.5 em 兜标点 / 单字落到别的脸的差；比例 4 % 兜整行字宽的系统性偏窄（长文字上定额不够）。
 * 写成**整数百分数**是因为后端看护用例要用 `tests/support/tsconst.exported_number` 结构性地读它们（同源对，
 * `docs/rules/repo/same-origin-pairs.md`），那个读法只认十进制整数字面量。
 */
export const FIT_MARGIN_EM_PERCENT = 50
export const FIT_MARGIN_RATIO_PERCENT = 4
export const FIT_MARGIN_EM = FIT_MARGIN_EM_PERCENT / 100
export const FIT_MARGIN_RATIO = FIT_MARGIN_RATIO_PERCENT / 100

/**
 * 贴字宽的框宽 = 字宽 × (1 + 4 %) + 0.5 em（余量的理由见上）。**收框公式只有这一处**：旋转前收框（`fitSingleLineBox`）
 * 与「图内文字复制为标注」（`lib/figureTextCopy`，字宽取自 matplotlib 排出来的块）都走它。
 */
export const fitWidthMm = (naturalMm: number, sizePt: number): number =>
  naturalMm * (1 + FIT_MARGIN_RATIO) + FIT_MARGIN_EM * sizePt * MM_PER_PT

/** 收框的结果：横排收的是 x / w（行长是宽），竖排收的是 y / h（行长是高）——只含被改的那一对 */
export type FitBox = { x: number; w: number } | { y: number; h: number }

/**
 * 单行文字的贴字框。横排量宽、收 x / w；竖排（ADR 0163）量**高**（行长是高）、收 y / h，
 * 同一个公式与余量（预览量长 × (1 + 4 %) + 0.5 em），按 `align` 锚点：左 / 中 / 右 = 上 / 中 / 下。
 */
export function fitSingleLineBox(o: TextObject): FitBox | null {
  if (o.text.includes('\n')) return null
  const el = document.querySelector<HTMLElement>(
    `[data-object-id="${CSS.escape(o.id)}"] [data-text-body]`,
  )
  if (!el) return null
  const vertical = o.writingMode === WRITING_MODE_VERTICAL
  // 取小数长度（getBoundingClientRect 回屏幕 px，除缩放换回世界 px）：offsetWidth 是整数，向下取整会让收后的框
  // 比字窄一丁点而折行
  const zoom = useViewportStore.getState().zoom || 1
  const length = () => (vertical ? el.getBoundingClientRect().height : el.getBoundingClientRect().width) / zoom
  const cur = length()
  const prop = vertical ? 'height' : 'width'
  const prev = { size: el.style[prop], whiteSpace: el.style.whiteSpace }
  el.style[prop] = 'max-content'
  el.style.whiteSpace = 'pre'
  const natural = length()
  el.style[prop] = prev.size
  el.style.whiteSpace = prev.whiteSpace
  if (!(natural > 0) || natural > cur + 0.5) return null
  // 余量 0.5 em：预览按浏览器 CSS 字体栈排，导出按 RenderCore 的 Liberation / Noto 排——Times New Roman / Arial
  // 与 Liberation 度量兼容，但机器上落到别的脸（Linux 无 TNR、中文落到 PingFang / Songti、标点宽度不同）时
  // 窄一点就会让导出折成两行。宁可框略宽，绝不让导出折行（看护：tests/test_compose_annotations.py 的收框用例）
  const len = fitWidthMm(worldToMm(natural), o.sizePt)
  const box = vertical ? o.h : o.w
  if (!(len > 0) || len >= box - 0.01) return null
  const at = (origin: number) =>
    o.align === 'left' ? origin : o.align === 'right' ? origin + box - len : origin + (box - len) / 2
  return vertical ? { y: at(o.y), h: len } : { x: at(o.x), w: len }
}

/** 旋转前要不要收框：只在文字、此刻没转（0 → 非 0 那一次）时才算 */
export function fitBeforeRotate(o: CanvasObject): FitBox | null {
  if (o.type !== 'text' || (o.rotationDeg ?? 0) !== 0) return null
  return fitSingleLineBox(o)
}
