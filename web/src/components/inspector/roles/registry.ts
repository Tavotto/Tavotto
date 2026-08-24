import { optionLabel as baseOptionLabel, propLabel as basePropLabel } from '@/store/actions'
import { t } from '@/i18n'

/**
 * 图内元素属性的显示注册表。
 *
 * 引擎 manifest 只给 prop 名和 group 名，**显示名与顺序全在前端**。这里是
 * 唯一的真源：新增角色时只补这张表，动态表单不需要改。
 *
 * ## 为什么表里还留着中文
 *
 * `ENGINE_GROUP` 与 `ENGINE_LABEL_PATTERNS` 的键是**引擎协议里的字面量**，
 * 不是界面文案：manifest 里的 `group` 与 `label` 今天就是这些中文串，它们是
 * worker 发过来的数据。前端在这里把它们映射成 i18n key，**认不出来的原样
 * 透出**——脚本自定义的分组、引擎新加的元素类型都不会因此变成空白。
 *
 * 更干净的做法是让引擎发稳定 id（`group: "typography"`）而不是散文，但那是
 * worker 协议的改动，会动到写回校验里的 manifest 比对与整条渲染链路，本次
 * 多语言改造刻意不碰。见 docs/i18n.md 的「边界」一节。
 */

/**
 * 同名属性在不同角色下说的不是一回事：figure/axes 的 facecolor 是背景，
 * 柱/散点/填充的 facecolor 是图元自己的填充色，叫「背景色」会误导。
 */
const ROLE_SCOPED_PROPS: Record<string, string[]> = {
  bar: ['facecolor'],
  bar_series: ['facecolor'],
  scatter: ['facecolor'],
  fill: ['facecolor'],
  // 脚本 add_patch 出的独立形状：facecolor 是它自己的填充
  patch: ['facecolor'],
}

/**
 * 属性显示名。查找顺序：角色专属 → 通用 → store 里的基础表 → **属性名原文**。
 *
 * 最后那条回退是硬要求：matplotlib 的属性是开放集合，脚本能暴露前端从没见过
 * 的 prop。查不到时显示 `linewidth` 比显示空白或 `inspector.prop.linewidth`
 * 有用得多。
 */
export const propLabel = (prop: string, role?: string): string => {
  if (role && ROLE_SCOPED_PROPS[role]?.includes(prop)) {
    const scoped = t(`propByRole.${role}.${prop}`, { ns: 'inspector', defaultValue: '' })
    if (scoped) return scoped
  }
  const generic = t(`prop.${prop}`, { ns: 'inspector', defaultValue: '' })
  return generic || basePropLabel(prop)
}

/** 角色显示名，用在「已选 N 个文字」这类多选标题上；未知角色回落到通用说法。 */
export const roleName = (role: string): string =>
  t(`role.${role}`, { ns: 'inspector', defaultValue: '' }) || t('role.unknown', { ns: 'inspector' })

/**
 * enum 选项的显示名。色图名（viridis…）与格式串（%.1f）保持原文——它们是
 * matplotlib 的标识符，翻译反而让人对不上文档；脚本自定义的枚举值同理。
 */
export const optionLabel = (prop: string, value: string): string => {
  const hit = t(`enum.${prop}.${value}`, { ns: 'inspector', defaultValue: '' })
  return hit || baseOptionLabel(prop, value)
}

/* ---------------------- 引擎发过来的分组名 → 显示名 ------------------------ */

/** manifest 的 `group` 字面量 → i18n key。键是协议数据，不是界面文案。 */
const ENGINE_GROUP: Record<string, string> = {
  位置与尺寸: 'geometry',
  视角: 'view',
  数据范围: 'dataRange',
  坐标轴: 'axis',
  轴箭头: 'axisArrows',
  刻度: 'ticks',
  刻度线: 'tickMarks',
  刻度定位: 'tickLocator',
  网格与边框: 'gridFrame',
  '边框（逐条）': 'gridFramePerSide',
  线条与标记: 'lineMarker',
  渐变填充: 'gradientFill',
  颜色映射: 'colormap',
  文字: 'text',
  排版: 'typography',
  背景: 'background',
  描边: 'stroke',
  图例: 'legend',
  样式: 'style',
  布局: 'layout',
  排列: 'arrange',
  高级: 'advanced',
}

/** 分组显示名；引擎发来没登记过的分组原样透出。 */
export const groupLabel = (name: string): string => {
  const key = ENGINE_GROUP[name]
  return key ? t(`group.${key}`, { ns: 'inspector' }) : name
}

/**
 * 分组的稳定顺序。manifest 里 group 出现的次序取决于引擎实现，按这张表排
 * 才能保证同类角色之间的版面一致。**按引擎名排，不按显示名**——否则换成
 * 英文界面后版面顺序会跟着字母序漂走。未登记的分组排在最后。
 */
const GROUP_ORDER = [
  '位置与尺寸',
  '视角',
  '数据范围',
  '坐标轴',
  '轴箭头',
  '刻度',
  '刻度线',
  '刻度定位',
  '网格与边框',
  '边框（逐条）',
  '线条与标记',
  '渐变填充',
  '颜色映射',
  '文字',
  '排版',
  '背景',
  '描边',
  '图例',
  '样式',
  '布局',
  '排列',
  '高级',
]

export const groupRank = (name: string) => {
  const i = GROUP_ORDER.indexOf(name)
  return i < 0 ? GROUP_ORDER.length : i
}

/* ---------------------- 引擎发过来的元素名 → 显示名 ------------------------ */

