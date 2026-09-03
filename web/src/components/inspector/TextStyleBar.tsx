import { useTranslation } from 'react-i18next'
import type { ManifestElement } from '@/lib/api'
import { propertyPathOf } from '@/lib/typography'
import type { PanelObject } from '@/types/document'
import { TypographyControls } from './controls/TypographyControls'
import { FIGURE_TEXT_SINGLE_PROPS, useFigureTypography } from './typographyAdapter'

/**
 * 图内文字的高频样式：**带可见标签的行**（字体 / 字号 / 颜色 / 对齐），
 * 不再是一排无标签控件 + 多层弹层（审计 P2 / 嵌套弹层）。
 *
 * 行距 / 旋转 / 垂直对齐 / 背景 / 描边 / 层级不再压进齿轮弹层——它们经
 * 展示注册表落进「更多」，与所有别的元素同一套折叠模型。
 *
 * **控件本体在 `controls/TypographyControls`，与批量文字样式、画布标注共用同一份。**
 * 这里只是「一个元素」这个特例的适配器组装：单选与多选的 B/I 是同一个
 * 三态图标按钮，不会因为多选就退化成 `常规 / 加粗` 的文字下拉。
 *
 * 属性页与右键快捷编辑共用这一份；控件严格按 manifest 里真有的字段出。
 * 画布标注文字（TextSection）用同一组行组件——两种「文字」一个操作语言。
 */

/**
 * 工具条覆盖掉的属性——平铺列表与分组要把它们让出来，避免出现两套控件。
 *
 * **从规范表现算**（`lib/typography.propertyPathOf`），不是手抄一遍：手抄的
 * 那份会在加一条排版属性时忘记更新，症状是同一个属性出现两套控件。
 */
export const TEXT_BAR_PROPS = new Set(
  FIGURE_TEXT_SINGLE_PROPS.map((p) => propertyPathOf('figureText', p)).filter(
    (v): v is string => !!v,
  ),
)

/**
 * 该不该给这个元素画文字样式行。判据是「它是不是一个 matplotlib Text」：
 * 三条都有才算——图例只有 fontsize、刻度标签只有 text，都不该套进来。
 */
export const hasTextStyleBar = (el: ManifestElement) =>
  ['fontsize', 'color', 'weight'].every((p) => el.editable.some((f) => f.prop === p))

export function TextStyleBar({
  panel,
  element,
  className,
  labelWidth = 72,
}: {
  panel: PanelObject
  element: ManifestElement
  className?: string
  /** 标签列宽：属性页 72（与 FieldRow 对齐），快捷编辑弹层可传 44 */
  labelWidth?: number
}) {
  useTranslation('inspector')
  const adapter = useFigureTypography(panel, singleton(element), FIGURE_TEXT_SINGLE_PROPS)
  return <TypographyControls adapter={adapter} className={className} labelWidth={labelWidth} />
}

/**
 * 单元素数组的稳定包装。直接写 `[element]` 会让 adapter 的 useMemo 每次
 * 渲染都失效——不致命，但白算一遍字段交集。
 */
const cache = new WeakMap<ManifestElement, ManifestElement[]>()
function singleton(el: ManifestElement): ManifestElement[] {
  const hit = cache.get(el)
  if (hit) return hit
  const arr = [el]
  cache.set(el, arr)
  return arr
}
