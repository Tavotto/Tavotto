import type { EditableField, Manifest, ManifestElement } from './api'
import { t } from '@/i18n'
import { panelScale } from './preflight'
import { sameRules } from './specBinding'
import { effectiveCanvasFamily, type CanvasTextFamily } from './typography'
import type { FigureDocument, PanelObject, PanelOverride, TextObject } from '@/types/document'
import { effectiveOverride } from '@/lib/effectiveOverride'

/**
 * 论文样式预设：一组可复用的排版规格（字号/线宽/刻度/图例/配色/页面）。
 *
 * 应用是纯前端映射：按角色把预设值翻译成图内元素 override 与画布标注属性，
 * 走与手动编辑完全相同的通路（PanelObject.overrides + TextObject 字段），
 * 因此天然进撤销、天然不写回源文件。
 *
 * ### 样式里的数字 = 页面上读者量到的 pt（2026-09-24）
 *
 * 字号与线宽这类以 pt 计的量（`PAGE_PT_PROPS`），样式里写的是**页面上**的值，
 * 与预检量的同一个东西（`preflight.panelScale`：manifest 值 × 面板在页面上的缩放比）。
 * 写进 override 之前除以缩放比换回脚本坐标系，提取时乘回来——面板缩到 60% 时
 * 样式写 9 pt，读者量到的就是 9 pt，而不是 5.4 pt、套完检查照样红。
 * 画布标注的 `sizePt` 本来就是页面上的绝对 pt，不换算。
 */

/**
 * 样式里描述一段画布文字的那一小块。
 *
 * **字段名跟着 `TextObject` 走，取值语义跟着属性能力层走**：`bold` 是
 * boolean（磁盘形状），落进文档时经 `writeCanvasText` 换算成规范值——
 * 样式应用与手动编辑因此写出同一种形状。
 *
 * `fontFamily` 是 Prompt 13 加的：标注能设字体了，样式就得能统一它，
 * 否则「一键把全图对齐到这套样式」会漏掉刚刚新增的那一维。
 */
export interface StyleTextEntry {
  sizePt?: number
  bold?: boolean
  italic?: boolean
  color?: string
  fontFamily?: CanvasTextFamily
}

/**
 * 一份样式的**内容**（信封里的 `data`）。名字、id、revision 在信封上
 * （`ProfileRecord`），不在这里——内容与身份混在一起时，「改个名字」会变成
 * 一次内容修改，乐观并发就再也分不清两个窗口在争什么。
 */
export interface StyleProfileData {
  /** role → prop → value。只登记用户明确要统一的项。 */
  element: Record<string, Record<string, unknown>>
  /** 系列配色（按曲线/散点/柱形在图内的出现顺序循环取色）；空 = 不动配色 */
  palette?: string[]
  /** 画布标注文字样式 */
  annotation?: StyleTextEntry
  /** 子图序号标签 (a)(b)(c) 样式（按内容 ^(x)$ 识别） */
  subLabel?: StyleTextEntry
  /** 页面预设 */
  page?: { w: number; h: number }
  /** 默认画布背景色；不设 = 不动背景 */
  background?: string
  /** 这份样式是从哪套规范派生的（内置样式用；只作说明） */
  derived_from_spec?: string
  /**
   * 以 pt 计的数字按什么口径读（`PAGE_PT_PROPS`）。`'page'` = 页面上读者量到的 pt，写入前
   * ÷ 面板缩放比（2026-09-24 起新建 / 提取 / 内置的样式都带它）；**缺席 = 旧版存下的样式**，
   * 数字就是当年写进 override 的脚本值，照旧原样写——不带标记的老样式换了语义的话，套在
   * 缩到 60% 的图上会从 9 pt 静默变成 15 pt。严格同源：`engine/profilestore._STYLE_KEYS`。
   */
  pt_basis?: 'page'
  /** 导入 / 迁移时没能映射的字段：**留着，不丢**（界面把它记成一条 warning） */
  extra?: Record<string, unknown>
}

/**
 * 编辑中的一份样式（信封 + 内容摊平）。**只活在界面里**：存盘时拆回
 * `{display_name, data}`，见 `profileToDraft` / `draftToData`。
 */
export interface StylePreset extends StyleProfileData {
  id?: string
  name: string
}

