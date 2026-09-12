import type { EditableField } from '@/lib/api'
import { isTextEffectSwitch } from '@/lib/textEffects'
import { groupRank } from '../roles/registry'
import { ROLE_PROFILES } from './roleProfiles'
import type {
  ControlKind,
  InspectorPriority,
  PresentedBuckets,
} from './types'

/**
 * 展示注册表：把 manifest 字段分桶（primary / more / advanced）并决定控件形态。
 *
 * 三条纪律：
 *   1. manifest 是能力权威——这里**只排版不裁能力**，字段进来多少出去多少；
 *   2. 未知角色 / 未知字段有兜底：无 group 的进 primary、有 group 的进 more、
 *      「高级」「排列」组进 advanced，原文显示也比隐藏好；
 *   3. 已被用户改过的字段永远显示（条件显示与折叠都要给它让路）。
 */

/** 引擎的这两个 group 天然是低频层（层级 zorder、诊断类） */
const ADVANCED_GROUPS = new Set(['高级', '排列'])
/** 与角色无关的低频属性：层级、figure 分数坐标的裸 rect */
const ADVANCED_PROPS = new Set(['zorder', 'position'])

/**
 * enum 字段的视觉控件按 prop 名认，不按选项内容猜。
 * 未列出的 enum 落回文字 Select（带明确标签的 fallback）。
 */
const CONTROL_BY_PROP: Record<string, ControlKind> = {
  linestyle: 'line-style',
  grid_linestyle: 'line-style',
  handle_linestyle: 'line-style',
  marker: 'marker',
  handle_marker: 'marker',
  hatch: 'hatch',
  cmap: 'colormap',
  fontfamily: 'font',
  arrowstyle: 'arrow-style',
}

/**
 * 0–1 的透明度类字段：界面按百分比显示与输入，写回仍是 0–1（审计 T16 / T20）。
 * **按 prop 名点名**，不按「min 0 max 1 就是百分比」猜——`framealpha` 与 `alpha`
 * 是透明度，而一个恰好落在 0–1 的比例（如 `handlelength` 的某些取值）不是。
 * 换算只在 `controls/PercentField` 一处。
 */
const PERCENT_PROPS = new Set(['alpha', 'grid_alpha', 'framealpha', 'bbox_alpha'])

/** 这个数值字段是不是按百分比显示的透明度（批量行与单元素行共用一条判据） */
export const isPercentField = (field: EditableField): boolean =>
  field.type === 'number' && PERCENT_PROPS.has(field.prop)

/**
 * 一句短提示，挂在标签与输入框上（悬停 / 辅助技术），**不加问号按钮**。
 *
 * 只给「单位或语义会被读错」的那几条——审计的统一验收规则说得很直接：
 * 常规字段不默认附带点击式问号，无操作的短提示悬停或聚焦时出现即可。
 * 表里放的是 i18n key 的尾段（`hint.<key>`），文案在 inspector.json。
 *
 * `size`（散点面积）是这一条的由来：单位 pt² 是**面积**不是直径，而
 * 「点大小 12」看着像个长度（审计 T16：保留面积单位，并用简短提示说明）。
 */
const FIELD_HINTS: Record<string, string> = {
  size: 'scatterSize',
  // ---- 术语桥（2026-09-12 critique P1）：主用户的脚本多半是编码 Agent 写的，
  // 他不一定认识这些词，但界面上的词必须与他将要读、将要让 Agent 改的脚本一致
  // （PRODUCT.md「就地教词，不改词」）。一句话说清**它改的是什么**，不复述标签。
  zorder: 'zorder',
  alpha: 'alpha',
  framealpha: 'alpha',
  bbox_alpha: 'alpha',
  grid_alpha: 'alpha',
  ha: 'ha',
  va: 'va',
  linespacing: 'linespacing',
  labelpad: 'labelpad',
  spine_top: 'spines',
  spine_right: 'spines',
  spine_bottom: 'spines',
  spine_left: 'spines',
  spine_color: 'spines',
  spine_linewidth: 'spines',
  ticks_top: 'tickSides',
  ticks_right: 'tickSides',
  ticks_bottom: 'tickSides',
  ticks_left: 'tickSides',
  grid_x: 'gridAxis',
  grid_y: 'gridAxis',
  loc: 'legendLoc',
  loc_anchor: 'legendAnchor',
  frameon: 'frameon',
  ncol: 'ncol',
  borderpad: 'legendSpacingUnit',
  labelspacing: 'legendSpacingUnit',
  handlelength: 'legendSpacingUnit',
  handletextpad: 'legendSpacingUnit',
  columnspacing: 'legendSpacingUnit',
  aspect: 'aspect',
  xscale: 'scale',
  yscale: 'scale',
  invert_x: 'invert',
  invert_y: 'invert',
  direction: 'tickDirection',
  major_mode: 'majorMode',
  minor_mode: 'minorMode',
  format: 'tickFormat',
  minor_format: 'tickFormat',
  mutation_scale: 'mutationScale',
  markersize: 'markersize',
  vmin: 'clim',
  vmax: 'clim',
  extend: 'cbExtend',
  transparent: 'transparent',
  size_mm: 'sizeMm',
  stroke_enabled: 'stroke',
  bbox_visible: 'bbox',
}

