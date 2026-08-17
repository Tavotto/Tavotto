import { optionLabel as baseOptionLabel, PROP_LABEL } from '@/store/actions'

/**
 * 图内元素属性的显示注册表。
 *
 * 引擎 manifest 只给 prop 名和 group 名，中文与顺序全在前端。这里是唯一的
 * 真源：新增角色时只补这张表，动态表单不需要改。
 */

/** R17 新增/扩展角色的属性中文名（基础项仍复用 store 的 PROP_LABEL） */
const R17_LABEL: Record<string, string> = {
  // figure
  facecolor: '背景色',
  transparent: '透明背景',

  // axes · 数据范围
  xscale: 'X 轴缩放',
  yscale: 'Y 轴缩放',
  invert_x: '反转 X 轴',
  invert_y: '反转 Y 轴',
  aspect: '纵横比',

  // axes · 网格与边框
  grid_x: 'X 网格',
  grid_y: 'Y 网格',
  grid_color: '网格颜色',
  grid_linestyle: '网格线型',
  grid_linewidth: '网格线宽',
  grid_alpha: '网格透明度',
  spine_top: '上边框',
  spine_right: '右边框',
  spine_bottom: '下边框',
  spine_left: '左边框',
  spine_color: '边框颜色',
  spine_linewidth: '边框线宽',

  // image / colorbar · 颜色映射
  cmap: '色图',
  vmin: '色阶下限',
  vmax: '色阶上限',
  interpolation: '插值',
  origin: '原点位置',

  // line · 线条与标记
  marker: '标记',
  markersize: '标记大小',
  markerfacecolor: '标记填充',
  markeredgecolor: '标记描边',

  // scatter / fill / bar
  size: '点大小',
  edgecolor: '描边色',
  bar_width: '柱宽',

  // errorbar
  capsize: '端帽长度',
  cap_thickness: '端帽粗细',

  // legend
  loc: '位置',
  title_fontsize: '标题字号',
  framealpha: '边框透明度',
  entry_order: '条目顺序',
  ncol: '列数',
  borderpad: '内边距',
  labelspacing: '行距',
  handlelength: '图例线长',

  // colorbar
  tick_fontsize: '刻度字号',
  tick_color: '刻度颜色',
  outline_visible: '外框',
  outline_width: '外框线宽',

  // ticks
  direction: '刻度朝向',
  length: '刻度长度',
  width: '刻度粗细',
  format: '数值格式',

  // axes3d · 视角 / 坐标轴
  elev: '俯仰角',
  azim: '方位角',
  roll: '侧倾角',
  axline_color: '轴线颜色',
  axline_width: '轴线线宽',
  pane_visible: '背景面板',
  pane_color: '面板颜色',
  grid_visible: '网格',
  proj_type: '投影方式',
  axis_arrows: '轴箭头',
  arrow_color: '箭头颜色',
  arrow_width: '箭头线宽',
  arrow_head: '箭头大小',

  // arrow_patch（图内独立箭头 / 标注箭头）
  mutation_scale: '箭头帽大小',
  arrowstyle: '箭头样式',

  // image · 单色渐变填充（imshow 渐变 + 裁剪路径的画法）
  gradient_color: '渐变基色',

  // 通用
  label: '名称',
  zorder: '堆叠层级',
}

/**
 * 同名属性在不同角色下说的不是一回事：figure/axes 的 facecolor 是背景，
 * 柱/散点/填充的 facecolor 是图元自己的填充色，叫「背景色」会误导。
 */
const ROLE_LABEL: Record<string, Record<string, string>> = {
  bar: { facecolor: '填充色' },
  bar_series: { facecolor: '填充色' },
  scatter: { facecolor: '填充色' },
  fill: { facecolor: '填充色' },
}

export const propLabel = (prop: string, role?: string) =>
  (role ? ROLE_LABEL[role]?.[prop] : undefined) ?? R17_LABEL[prop] ?? PROP_LABEL[prop] ?? prop

/** 角色的中文名，用在「已选 N 个文字」这类多选标题上 */
const ROLE_NAME: Record<string, string> = {
  figure: '整张图',
  axes: '子图',
  axes3d: '3D 子图',
  image: '图像',
  line: '曲线',
  scatter: '散点系列',
  fill: '填充区域',
  bar_series: '柱形系列',
  bar: '柱',
  errorbar: '误差棒',
  legend: '图例',
  legend_text: '图例项',
  colorbar: '色条',
  ticks: '刻度组',
  ticklabel: '刻度文字',
  axis_label: '轴标题',
  title: '标题',
  text: '文字',
  arrow_patch: '箭头',
}