/**
 * 预设里允许出现的 role → props 白名单（与 manifest 的 editable 字段一一对应）。
 * **加一项之前先确认 manifest 真的暴露了它**——白名单里多一个渲染层没有的
 * prop，应用时只会安静地进 `unmappable`，用户以为设了、其实什么都没发生。
 *
 * 字体族按 manifest 真实暴露的位置登记：`ticks` 有 `fontfamily`（引擎的
 * `("ticks", "fontfamily")`，ADR 0051）；**图例容器没有**，字体在每条图例项
 * （`legend_text`）上；图例标题没有 override 入口，应用时如实进 `unmappable`
 * （见 `planStyle`），不假装改了。
 */
export const STYLE_ROLE_PROPS: Record<string, string[]> = {
  text: ['fontsize', 'color', 'fontfamily', 'weight', 'style'],
  title: ['fontsize', 'color', 'weight', 'style', 'fontfamily'],
  axis_label: ['fontsize', 'color', 'weight', 'style', 'fontfamily'],
  ticks: ['fontsize', 'fontfamily', 'color', 'direction', 'length', 'width', 'minor_length', 'minor_width'],
  legend: ['fontsize', 'frameon', 'framealpha', 'edgecolor'],
  // 粗体 / 斜体也在图例项上（与字体同一处；容器没有 `weight` / `style`）
  legend_text: ['fontfamily', 'weight', 'style'],
  line: ['linewidth', 'linestyle', 'marker', 'markersize'],
  errorbar: ['linewidth', 'capsize', 'cap_thickness'],
  bar_series: ['linewidth', 'edgecolor'],
  axes: ['spine_linewidth', 'spine_color'],
  colorbar: ['tick_fontsize', 'outline_width'],
}

/**
 * 以 pt 计、**跟着面板缩放**的属性：样式里的值是页面上的 pt，写入前 ÷ 缩放比。
 *
 * 口径与预检同源——`preflight.ts` 量字号（`fontsize` / `tick_fontsize` /
 * `title_fontsize`）与线宽（`linewidth` / `spine_linewidth` / `handle_linewidth`）
 * 时都乘 `scale`；其余同为 pt 的几何量（刻度长宽、标记、误差棒帽、图例框线、
 * 色条外框）预检不量，但它们在页面上同样跟着面板缩放，同一份样式里不该有两种单位。
 * 透明度、颜色、枚举不在表里：它们没有尺寸。
 */
export const PAGE_PT_PROPS: ReadonlySet<string> = new Set([
  'fontsize',
  'title_fontsize',
  'tick_fontsize',
  'linewidth',
  'spine_linewidth',
  'handle_linewidth',
  'frame_linewidth',
  'outline_width',
  'width',
  'minor_width',
  'length',
  'minor_length',
  'markersize',
  'capsize',
  'cap_thickness',
  // 以下几条样式里不出现，但**属性页里**与上面那些并排摆着（2026-09-24，属性页改按页面 pt
  // 显示）：边框卡的「全部」是 `spine_linewidth`、逐边是 `spine_<side>_linewidth`，一张卡里
  // 一个页面值一个脚本值，「多个值」的判断就成了拿两种单位比大小。凡是 manifest 标 `pt`、
  // 随面板**线性**缩放的量都在表里；`size`（散点面积，pt²）不在——它按缩放比的平方走
  'spine_top_linewidth',
  'spine_right_linewidth',
  'spine_bottom_linewidth',
  'spine_left_linewidth',
  'grid_linewidth',
  'bbox_linewidth',
  'stroke_width',
  'handle_markersize',
  'axline_width',
  'arrow_width',
  'arrow_head',
  'mutation_scale',
  'labelpad',
])

/**
 * 页面上的 pt → 写进 override 的脚本值。
 *
 * 取两位小数：manifest 把这些值按两位小数回报（`engine/manifest.py` 的 `round(…, 2)`；表里每一条
 * 都是，`tests/test_page_pt_precision.py` 按 AST 看护——少一位的话输入 8.5 存 14.17、回报 14.2、
 * 回显 8.52），
 * 预检读到的就是那两位——多写的位数下一轮就被截掉。用**就近**取整（误差 ≤ 0.005 × 缩放比）：
 * 一键修复知道自己要往规范的哪一侧走（`issueFix.writeFontSize` 按方向取整），样式不知道
 * 这个数贴着规范的哪条边，就近是唯一不偏向任何一侧的选择。缩放比算不出来时原样写。
 */
