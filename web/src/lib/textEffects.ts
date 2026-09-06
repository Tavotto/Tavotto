/**
 * 图内文字的两种「效果」：背景框（`bbox_*`）与描边（`stroke_*`）。
 *
 * 每种效果是**一个开关 + 一组从属字段**。开关关着的时候从属字段写了也不生效
 * ——摆一排此刻写了不生效的控件，比藏起来更不诚实（与刻度的 `major_step`
 * 只在 step 模式下出现同一条理由）。这张表是三处消费点的唯一出处：
 *
 *   * 展示注册表（`presentation/roleProfiles`）：从属字段只在开关开着时渲染；
 *   * 控件形态（`presentation/registry`）：开关本身画成「＋添加背景」/ 开关；
 *   * 关掉效果的动作（`store/actions.disableTextEffect`）：连同从属字段的
 *     override 一起清掉——留着的话「用户改过的必须能看到」会把它们再摆回来，
 *     而它们此刻没有任何作用。
 *
 * prop 名是 `engine/manifest._text_fields` 发的字面量，与画布文字
 * （`TextObject.bg` / `borderColor`）**不是**同一套字段：画布那侧没有开关，
 * 「有没有背景」就是字段在不在（`TextSection`）。两侧共享的是操作模式
 * （关着只给「＋添加」，开了才铺参数），不是数据形状。
 */
export const TEXT_EFFECTS: Readonly<Record<string, readonly string[]>> = {
  bbox_visible: [
    'bbox_facecolor',
    'bbox_alpha',
    'bbox_edgecolor',
    'bbox_linewidth',
    'bbox_pad',
    'bbox_rounded',
  ],
  stroke_enabled: ['stroke_color', 'stroke_width'],
}

/** 这个属性是不是某个效果的开关 */
export const isTextEffectSwitch = (prop: string): boolean => prop in TEXT_EFFECTS

/** 从属字段 → 它归哪个开关管；不归任何效果管就回 null */
export function textEffectSwitchOf(prop: string): string | null {
  for (const [sw, deps] of Object.entries(TEXT_EFFECTS)) {
    if (deps.includes(prop)) return sw
  }
  return null
}