/** 这个字段有没有一句短提示（返回 i18n 的 `hint.<key>` 尾段） */
export const fieldHintKey = (prop: string): string | undefined => FIELD_HINTS[prop]

/* ------------------------------ 术语桥：matplotlib 名 ------------------------------ */

/**
 * 每个属性对应的 **matplotlib 调用**，挂在标签的气泡里（与上面的短提示同一个气泡）。
 *
 * 为什么要它：产品的术语立场是「界面用 matplotlib 原生词」（PRODUCT.md，2026-09-12
 * 确认），而 136 个显示名全是日常词——「堆叠层级」对得上脚本里的 `zorder` 吗？用户
 * 回头让 Codex 改脚本时得说得出那个词。气泡第一行就是那个词。
 *
 * 这张表**镜像 `src/tavotto/engine/overrides.py` 的 `HANDLERS`**：每一条写的是引擎
 * 真正调用的 setter / 关键字，不是凭属性名猜的。它只是展示用的词表，不参与任何
 * 判据；引擎换了实现时这里要跟着改（表头注明来源就是为了让下一个人找得到）。
 * **查不到就不显示**——宁可没有第一行，也不给 Tavotto 自造的复合属性
 * （`entry_order`、`binding`、`axis_arrows`）编一个不存在的 matplotlib 名。
 *
 * 同名属性在不同角色下调的不是同一个方法（`color` 在文字上是 `Text.set_color()`，
 * 在刻度上是 `tick_params(labelcolor=)`），所以先按角色查、再回落到通用表。
 */
const TEXT_ROLES = new Set(['text', 'title', 'axis_label', 'ticklabel', 'legend_text'])

const MPL_TERMS_GENERIC: Record<string, string> = {
  visible: 'set_visible()',
  zorder: 'set_zorder()',
  alpha: 'set_alpha()',
  color: 'set_color()',
  linewidth: 'set_linewidth()',
  linestyle: 'set_linestyle()',
  facecolor: 'set_facecolor()',
  edgecolor: 'set_edgecolor()',
  label: 'set_label()',
  hatch: 'set_hatch()',
}

const MPL_TERMS_TEXT: Record<string, string> = {
  text: 'Text.set_text()',
  fontsize: 'Text.set_fontsize()',
  color: 'Text.set_color()',
  weight: 'Text.set_fontweight()',
  style: 'Text.set_fontstyle()',
  rotation: 'Text.set_rotation()',
  fontfamily: 'Text.set_fontfamily()',
  ha: 'Text.set_ha()',
  va: 'Text.set_va()',
  linespacing: 'Text.set_linespacing()',
  labelpad: 'Axis.labelpad',
  bbox_visible: 'Text.set_bbox()',
  bbox_facecolor: 'Text.set_bbox(facecolor=)',
  bbox_edgecolor: 'Text.set_bbox(edgecolor=)',
  bbox_linewidth: 'Text.set_bbox(linewidth=)',
  bbox_alpha: 'Text.set_bbox(alpha=)',
  bbox_pad: 'Text.set_bbox(pad=)',
  bbox_rounded: 'Text.set_bbox(boxstyle=)',
  stroke_enabled: 'patheffects.withStroke()',
  stroke_color: 'withStroke(foreground=)',
  stroke_width: 'withStroke(linewidth=)',
}