export function toScriptValue(prop: string, value: unknown, scale: number): unknown {
  if (!PAGE_PT_PROPS.has(prop) || typeof value !== 'number') return value
  if (!Number.isFinite(scale) || scale <= 0 || scale === 1) return value
  return roundPt(value / scale)
}

/** 脚本值 → 页面上的 pt（显示与提取用），同样两位小数 */
export function toPageValue(prop: string, value: unknown, scale: number): unknown {
  if (!PAGE_PT_PROPS.has(prop) || typeof value !== 'number') return value
  if (!Number.isFinite(scale) || scale <= 0 || scale === 1) return value
  return roundPt(value * scale)
}

/**
 * pt 数值的小数位——换算的取整与数字框的显示**同一个数**（`NumberField precision`）：
 * 字号框以前显示一位小数，页面值 8.25 在属性页上成了 8.3、样式面板与问题面板却是 8.25。
 */
export const PT_DECIMALS = 2

/**
 * 两位小数就近，**用 `toFixed`**：问题面板的「当前 X pt」是预检的 `eff.toFixed(2)`（Python 侧
 * `%.2f`，同一种按真实二进制值的就近），数字框显示也是 `toFixed`。`Math.round(v * 100)` 在
 * 正好「半格」的值上会先乘出一个进位——缩放比 1.33 时脚本值 6.5 × 1.33 = 8.645（二进制里是
 * 8.64499…），问题面板说 8.64，`Math.round` 却给 8.65：同一个字又是两个数。
 */
const roundPt = (v: number) => Number(v.toFixed(PT_DECIMALS))

/**
 * 一张面板的「页面 pt 透镜」：属性页、样式面板、浮动工具条读写图内以 pt 计的量时**都过它**
 * （2026-09-24 用户拍板「统一按页面上的实际大小显示」）。
 *
 * 同一个刻度字号，属性页以前显示脚本里的 9、样式面板与问题面板显示页面上的 5.4——同一个字
 * 三处两个数。换算因此只有这一份：`toPage` / `toScript` 就是上面两个函数，`field` 把 manifest
 * 字段的当前值与上下界一起换到页面上。
 *
 * * **上下界约束的是脚本值**（引擎的字段说它接受什么），这里只是把它**换到页面上显示与钳位**
 *   （`min × scale`），与样式面板的 `PtField` 同一个口径；不另造一套页面上的界。
 * * **取整**：显示两位小数就近，写入两位小数就近（`toScriptValue` 的理由）。缩放比 ≤ 1 时
 *   任何两位小数的页面值写进去再读回来都是原数；> 1 时页面上能表示的值间隔是 0.01 × 缩放比
 *   （manifest 按两位小数回报脚本值），不可表示的输入真实渲染出来离它 ≤ 0.005 × 缩放比，显示出来差
 *   一格（例：缩放比 1.33 时输入 8 回显 8.01，真实 8.0066 pt）。要消掉它得提高 manifest 的精度。
 *   三处读的是同一个脚本值、同一个换算，所以无论哪种情况三处显示同一个数。
 * * **缩放比算不出来**（`nativeW` 缺失；文档模型里它是必填，迁移时补成摆放宽度，渲染回来
 *   按 manifest 的 `size_mm` 校正）：`scale = 1`，显示的就是脚本值。**不另外标「原始值」**：
 *   预检在同一种情况下也按 1 量（`panelScale`），问题面板说的正是这个数——属性页单独标一句
 *   「这是原始值」，反而是在宣称一个产品其余部分都不承认的差别。
 * * 表外的属性、非数值原样进出，返回**同一个对象**（memo 不白白失效）。
 */
export interface PagePtLens {
  /** 面板在页面上的缩放比；算不出来时为 1（与预检同一个兜底） */
  scale: number
  toPage: (prop: string, value: unknown) => unknown
  toScript: (prop: string, value: unknown) => unknown
  field: <F extends EditableField | undefined>(field: F) => F
  /** 一个写死在控件上的**脚本坐标系**的界 → 页面上的界（表外原样；只乘不取整） */
  bound: (prop: string, value: number) => number
}