/**
 * manifest 的 `label` 是**带用户内容的散文**（`曲线 “电流密度”`、`子图 2`）。
 * 这里按引擎当前的构词法把它拆成「类型 + 序号/内容」再用当前语言重组。
 *
 * 三条纪律：
 *   ① 引号里的内容是**用户自己的文字**（曲线名、标题、刻度文本），原样带过去，
 *      一个字都不翻；
 *   ② 匹配不上的原样返回——引擎改了措辞只会退回中文，不会变成空白；
 *   ③ 顺序敏感：`散点 “x”` 必须排在 `散点系列 N` 之前那类前缀冲突要先长后短。
 */
const ENGINE_LABEL_PATTERNS: { re: RegExp; key: string }[] = [
  { re: /^整张图$/, key: 'figure' },
  { re: /^色条轴$/, key: 'colorbarAxes' },
  { re: /^色条$/, key: 'colorbar' },
  { re: /^图例$/, key: 'legend' },
  { re: /^标注箭头$/, key: 'annotationArrow' },
  { re: /^图例标题 “(.*)”$/s, key: 'legendTitleNamed' },
  { re: /^图例项 “(.*)”$/s, key: 'legendEntryNamed' },
  { re: /^柱形系列 “(.*)”$/s, key: 'barSeriesNamed' },
  { re: /^柱形系列 (\d+)$/, key: 'barSeries' },
  { re: /^散点 “(.*)”$/s, key: 'scatterNamed' },
  { re: /^散点系列 (\d+)$/, key: 'scatterSeries' },
  { re: /^曲线 “(.*)”$/s, key: 'lineNamed' },
  { re: /^曲线 (\d+)$/, key: 'line' },
  { re: /^标题 “(.*)”$/s, key: 'titleNamed' },
  { re: /^文字 “(.*)”$/s, key: 'textNamed' },
  { re: /^刻度 “(.*)”$/s, key: 'tickNamed' },
  { re: /^子图 (\d+)$/, key: 'axes' },
  { re: /^柱 (\d+)$/, key: 'bar' },
  { re: /^误差棒 (\d+)$/, key: 'errorbar' },
  { re: /^图像 (\d+)$/, key: 'image' },
  { re: /^填充区域 (\d+)$/, key: 'fillArea' },
  { re: /^形状 (\d+)$/, key: 'shape' },
  { re: /^箭头 (\d+)$/, key: 'arrow' },
  { re: /^([XYZ]) 轴 “(.*)”$/s, key: 'axisLabelNamed' },
  { re: /^([XYZ]) 刻度文字$/, key: 'tickLabels' },
]

/**
 * 引擎元素名 → 当前语言。中文界面下是恒等映射（key 的中文译文与引擎原串
 * 一致），英文界面下重组成英文。
 */
export function engineLabel(label: string): string {
  for (const { re, key } of ENGINE_LABEL_PATTERNS) {
    const m = re.exec(label)
    if (!m) continue
    // 单捕获组统一喂给 {{value}}；轴标签那条是 (轴名, 文字) 两个组
    const values =
      m.length === 3
        ? { axis: m[1], value: m[2] }
        : m.length === 2
          ? { axis: m[1], value: m[1] }
          : {}
    return t(`engineLabel.${key}`, { ns: 'inspector', ...values })
  }
  return label
}

/**
 * 引擎明确不支持的能力：不画空白页、不摆假的 disabled 控件，
 * 而是说清为什么，并把用户导向改图助手（那条路真能做到）。
 *
 * 目前是空表。**色条曾经在这里**——那条说明写的是「翻转方向必须销毁重建
 * 色条轴、会打乱 gid」，而引擎已改成**就地**改造（同一个 Axes 对象，
 * `fig.axes` 顺序一个字节不动，见 engine/overrides.py 的 `_cb_reorient`），
 * 方向与两端延伸都成了普通的 enum 字段。留着这条机制是因为「说清为什么
 * 做不到」比「摆一个点了没反应的控件」好，下一个真做不到的能力还得用它。
 */
export const UNSUPPORTED_ROLES = [] as const

export const unsupportedOf = (role: string): { title: string; reason: string } | undefined =>
  (UNSUPPORTED_ROLES as readonly string[]).includes(role)
    ? {
        title: t(`unsupported.${role}.title`, { ns: 'inspector' }),
        reason: t(`unsupported.${role}.reason`, { ns: 'inspector' }),
      }
    : undefined

/**
 * 字段的「中性默认」——等于它就说明脚本没在这上面做文章。
 * 只登记有明确关闭态的开关与零值，其余字段不表态（避免误判成「有内容」）。
 */
const NEUTRAL: Record<string, unknown> = {
  bbox_visible: false,
  stroke_enabled: false,
  axis_arrows: false,
  visible: true,
  rotation: 0,
  grid_x: false,
  grid_y: false,
  // 刻度线四边开关：matplotlib 默认下/左有、上/右无（issue #92）
  ticks_bottom: true,
  ticks_top: false,
  ticks_left: true,
  ticks_right: false,
  frameon: false,
  transparent: false,
  invert_x: false,
  invert_y: false,
  outline_visible: false,
  major_mode: 'auto',
  minor_mode: 'auto',
  minor_format: 'none',
  extend: 'neither',
}

/**
 * 分组是否「已经有内容」：组里任一字段被脚本设成了非中性值，或用户改过。
 *
 * 这条是可发现性的关键：Ra 标签本来就带黑底（bbox_visible=true），
 * 折叠着会让用户以为背景不可编辑，所以这种组要默认展开。
 */
export function groupHasContent(
  fields: { prop: string; value: unknown }[],
  isOverridden: (prop: string) => boolean,
): boolean {
  return fields.some((f) => {
    if (isOverridden(f.prop)) return true
    if (!(f.prop in NEUTRAL)) return false
    return f.value !== NEUTRAL[f.prop]
  })
}