const MPL_TERMS_BY_ROLE: Record<string, Record<string, string>> = {
  line: {
    marker: 'Line2D.set_marker()',
    markersize: 'Line2D.set_markersize()',
    markerfacecolor: 'Line2D.set_markerfacecolor()',
    markeredgecolor: 'Line2D.set_markeredgecolor()',
  },
  scatter: {
    size: 'PathCollection.set_sizes()',
    marker: 'PathCollection.set_paths()',
  },
  bar: { bar_width: 'Rectangle.set_width()' },
  bar_series: { bar_width: 'Rectangle.set_width()' },
  legend: {
    frameon: 'Legend.set_frame_on()',
    fontsize: 'Legend.get_texts()[i].set_fontsize()',
    loc: 'legend(loc=)',
    loc_anchor: 'legend(bbox_to_anchor=)',
    title: 'Legend.set_title()',
    title_fontsize: 'Legend.get_title().set_fontsize()',
    facecolor: 'Legend.get_frame().set_facecolor()',
    edgecolor: 'Legend.get_frame().set_edgecolor()',
    framealpha: 'Legend.get_frame().set_alpha()',
    frame_linewidth: 'Legend.get_frame().set_linewidth()',
    frame_rounded: 'legend(fancybox=)',
    ncol: 'legend(ncol=)',
    borderpad: 'legend(borderpad=)',
    labelspacing: 'legend(labelspacing=)',
    handlelength: 'legend(handlelength=)',
    handletextpad: 'legend(handletextpad=)',
    columnspacing: 'legend(columnspacing=)',
  },
  axes: {
    xlim: 'Axes.set_xlim()',
    ylim: 'Axes.set_ylim()',
    xscale: 'Axes.set_xscale()',
    yscale: 'Axes.set_yscale()',
    invert_x: 'Axes.invert_xaxis()',
    invert_y: 'Axes.invert_yaxis()',
    aspect: 'Axes.set_aspect()',
    facecolor: 'Axes.set_facecolor()',
    position: 'Axes.set_position()',
    grid_x: "Axes.grid(axis='x')",
    grid_y: "Axes.grid(axis='y')",
    grid_visible: 'Axes.grid()',
    grid_color: 'tick_params(grid_color=)',
    grid_linestyle: 'tick_params(grid_linestyle=)',
    grid_linewidth: 'tick_params(grid_linewidth=)',
    grid_alpha: 'tick_params(grid_alpha=)',
    ticks_top: 'tick_params(top=)',
    ticks_right: 'tick_params(right=)',
    ticks_bottom: 'tick_params(bottom=)',
    ticks_left: 'tick_params(left=)',
    spine_top: "spines['top'].set_visible()",
    spine_right: "spines['right'].set_visible()",
    spine_bottom: "spines['bottom'].set_visible()",
    spine_left: "spines['left'].set_visible()",
    spine_top_color: "spines['top'].set_color()",
    spine_right_color: "spines['right'].set_color()",
    spine_bottom_color: "spines['bottom'].set_color()",
    spine_left_color: "spines['left'].set_color()",
    spine_top_linewidth: "spines['top'].set_linewidth()",
    spine_right_linewidth: "spines['right'].set_linewidth()",
    spine_bottom_linewidth: "spines['bottom'].set_linewidth()",
    spine_left_linewidth: "spines['left'].set_linewidth()",
    spine_color: 'spines[...].set_color()',
    spine_linewidth: 'spines[...].set_linewidth()',
  },
  axes3d: {
    elev: 'Axes3D.view_init(elev=)',
    azim: 'Axes3D.view_init(azim=)',
    roll: 'Axes3D.view_init(roll=)',
    proj_type: 'Axes3D.set_proj_type()',
    pane_visible: 'axis.pane.set_visible()',
    pane_color: 'axis.pane.set_facecolor()',
    axline_color: 'axis.line.set_color()',
    axline_width: 'axis.line.set_linewidth()',
  },
  ticks: {
    fontsize: 'tick_params(labelsize=)',
    color: 'tick_params(labelcolor=)',
    rotation: 'tick_params(labelrotation=)',
    visible: 'tick_params(labelbottom= / labelleft=)',
    direction: 'tick_params(direction=)',
    length: 'tick_params(length=)',
    width: 'tick_params(width=)',
    minor_length: "tick_params(which='minor', length=)",
    minor_width: "tick_params(which='minor', width=)",
    major_mode: 'Axis.set_major_locator()',
    major_step: 'MultipleLocator()',
    major_values: 'FixedLocator()',
    minor_visible: 'Axis.set_minor_locator()',
    minor_mode: 'Axis.set_minor_locator()',
    minor_step: "MultipleLocator() (which='minor')",
    format: 'Axis.set_major_formatter()',
    minor_format: 'Axis.set_minor_formatter()',
  },
  figure: {
    size_mm: 'Figure.set_size_inches()',
    facecolor: 'Figure.patch.set_facecolor()',
    transparent: 'Figure.patch.set_visible()',
  },
  image: {
    cmap: 'AxesImage.set_cmap()',
    vmin: 'set_clim(vmin=)',
    vmax: 'set_clim(vmax=)',
    interpolation: 'AxesImage.set_interpolation()',
    origin: 'imshow(origin=)',
  },
  colorbar: {
    label: 'Colorbar.set_label()',
    cmap: 'Colorbar.mappable.set_cmap()',
    vmin: 'Colorbar.mappable.set_clim(vmin=)',
    vmax: 'Colorbar.mappable.set_clim(vmax=)',
    tick_fontsize: 'cb.ax.tick_params(labelsize=)',
    tick_color: 'cb.ax.tick_params(labelcolor=)',
    outline_visible: 'Colorbar.outline.set_visible()',
    outline_width: 'Colorbar.outline.set_linewidth()',
    orientation: 'colorbar(orientation=)',
    extend: 'colorbar(extend=)',
  },
  arrow_patch: {
    color: 'FancyArrowPatch.set_color()',
    mutation_scale: 'FancyArrowPatch.set_mutation_scale()',
    arrowstyle: 'FancyArrowPatch.set_arrowstyle()',
    endpoints_frac: 'FancyArrowPatch.set_positions()',
  },
}