export function pagePtLens(panel: PanelObject): PagePtLens {
  const raw = panelScale(panel)
  const scale = Number.isFinite(raw) && raw > 0 ? raw : 1
  return {
    scale,
    toPage: (prop, value) => toPageValue(prop, value, scale),
    toScript: (prop, value) => toScriptValue(prop, value, scale),
    field: (f) => pageField(f, scale),
    bound: (prop, value) => pageBound(prop, value, scale),
  }
}

/**
 * 脚本坐标系的上下界 → 页面上的界。**只乘不取整**：界是引擎接受什么的事实，钳到页面上的
 * `min × scale` 再换回去正好是 `min`；取整反而可能让换回去的值落到界外一点点。
 */
function pageBound(prop: string, value: number, scale: number): number {
  if (!PAGE_PT_PROPS.has(prop) || !Number.isFinite(scale) || scale <= 0 || scale === 1) return value
  return value * scale
}

/** 什么都不换算的透镜：缩放比 1（缩放比为 1 时 `pagePtLens` 本来就原样进出） */
const IDENTITY_LENS: PagePtLens = {
  scale: 1,
  toPage: (_prop, value) => value,
  toScript: (_prop, value) => value,
  field: (f) => f,
  bound: (_prop, value) => value,
}

/**
 * 应用一份样式到一张面板时用的透镜：样式的数字按 `pt_basis` 读——`'page'` 过这张面板的
 * `pagePtLens`，缺席（旧版存下的样式，数字就是当年的脚本值）原样写。换算仍然只有
 * `pagePtLens` 这一份；这里只决定「这份数字要不要过它」。
 */
export function styleLens(style: Pick<StyleProfileData, 'pt_basis'>, panel: PanelObject): PagePtLens {
  return style.pt_basis === 'page' ? pagePtLens(panel) : IDENTITY_LENS
}

/** manifest 字段 → 页面上的字段（当前值两位小数；上下界见 `pageBound`） */
export function pageField<F extends EditableField | undefined>(field: F, scale: number): F {
  if (!field || !PAGE_PT_PROPS.has(field.prop) || field.type !== 'number') return field
  if (!Number.isFinite(scale) || scale <= 0 || scale === 1) return field
  return {
    ...field,
    value: toPageValue(field.prop, field.value, scale),
    ...(field.min != null ? { min: pageBound(field.prop, field.min, scale) } : {}),
    ...(field.max != null ? { max: pageBound(field.prop, field.max, scale) } : {}),
  }
}

/** 信封 → 编辑草稿。内容里的未知字段（`extra`）原样带着走。 */
export function profileToDraft(record: {
  id: string
  display_name: string
  data: Record<string, unknown>
}): StylePreset {
  const d = record.data as unknown as StyleProfileData
  return {
    id: record.id,
    name: record.display_name,
    element: (d.element ?? {}) as StyleProfileData['element'],
    ...(d.palette ? { palette: d.palette } : {}),
    ...(d.annotation ? { annotation: d.annotation } : {}),
    ...(d.subLabel ? { subLabel: d.subLabel } : {}),
    ...(d.page ? { page: d.page } : {}),
    ...(d.background ? { background: d.background } : {}),
    ...(d.derived_from_spec ? { derived_from_spec: d.derived_from_spec } : {}),
    ...(d.pt_basis === 'page' ? { pt_basis: 'page' as const } : {}),
    ...(d.extra ? { extra: d.extra } : {}),
  }
}

/** 编辑草稿 → 信封里的 `data`（把 id / name 摘掉，别的原样）。 */
export function draftToData(preset: StylePreset): Record<string, unknown> {
  const { id: _id, name: _name, ...data } = preset
  // 编辑器里存下的数字一律按页面 pt 记（旧样式在这里第一次被编辑时升级，见 `withPageBasis`）
  return withPageBasis(data as StyleProfileData) as unknown as Record<string, unknown>
}

/** 这份样式是旧版存的吗（数字是脚本值，没有 `pt_basis`） */
export const isLegacyBasis = (data: { pt_basis?: unknown } | null | undefined): boolean =>
  !!data && data.pt_basis !== 'page'