export const roleName = (role: string) => ROLE_NAME[role] ?? '同类元素'

/**
 * enum 选项的中文名。色图名（viridis…）与格式串（%.1f）保持原文——
 * 它们是 matplotlib 的标识符，翻译反而让人对不上文档。
 */
const R17_ENUM: Record<string, Record<string, string>> = {
  xscale: { linear: '线性', log: '对数', symlog: '对称对数', logit: 'logit' },
  yscale: { linear: '线性', log: '对数', symlog: '对称对数', logit: 'logit' },
  aspect: { auto: '自动', equal: '等比' },
  direction: { out: '朝外', in: '朝内', inout: '跨轴' },
  format: { auto: '自动', sci: '科学计数' },
  interpolation: {
    auto: '自动',
    nearest: '最近邻',
    bilinear: '双线性',
    bicubic: '双三次',
    lanczos: 'Lanczos',
    none: '无',
  },
  origin: { upper: '左上', lower: '左下' },
  proj_type: { persp: '透视', ortho: '正交' },
  // matplotlib 的 marker 是单字符代号，直接显示 "o"/"^" 没人看得懂
  marker: {
    None: '无',
    none: '无',
    '': '无',
    original: '脚本原始',
    o: '圆点',
    s: '方块',
    D: '菱形',
    d: '瘦菱形',
    '^': '上三角',
    v: '下三角',
    '<': '左三角',
    '>': '右三角',
    x: '叉号',
    '+': '加号',
    '*': '星形',
    '.': '小点',
    p: '五边形',
    h: '六边形',
  },
  weight: { normal: '常规', bold: '加粗', light: '细体', medium: '中等', semibold: '半粗', heavy: '特粗' },
  style: { normal: '正体', italic: '斜体', oblique: '倾斜' },
  grid_linestyle: { '-': '实线', '--': '虚线', '-.': '点划线', ':': '点线' },
  linestyle: { '-': '实线', '--': '虚线', '-.': '点划线', ':': '点线', none: '无' },
  // matplotlib 的 arrowstyle 代号（"-|>" 等）直接显示没人看得懂
  arrowstyle: {
    '-': '无箭头',
    '->': '细箭头',
    '-|>': '实心箭头',
    '<-': '反向细箭头',
    '<|-': '反向实心箭头',
    '<->': '双向细箭头',
    '<|-|>': '双向实心箭头',
    '|-|': '两端竖线',
    ']-[': '两端方括号',
    simple: '简约',
    fancy: '花式',
    wedge: '楔形',
    custom: '自定义（脚本设定）',
  },
  loc: {
    best: '自动',
    'upper right': '右上',
    'upper left': '左上',
    'lower left': '左下',
    'lower right': '右下',
    right: '右',
    'center left': '左中',
    'center right': '右中',
    'lower center': '下中',
    'upper center': '上中',
    center: '居中',
    custom: '自定义（拖动过）',
  },
}

export const optionLabel = (prop: string, value: string) =>
  R17_ENUM[prop]?.[value] ?? baseOptionLabel(prop, value)

/**
 * 分组的稳定顺序。manifest 里 group 出现的次序取决于引擎实现，
 * 按这张表排才能保证同类角色之间的版面一致。未登记的分组排在最后。
 */
const GROUP_ORDER = [
  '位置与尺寸',
  '视角',
  '数据范围',
  '坐标轴',
  '轴箭头',
  '刻度',
  '刻度线',
  '网格与边框',
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

/**
 * 引擎明确不支持的能力：不画空白页、不摆假的 disabled 控件，
 * 而是说清为什么，并把用户导向改图助手（那条路真能做到）。
 */
export const UNSUPPORTED: Record<string, { title: string; reason: string }> = {
  colorbar: {
    title: '色条方向',
    reason:
      '翻转方向必须销毁并重建色条轴，这会打乱全图元素的稳定编号（gid），' +
      '已有修改与撤销都会失效——请在脚本里改 orientation（可用改图助手）。',
  },
}

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
  frameon: false,
  transparent: false,
  invert_x: false,
  invert_y: false,
  outline_visible: false,
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