/** 这个字段在 matplotlib 里叫什么（找不到就 undefined，不编） */
export function mplTermOf(prop: string, role?: string): string | undefined {
  if (role) {
    const scoped = MPL_TERMS_BY_ROLE[role]?.[prop]
    if (scoped) return scoped
    if (TEXT_ROLES.has(role) && MPL_TERMS_TEXT[prop]) return MPL_TERMS_TEXT[prop]
  }
  return MPL_TERMS_GENERIC[prop]
}

/**
 * 「图上看得见、但引擎没发编辑字段」的外观属性。
 *
 * 柱形是现成的例子：脚本给柱子画了斜线纹理，属性面板里却连一行纹理都没有
 * ——用户会在面板里反复找（审计 T19）。**这里不给引擎加字段**：新增纹理
 * 编辑能力是另一件事，审计原文明说「不能当作纯文案修复」。能做的是把
 * 「这一项在这里改不了、它来自脚本」说出口，并给出源对象入口。
 *
 * 判据是**这个元素此刻的字段表里没有它**，不是「柱形永远没有纹理」——
 * 引擎哪天真发了这个字段，这条提示自己就消失了，不需要有人记得回来删。
 */
const APPEARANCE_ABSENT: Record<string, string[]> = {
  bar: ['hatch'],
  bar_series: ['hatch'],
}

/** 这个角色该说、而 manifest 此刻没发的外观属性 */
export function absentAppearance(role: string, fields: EditableField[]): string[] {
  const listed = APPEARANCE_ABSENT[role]
  if (!listed) return []
  const have = new Set(fields.map((f) => f.prop))
  return listed.filter((p) => !have.has(p))
}

const CONTROL_BY_TYPE: Record<EditableField['type'], ControlKind> = {
  text: 'text',
  number: 'number',
  color: 'color',
  bool: 'toggle',
  enum: 'select',
  pair: 'pair',
  rect: 'rect',
  order: 'order',
  number_list: 'number-list',
}