/**
 * 旧样式**第一次被编辑**时升级成按页面 pt 记（2026-09-24 拍板）：已有的数字原样保留。
 *
 * 这等于把旧数字按缩放比 1 解读——旧样式是按原生尺寸从图里提取的，原尺寸摆放的图上页面值
 * 就等于脚本值，那些图套用前后一个字节都不变；缩放过的图从此按页面 pt 对齐（读者量到的
 * 就是样式里写的数）。不升级的话，面板交出的页面值会被当成脚本值写进去：缩放比 0.6 的图
 * 上输入 9，脚本字号变成 9，读者量到 5.4。所有编辑入口（样式面板 / 设置 / 样式对话框）
 * 都经过这里。
 */
export function withPageBasis<T extends { pt_basis?: 'page' }>(data: T): T {
  return data.pt_basis === 'page' ? data : { ...data, pt_basis: 'page' }
}

/** 样式表里角色的显示名；未登记的角色原样显示 */
export const styleRoleLabel = (role: string): string =>
  t(`style.roleLabel.${role}`, { ns: 'dialogs', defaultValue: role })

/** 参与配色循环的系列角色 → 承接颜色的 prop */
const PALETTE_PROP: Record<string, string> = {
  line: 'color',
  scatter: 'facecolor',
  bar_series: 'facecolor',
}

const SUB_LABEL_RE = /^\([a-z]\)$/i

export const isSubLabel = (t: TextObject) => SUB_LABEL_RE.test(t.text.trim())

/* ------------------------------- 提取 ------------------------------------- */

/**
 * 从一个已渲染面板提取样式：每个角色取第一个元素的当前值。
 * 只提取白名单里的 prop，且元素确实暴露了该字段才提取。
 *
 * `scale` 是这张面板在页面上的缩放比（`preflight.panelScale`）：提取出来的字号 /
 * 线宽是**页面上**的 pt，与样式里数字的语义一致（见文件头）。
 */
export function extractFromManifest(manifest: Manifest, scale = 1): StylePreset['element'] {
  const out: StylePreset['element'] = {}
  for (const [role, props] of Object.entries(STYLE_ROLE_PROPS)) {
    const el = manifest.elements.find((e) => e.role === role && e.editable.length > 0)
    if (!el) continue
    const entry: Record<string, unknown> = {}
    for (const prop of props) {
      const f = el.editable.find((x) => x.prop === prop)
      if (f && f.value !== null && f.value !== undefined) entry[prop] = toPageValue(prop, f.value, scale)
    }
    if (Object.keys(entry).length) out[role] = entry
  }
  return out
}

/** 从面板提取系列配色（按 gid 顺序） */
export function extractPalette(manifest: Manifest): string[] {
  const colors: string[] = []
  for (const el of manifest.elements) {
    const prop = PALETTE_PROP[el.role]
    if (!prop) continue
    const f = el.editable.find((x) => x.prop === (el.role === 'line' ? 'color' : 'facecolor'))
    // 引擎报 `none` 的是「没有颜色」（#427），不是一种配色
    if (typeof f?.value === 'string' && f.value !== 'none' && !colors.includes(f.value)) {
      colors.push(f.value)
    }
  }
  return colors.slice(0, 8)
}

/* ------------------------------- 应用 ------------------------------------- */

export interface PanelPlan {
  panel: PanelObject
  patches: PanelOverride[]
  /** 将覆盖的现有 override 数（冲突提示） */
  overwrites: number
  /** 无法映射的项：元素没有该字段（如 3D 刻度无 direction） */
  unmappable: string[]
}

export interface StylePlan {
  panels: PanelPlan[]
  /** 有脚本但还没渲染过，取不到 manifest，无法映射 */
  unrendered: PanelObject[]
  /** 受影响的标注文字对象 id */
  annotationIds: string[]
  subLabelIds: string[]
  page?: { w: number; h: number }
  /** 画布背景（样式里设了才有；`undefined` = 不动背景，与"设成白色"不是一回事） */
  background?: string
}

/** 把预设映射成每个面板的 override 批次（不执行，仅供预览与应用） */
export function planStyle(
  preset: StylePreset,
  panels: PanelObject[],
  manifestOf: (panel: PanelObject) => Manifest | null | undefined,
  doc: FigureDocument,
  includeAnnotations: boolean,
): StylePlan {
  const plans: PanelPlan[] = []
  const unrendered: PanelObject[] = []

  for (const panel of panels) {
    const manifest = manifestOf(panel)
    if (!manifest) {
      unrendered.push(panel)
      continue
    }
    const patches: PanelOverride[] = []
    const unmappable: string[] = []
    // 样式里的 pt 是页面上的 pt：按这张面板的缩放比换回脚本坐标系再写（与属性页同一个透镜）。
    // 不带 `pt_basis` 的旧样式存的是脚本值，原样写
    const lens = styleLens(preset, panel)
    for (const [role, props] of Object.entries(preset.element)) {
      const els = manifest.elements.filter((e) => e.role === role)
      for (const el of els) {
        for (const [prop, value] of Object.entries(props)) {
          const field = el.editable.find((f) => f.prop === prop)
          if (!field) {
            unmappable.push(
              t('style.unmappableEntry', { ns: 'dialogs', label: el.label, prop }),
            )
            continue
          }
          patches.push({ gid: el.gid, prop, value: lens.toScript(prop, value) })
        }
      }
    }
    // 图例标题没有字体的 override 入口（引擎只有 `title_fontsize`）：样式统一了图例项
    // 的字体时，有标题的图例如实记一条「这里没改到」，别让用户以为整块图例都换了
    const legendFamily = preset.element.legend_text?.fontfamily
    if (legendFamily !== undefined) {
      for (const el of manifest.elements) {
        if (el.role !== 'legend' || !legendTitle(el)) continue
        unmappable.push(
          t('style.unmappableEntry', {
            ns: 'dialogs',
            label: t('style.legendTitle', { ns: 'dialogs' }),
            prop: 'fontfamily',
          }),
        )
      }
    }
    if (preset.palette?.length) {
      let i = 0
      for (const el of manifest.elements) {
        const prop = PALETTE_PROP[el.role]
        if (!prop) continue
        if (!el.editable.some((f) => f.prop === prop)) continue
        patches.push({ gid: el.gid, prop, value: preset.palette[i % preset.palette.length] })
        i += 1
      }
    }
    // 比生效的那条（重复 (gid, prop) 时是最后一条，与写入同一判据）
    const overwrites = patches.filter((p) => {
      const cur = effectiveOverride(panel.overrides, p.gid, p.prop)
      return cur !== undefined && JSON.stringify(cur.value) !== JSON.stringify(p.value)
    }).length
    plans.push({ panel, patches, overwrites, unmappable })
  }

  const annotationIds: string[] = []
  const subLabelIds: string[] = []
  if (includeAnnotations) {
    for (const o of doc.objects) {
      if (o.type !== 'text') continue
      if (isSubLabel(o)) {
        if (preset.subLabel) subLabelIds.push(o.id)
      } else if (preset.annotation) {
        annotationIds.push(o.id)
      }
    }
  }

  return {
    panels: plans,
    unrendered,
    annotationIds,
    subLabelIds,
    page: preset.page,
    background: preset.background,
  }
}

/** 图例有没有标题（manifest 的 `title` 字段非空） */
const legendTitle = (el: ManifestElement): boolean => {
  const v = el.editable.find((f) => f.prop === 'title')?.value
  return typeof v === 'string' && v.trim() !== ''
}

/**
 * 一张图上「样式管得到」的那些 override：role × prop 落在 `STYLE_ROLE_PROPS` 里，
 * 或者是配色循环承接颜色的那一格（`PALETTE_PROP`），**并且此刻 manifest 上这个元素确实暴露这条属性**。
 * 「恢复原样」清的正是这批——与 `planStyle` 能写的范围同一张表、同一个可编辑判据，不另立一份。
 *
 * 只看角色白名单的话，重跑后元素还在、却不再暴露某条属性（刻度变成 3D、没有 `direction`）时，
 * 用户手改的那条 override 成了孤儿也会被挑中删掉，脚本日后再暴露它时用户的值没了（Codex #547 P1）。
 * 样式写的孤儿由 `style.owned` 那一支负责清（`styleBinding.restoreCanvasStyle`）。
 */