export function controlKindOf(role: string, field: EditableField): ControlKind {
  // 图例位置是角色专属语义（3×3 网格）；别的角色如果哪天也发 loc，回落 Select
  if (field.prop === 'loc' && role === 'legend' && field.type === 'enum') {
    return 'legend-position'
  }
  // 色条的方向 / 两端延伸：用当前色图画的小色条预览，不是两个文字下拉（审计 T23）
  if (role === 'colorbar' && field.type === 'enum') {
    if (field.prop === 'orientation') return 'colorbar-orientation'
    if (field.prop === 'extend') return 'colorbar-extend'
  }
  // 三维子图的投影方式：透视 / 正交各一个小立方体（审计 T24）
  if (field.prop === 'proj_type' && role === 'axes3d' && field.type === 'enum') {
    return 'projection'
  }
  // 图例项的绑定：一行状态 + 动作（跟随 / 自定义），不是一个下拉
  if (field.prop === 'binding' && role === 'legend_text' && field.type === 'enum') {
    return 'legend-binding'
  }
  // 子图纵横比：引擎按 text 发（'auto' | 'equal' | 数字串），但它不是一段文字
  // ——落进带上下标 / 换行 / 大小写转换的富文本编辑器是审计 T12 点名的错配。
  // 按 prop + 角色认，不按「值长得像什么」猜：给它一个明确的控件形态
  if (field.prop === 'aspect' && role === 'axes' && field.type === 'text') {
    return 'aspect'
  }
  const byProp = CONTROL_BY_PROP[field.prop]
  if (byProp && field.type === 'enum') return byProp
  if (isPercentField(field)) return 'percent'
  // 背景 / 描边的开关：关着的时候不是一个开关，是一条「＋添加背景」入口
  // （与画布文字 `TextSection` 同一种操作模式；表在 `lib/textEffects`）
  if (field.type === 'bool' && isTextEffectSwitch(field.prop)) return 'effect'
  return CONTROL_BY_TYPE[field.type] ?? 'text'
}

export interface PresentOptions {
  /** 该属性是否已被用户改过（override 存在） */
  isOverridden: (prop: string) => boolean
  /** 当前值读取（override 优先），供条件显示判断 */
  read: (prop: string) => unknown
}

/** 桶内排序的大偏移：显式点名的排前（0..n），兜底的按引擎组序 + 出现序跟在后面 */
const FALLBACK_BASE = 1000

/**
 * 单个字段此刻该不该显示：模板的 `visibleWhen`（「开关 → 从属字段」那张表）
 * + 「用户改过的必须能看到」。**只有这一条判据**：`presentFields` 分桶用它，
 * 不走桶的复合控件（刻度卡的次刻度长宽）也用它——次刻度关着时该收起哪些行，
 * 通用列表与刻度卡说的必须是同一句话。
 */
export function fieldVisible(role: string, prop: string, opts: PresentOptions): boolean {
  const cond = ROLE_PROFILES[role]?.visibleWhen?.[prop]
  return !cond || cond(opts.read) || opts.isOverridden(prop)
}

/**
 * 这个角色里与 `prop` 并排成一行的另一条字段（模板 `pairRows`）；没有就 null。
 * 只回答「谁和谁一对」，画不画在一行由列表按「两条都在同一桶里」决定。
 */
export function pairedProp(role: string, prop: string): string | null {
  for (const [a, b] of ROLE_PROFILES[role]?.pairRows ?? []) {
    if (a === prop) return b
    if (b === prop) return a
  }
  return null
}

export function presentFields(
  role: string,
  fields: EditableField[],
  opts: PresentOptions,
): PresentedBuckets {
  const profile = ROLE_PROFILES[role]
  const out: PresentedBuckets = { primary: [], more: [], advanced: [] }

  fields.forEach((field, engineIndex) => {
    // 条件显示：模式从属字段只在对应模式下渲染；用户改过的必须能看到
    if (!fieldVisible(role, field.prop, opts)) return

    let priority: InspectorPriority
    let order: number

    const pi = profile?.primary.indexOf(field.prop) ?? -1
    const mi = profile?.more?.indexOf(field.prop) ?? -1
    const ai = profile?.advanced?.indexOf(field.prop) ?? -1
    // 兜底顺序：无 group 的排在有 group 的前面，其余按引擎组序 + 出现序
    const fallbackOrder =
      FALLBACK_BASE + (field.group ? groupRank(field.group) : -1) * 100 + engineIndex

    if (pi >= 0) {
      priority = 'primary'
      order = pi
    } else if (ai >= 0) {
      priority = 'advanced'
      order = ai
    } else if (ADVANCED_PROPS.has(field.prop) || ADVANCED_GROUPS.has(field.group ?? '')) {
      priority = 'advanced'
      order = fallbackOrder
    } else if (mi >= 0) {
      priority = 'more'
      order = mi
    } else if (profile) {
      // 建过档的角色：没点名的字段一律进「更多」，不丢
      priority = 'more'
      order = fallbackOrder
    } else {
      // 未建档角色：沿用「无 group 平铺在前」的老约定
      priority = field.group ? 'more' : 'primary'
      order = fallbackOrder
    }

    out[priority].push({
      field,
      priority,
      control: controlKindOf(role, field),
      order,
    })
  })

  for (const bucket of [out.primary, out.more, out.advanced]) {
    bucket.sort((a, b) => a.order - b.order)
  }
  return out
}