export function styleOverrideTargets(
  panel: PanelObject,
  manifest: Manifest,
): { gid: string; prop: string }[] {
  const byGid = new Map(manifest.elements.map((e) => [e.gid, e]))
  return panel.overrides
    .filter((o) => {
      const el = byGid.get(o.gid)
      if (!el || !el.editable.some((f) => f.prop === o.prop)) return false
      return !!STYLE_ROLE_PROPS[el.role]?.includes(o.prop) || PALETTE_PROP[el.role] === o.prop
    })
    .map((o) => ({ gid: o.gid, prop: o.prop }))
}

/** 预设内容的一行行摘要（编辑器里展示 / 删除用） */
export interface PresetEntry {
  role: string
  prop: string
  value: unknown
}

export function presetEntries(preset: StylePreset): PresetEntry[] {
  const out: PresetEntry[] = []
  for (const [role, props] of Object.entries(preset.element)) {
    for (const [prop, value] of Object.entries(props)) out.push({ role, prop, value })
  }
  return out
}

/**
 * 样式条目在界面上按**对象类别**分组：文字 / 曲线与系列 / 坐标轴 / 图例（2026-09-13
 * 审计 B25：后端的角色 × 属性平铺成一张表，「轴标题 / 字号 / 字体」反复出现，
 * 读不出结构）。分组只影响排版，写进磁盘的内容一个字不变；没登记的角色归到「其他」。
 */
export type StyleGroup = 'text' | 'series' | 'axes' | 'legend' | 'other'
const STYLE_GROUP_OF: Record<string, StyleGroup> = {
  text: 'text',
  title: 'text',
  axis_label: 'text',
  line: 'series',
  errorbar: 'series',
  bar_series: 'series',
  axes: 'axes',
  ticks: 'axes',
  colorbar: 'axes',
  legend: 'legend',
  legend_text: 'legend',
}
export const STYLE_GROUP_ORDER: readonly StyleGroup[] = ['text', 'series', 'axes', 'legend', 'other']
export const styleGroupOf = (role: string): StyleGroup => STYLE_GROUP_OF[role] ?? 'other'
export const styleGroupLabel = (group: StyleGroup): string =>
  t(`style.group.${group}`, { ns: 'dialogs' })

/** 条目按组归并，组按 `STYLE_GROUP_ORDER`，组内保持条目原有顺序；空组不出现 */
export function groupedEntries(preset: StylePreset): { group: StyleGroup; entries: PresetEntry[] }[] {
  const entries = presetEntries(preset)
  return STYLE_GROUP_ORDER.map((group) => ({
    group,
    entries: entries.filter((en) => styleGroupOf(en.role) === group),
  })).filter((g) => g.entries.length > 0)
}

/* ------------------------------ 跟随（ADR 0081） ------------------------------ */

/** 内容比较（键序无关）：与规范绑定判「有没有新版」同一把尺子 */
const sameValue = (a: unknown, b: unknown): boolean => sameRules(a ?? null, b ?? null)

/**
 * 两份样式内容之间**变了的那部分**，拼成一份只含变化的预设。
 *
 * 画布跟随样式时，库里那一条改了一个字号，画布上要动的就只有那一个字号——整份重新
 * 套一遍的话，用户在属性页里对某一张图刻意改过的别的项（字体、颜色）会被顺手冲掉。
 * 从样式里**删掉**的条目不在结果里：样式不再管它，图就保持现在的样子。
 * `prev` 为 null（刚绑定）时就是整份。
 */
export function presetDelta(prev: StyleProfileData | null, next: StyleProfileData): StylePreset {
  const out: StylePreset = { name: '', element: {} }
  for (const [role, props] of Object.entries(next.element ?? {})) {
    for (const [prop, value] of Object.entries(props)) {
      if (prev && sameValue(prev.element?.[role]?.[prop], value)) continue
      ;(out.element[role] ??= {})[prop] = value
    }
  }
  if (next.palette?.length && (!prev || !sameValue(prev.palette, next.palette))) out.palette = next.palette
  // 标注 / 序号标签**逐个属性**比：只改了字号时不该把颜色、字体一起重新套一遍
  // （用户在绑定之后对某一条标注手改的颜色会被冲掉）
  const textDelta = (a: StyleTextEntry | undefined, b: StyleTextEntry | undefined) => {
    if (!b) return undefined
    const d: StyleTextEntry = {}
    for (const [k, v] of Object.entries(b) as [keyof StyleTextEntry, unknown][]) {
      if (prev && sameValue(a?.[k], v)) continue
      ;(d as Record<string, unknown>)[k] = v
    }
    return Object.keys(d).length ? d : undefined
  }
  const annotation = textDelta(prev?.annotation, next.annotation)
  if (annotation) out.annotation = annotation
  const subLabel = textDelta(prev?.subLabel, next.subLabel)
  if (subLabel) out.subLabel = subLabel
  if (next.page && (!prev || !sameValue(prev.page, next.page))) out.page = next.page
  if (next.background && (!prev || prev.background !== next.background)) out.background = next.background
  if (next.pt_basis === 'page') out.pt_basis = 'page'
  return out
}

/** 读数一样吗：数值按 manifest 回报的两位小数比（写进去的也是两位），其余按内容 */
export const sameReading = (a: unknown, b: unknown): boolean =>
  typeof a === 'number' && typeof b === 'number' ? Math.abs(a - b) < 0.005 : sameValue(a, b)

/** 画布文字此刻的样子是不是已经等于这一项样式 */
function textMatches(o: TextObject, st: StyleTextEntry): boolean {
  if (st.sizePt != null && !sameReading(o.sizePt, st.sizePt)) return false
  if (st.bold != null && !!o.bold !== st.bold) return false
  if (st.italic != null && !!o.italic !== st.italic) return false
  if (st.color != null && (o.color ?? '').toLowerCase() !== st.color.toLowerCase()) return false
  if (st.fontFamily != null && effectiveCanvasFamily(o) !== st.fontFamily) return false
  return true
}

/**
 * 只留**会真的改变什么**的那部分：值与此刻一样的 patch、已经是这个样子的标注、
 * 已经是这个尺寸 / 底色的页面，全部去掉。
 *
 * 「此刻」= 这个 (gid, prop) 上的 override，没有 override 就是 manifest 报的值——
 * 与属性页读值同一个口径（`textStyleModel.currentOf`）。结果为空（`isEmptyPlan`）
 * 就**不写**：打开文档、切画布、渲染回来这些时刻，已经合样式的图不该产生任何一条历史。
 */
export function effectiveChanges(
  plan: StylePlan,
  doc: FigureDocument,
  manifestOf: (panel: PanelObject) => Manifest | null | undefined,
  preset: StylePreset,
): StylePlan {
  const panels = plan.panels
    .map((pp) => {
      const manifest = manifestOf(pp.panel)
      const patches = pp.patches.filter((p) => {
        // 生效的那条（重复条目 last-wins，#587）：读第一条会把「过期的同值」当成已合样式（Codex #547 P1）
        const ov = effectiveOverride(pp.panel.overrides, p.gid, p.prop)
        const now = ov
          ? ov.value
          : manifest?.elements.find((e) => e.gid === p.gid)?.editable.find((f) => f.prop === p.prop)?.value
        return !sameReading(now, p.value)
      })
      return { ...pp, patches }
    })
    .filter((pp) => pp.patches.length > 0)
  const text = (id: string) => doc.objects.find((o): o is TextObject => o.id === id && o.type === 'text')
  const annotationIds = plan.annotationIds.filter((id) => {
    const o = text(id)
    return !!o && !!preset.annotation && !textMatches(o, preset.annotation)
  })
  const subLabelIds = plan.subLabelIds.filter((id) => {
    const o = text(id)
    return !!o && !!preset.subLabel && !textMatches(o, preset.subLabel)
  })
  const page =
    plan.page && (Math.abs(plan.page.w - doc.page.w) > 1e-6 || Math.abs(plan.page.h - doc.page.h) > 1e-6)
      ? plan.page
      : undefined
  const background =
    plan.background && (doc.page.bg ?? '').toLowerCase() !== plan.background.toLowerCase()
      ? plan.background
      : undefined
  return { ...plan, panels, annotationIds, subLabelIds, page, background }
}

/** 这份计划写下去什么都不会变 */
export const isEmptyPlan = (plan: StylePlan): boolean =>
  !plan.panels.some((p) => p.patches.length) &&
  !plan.annotationIds.length &&
  !plan.subLabelIds.length &&
  !plan.page &&
  !plan.background
