/**
 * 属性能力层：**文字排版**（ADR 0032）。
 *
 * 在这之前，「一段文字长什么样」在产品里有两套互不认识的表达：
 *
 * ```text
 * 图内文字（matplotlib Text）   fontsize / weight:'bold' / style:'italic' / ha / fontfamily
 * 画布文字（TextObject）        sizePt   / bold:true      / italic:true    / align / （没有）
 * ```
 *
 * 两套的后果不是「代码重复」，是**用户说不出一句话**：把标题和压在它上面的
 * 标注一起选中，界面没有一个控件能同时描述它们；「都设成 9pt 的加粗衬线」
 * 要在两个面板里各做一遍，而其中一个面板根本没有字体这一行。
 *
 * 本模块是那句话的词汇表：**一套规范属性名 + 一套取值语义 + 一张能力表**。
 * UI 只认这里的名字，写入只经各自的 writer——两件事分开，界面语言统一，
 * 数据通道不混。
 *
 * ### 三条纪律
 *
 * 1. **同一属性同一取值空间。** `weight` 两边都是 `'normal' | 'bold'`；
 *    画布文字磁盘上仍是 `bold: boolean`，换算只在 `readCanvasText` /
 *    `writeCanvasText` 这一对里发生，别处不许再写第二次 `? 'bold' : 'normal'`。
 * 2. **「不支持」「没设过」「多个值」是三个不同的答案**，不许压成一个默认值
 *    （`TypographyValue` 的四档）。字号 mixed 画成 9pt、字体 inherit 画成
 *    `serif`，都会让用户一敲回车就把他没看见的差异抹平。
 * 3. **property path 只有这一份。** 检查（`preflight`）报的字段名、问题面板
 *    定位用的 `data-prop`、控件挂的锚点，全部从 `propertyPathOf()` 出——
 *    三处各写各的字符串时，缺的那一处的表现是「点了定位，什么都没发生」。
 */

import type { EditableField } from './api'
import type { TextObject } from '@/types/document'

/* ----------------------------- 规范属性名 --------------------------------- */

/**
 * 排版属性的**闭集**。加一条要同时回答三个问题：两类对象各支不支持、
 * property path 叫什么、取值怎么校验——三张表都在本文件里，缺一处不编译。
 */
export const TYPOGRAPHY_PROPS = [
  'fontFamily',
  'sizePt',
  'weight',
  'style',
  'color',
  'halign',
  'valign',
  'lineHeight',
  'rotationDeg',
] as const

export type TypographyProp = (typeof TYPOGRAPHY_PROPS)[number]

/**
 * 对象类别。**只有两类**，因为 property path 与存储形态只有两套：
 * 图内文字（override 进 `panel.overrides`，字段由 manifest 说了算）与
 * 画布文字（直接是 `doc.objects` 里的 `TextObject`）。
 *
 * 标题 / 轴标题 / 刻度文字 / 图例文字都是 `figureText`——它们在 matplotlib
 * 里就是同一种 `Text`，差别在**引擎发不发那条字段**，不在类别。
 */
export type TypographyKind = 'figureText' | 'canvasText'

/* ------------------------------ 取值语义 ---------------------------------- */

export const WEIGHTS = ['normal', 'bold'] as const
export const STYLES = ['normal', 'italic'] as const
export const HALIGNS = ['left', 'center', 'right'] as const

export type FontWeight = (typeof WEIGHTS)[number]
export type FontStyle = (typeof STYLES)[number]
export type HAlign = (typeof HALIGNS)[number]

/**
 * 画布文字能选的字体族。**闭集，且只有三个通用族。**
 *
 * 理由是能力，不是偷懒：合成与写回跑在 Flask 进程里，那里**没有
 * matplotlib**（见 `src/tavotto/AGENTS.md` 的进程边界），画字只能用
 * PyMuPDF 自带的 base-14——它恰好就是这三个族。把「Times New Roman」摆进
 * 这个下拉，得到的会是「界面上选得中、导出时悄悄换一个」，那正是本轮要
 * 消灭的那类静默替换。
 *
 * 严格同源：`src/tavotto/pdfbackend/__init__.py` 的 `CANVAS_TEXT_FAMILIES`
 * （看护 `tests/test_typography_families.py`）。
 */
export const CANVAS_TEXT_FAMILIES = ['serif', 'sans-serif', 'monospace'] as const
export type CanvasTextFamily = (typeof CANVAS_TEXT_FAMILIES)[number]

/**
 * 画布文字没设过字体时**生效**的族。
 *
 * 它是「继承」的落点，不是一个被写进文档的值：`TextObject.fontFamily` 保持
 * `undefined`，老文档一个字节不变，导出载荷也不多一个字段。检查与渲染问
 * 「实际画成什么」时才用它。
 */
export const CANVAS_TEXT_DEFAULT_FAMILY: CanvasTextFamily = 'serif'

export const isCanvasTextFamily = (v: unknown): v is CanvasTextFamily =>
  typeof v === 'string' && (CANVAS_TEXT_FAMILIES as readonly string[]).includes(v)

/** 画布文字**实际**画成哪个族（`undefined` = 继承默认）。 */
export const effectiveCanvasFamily = (o: Pick<TextObject, 'fontFamily'>): CanvasTextFamily =>
  isCanvasTextFamily(o.fontFamily) ? o.fontFamily : CANVAS_TEXT_DEFAULT_FAMILY

/**
 * 画布文字的 CSS 字体栈。
 *
 * 衬线那一档就是历史上的 `--font-doc`：**默认族的画面一个像素不许变**，
 * 否则「加了个字体下拉」会把每一份老文档的排版都改一遍。
 */
export const canvasFontStack = (family: CanvasTextFamily): string => {
  switch (family) {
    case 'sans-serif':
      return 'Helvetica, Arial, "PingFang SC", "Microsoft YaHei", sans-serif'
    case 'monospace':
      return 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'
    default:
      return 'var(--font-doc)'
  }
}

/* ------------------------------ 能力表 ------------------------------------ */

/**
 * 一条属性在某类对象上**结构上**存不存在。
 *
 * 对 `figureText` 这只是「值不值得问」——真正能不能改由 manifest 的
 * `editable` 说了算（脚本画的图例可能只发 `fontsize`）。对 `canvasText`
 * 它就是最终答案：`TextObject` 的字段是我们自己定的。
 */
const SUPPORT: Record<TypographyKind, ReadonlySet<TypographyProp>> = {
  figureText: new Set<TypographyProp>([
    'fontFamily',
    'sizePt',
    'weight',
    'style',
    'color',
    'halign',
    'valign',
    'rotationDeg',
  ]),
  canvasText: new Set<TypographyProp>([
    'fontFamily',
    'sizePt',
    'weight',
    'style',
    'color',
    'halign',
    'lineHeight',
    'rotationDeg',
  ]),
}

/** 不支持的**成因**（闭集）——界面按 code 说人话，不拼自由文本。 */
export type UnsupportedReason =
  /** 这类对象结构上就没有这条属性（画布文字没有垂直对齐、图内文字没有行距） */
  | 'kind_unsupported'
  /** 引擎这次没给这条字段（脚本画的图例只发了 fontsize） */
  | 'not_in_manifest'
  /** 选中的对象横跨两类，这条属性不是两类都有 */
  | 'mixed_kinds'

export const supportsTypography = (kind: TypographyKind, prop: TypographyProp): boolean =>
  SUPPORT[kind].has(prop)

/**
 * 一组目标（可能横跨两类）共同支持的属性。
 * 交集为空的那条不是「关掉」，是`mixed_kinds`——说得出为什么。
 */
export function commonSupport(kinds: readonly TypographyKind[]): Set<TypographyProp> {
  const out = new Set<TypographyProp>()
  if (!kinds.length) return out
  for (const prop of TYPOGRAPHY_PROPS) {
    if (kinds.every((k) => SUPPORT[k].has(prop))) out.add(prop)
  }
  return out
}

/* --------------------------- property path -------------------------------- */

/**
 * 规范属性名 → 各自存储里的字段名。**检查、定位、控件锚点共用这一份。**
 *
 * 图内那一列就是 manifest 的 prop 名（`preflight` 报的也是它）；画布那一列
 * 是 `TextObject` 的字段名（`sizePt` 已经是 Session 11 在用的那个）。这里
 * **刻意不统一成第三套名字**：统一名字要动跨语言的检查合同，而真正缺的
 * 从来不是名字一致，是「三处读同一张表」。
 */
const PATHS: Record<TypographyKind, Partial<Record<TypographyProp, string>>> = {
  figureText: {
    fontFamily: 'fontfamily',
    sizePt: 'fontsize',
    weight: 'weight',
    style: 'style',
    color: 'color',
    halign: 'ha',
    valign: 'va',
    rotationDeg: 'rotation',
  },
  canvasText: {
    fontFamily: 'fontFamily',
    sizePt: 'sizePt',
    weight: 'bold',
    style: 'italic',
    color: 'color',
    halign: 'align',
    lineHeight: 'lineHeight',
    rotationDeg: 'rotationDeg',
  },
}

export function propertyPathOf(kind: TypographyKind, prop: TypographyProp): string | null {
  return PATHS[kind][prop] ?? null
}

/** 反查：`preflight` 报来的字段名 → 规范属性名（定位服务用）。 */
export function propOfPath(kind: TypographyKind, path: string): TypographyProp | null {
  for (const prop of TYPOGRAPHY_PROPS) {
    if (PATHS[kind][prop] === path) return prop
  }
  return null
}

/**
 * 排版能产生的 property path 的**全集**。
 *
 * 看护用：控件必须为每一条支持的属性挂上锚点，否则「从问题面板定位到字段」
 * 会安静地什么都不做（本轮修掉的就是这个——工具条把 6 条属性从平铺列表里
 * 拿走了，却没有把锚点一起带过来）。
 */
export const TYPOGRAPHY_PROPERTY_PATHS: ReadonlySet<string> = new Set(
  (Object.keys(PATHS) as TypographyKind[]).flatMap((k) =>
    TYPOGRAPHY_PROPS.map((p) => PATHS[k][p]).filter((v): v is string => !!v),
  ),
)

/* --------------------------- 值：四档，不压扁 ------------------------------ */

export type TypographyValue<T = unknown> =
  /** 所有目标一致 */
  | { kind: 'uniform'; value: T }
  /** 目标之间不一致——**绝不拿第一个冒充全部** */
  | { kind: 'mixed' }
  /** 支持，但谁都没设过（画布文字的 `fontFamily`、`lineHeight`） */
  | { kind: 'inherit'; value: T }
  /** 至少一个目标不支持：控件不画，或者画成 disabled 并说得出 reason */
  | { kind: 'unsupported'; reason: UnsupportedReason }

/** 「这个控件有没有一个可显示的值」——`inherit` 有（继承来的那个）。 */
export const displayValueOf = <T>(v: TypographyValue<T>): T | undefined =>
  v.kind === 'uniform' || v.kind === 'inherit' ? v.value : undefined

/* ------------------------------ 校验与规整 -------------------------------- */

/** 校验失败的成因（闭集）。invalid 的输入**不进历史**，也不写文档。 */
export type CoerceFailure = 'not_a_number' | 'out_of_range' | 'not_an_option' | 'not_a_color'

export type Coerced<T = unknown> = { ok: true; value: T } | { ok: false; reason: CoerceFailure }

/** 数值属性的兜底区间（引擎没给 min/max 时用）。 */
const NUMBER_RANGE: Partial<Record<TypographyProp, { min: number; max: number }>> = {
  sizePt: { min: 1, max: 400 },
  lineHeight: { min: 0.5, max: 5 },
  rotationDeg: { min: -360, max: 360 },
}

const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i

/**
 * 规整一个待写入的值。**写入前必过这一关**：越界的字号、下拉里没有的族、
 * 拼错的颜色都在这里被挡住，绝不进事务、绝不进历史。
 *
 * `field` 是这一次的实际值域（图内文字来自 manifest，画布文字来自本表）；
 * 数值区间取它与兜底区间的**更紧者**——宽的那个会让某个目标收到越界值。
 */
export function coerceTypography(
  prop: TypographyProp,
  raw: unknown,
  field?: Pick<EditableField, 'min' | 'max' | 'options'>,
): Coerced {
  switch (prop) {
    case 'sizePt':
    case 'lineHeight':
    case 'rotationDeg': {
      const n = typeof raw === 'number' ? raw : Number(raw)
      if (!Number.isFinite(n)) return { ok: false, reason: 'not_a_number' }
      const base = NUMBER_RANGE[prop]!
      const min = Math.max(base.min, field?.min ?? -Infinity)
      const max = Math.min(base.max, field?.max ?? Infinity)
      if (n < min || n > max) return { ok: false, reason: 'out_of_range' }
      return { ok: true, value: n }
    }
    case 'color': {
      if (typeof raw !== 'string' || !HEX.test(raw)) return { ok: false, reason: 'not_a_color' }
      return { ok: true, value: raw }
    }
    default: {
      // enum：选项表由调用方给（族的选项因运行时而异），没给就用规范里的默认集
      const options =
        field?.options ??
        (prop === 'weight'
          ? (WEIGHTS as readonly string[])
          : prop === 'style'
            ? (STYLES as readonly string[])
            : prop === 'halign' || prop === 'valign'
              ? (HALIGNS as readonly string[])
              : (CANVAS_TEXT_FAMILIES as readonly string[]))
      if (typeof raw !== 'string' || !options.includes(raw)) {
        return { ok: false, reason: 'not_an_option' }
      }
      return { ok: true, value: raw }
    }
  }
}

/* --------------------- 画布文字：存储 ↔ 规范值 的唯一换算 -------------------- */

/**
 * 读一条规范属性。`undefined` = **没设过**（`inherit`），不是 0 也不是空串。
 *
 * `bold: false` 与「没设过」在磁盘上分不开（它是必填字段），所以字重永远
 * 回 `'normal' | 'bold'`，不回 undefined；`italic` / `lineHeight` /
 * `fontFamily` / `rotationDeg` 是可选字段，没设过就如实回 undefined。
 */
export function readCanvasText(o: TextObject, prop: TypographyProp): unknown {
  switch (prop) {
    case 'fontFamily':
      return o.fontFamily
    case 'sizePt':
      return o.sizePt
    case 'weight':
      return o.bold ? 'bold' : 'normal'
    case 'style':
      return o.italic === true ? 'italic' : o.italic === false ? 'normal' : undefined
    case 'color':
      return o.color
    case 'halign':
      return o.align
    case 'lineHeight':
      return o.lineHeight
    case 'rotationDeg':
      return o.rotationDeg
    default:
      return undefined
  }
}

/** 没设过时**生效**的那个值（`inherit` 档要显示的东西）。 */
export function inheritedCanvasValue(prop: TypographyProp): unknown {
  switch (prop) {
    case 'fontFamily':
      return CANVAS_TEXT_DEFAULT_FAMILY
    case 'style':
      return 'normal'
    case 'lineHeight':
      return 1.25
    case 'rotationDeg':
      return 0
    default:
      return undefined
  }
}

/**
 * 写一条规范属性（在 draft 上原地改，调用方负责事务）。
 *
 * **回到默认值就删字段**，不写一个等于默认值的显式值：留着的话导出载荷会
 * 多一个字段、老后端拿到的字节就不一样了，而语义完全相同。
 */
export function writeCanvasText(o: TextObject, prop: TypographyProp, value: unknown): void {
  switch (prop) {
    case 'fontFamily':
      if (isCanvasTextFamily(value) && value !== CANVAS_TEXT_DEFAULT_FAMILY) o.fontFamily = value
      else delete o.fontFamily
      return
    case 'sizePt':
      o.sizePt = Number(value)
      return
    case 'weight':
      o.bold = value === 'bold'
      return
    case 'style':
      if (value === 'italic') o.italic = true
      else delete o.italic
      return
    case 'color':
      o.color = String(value)
      return
    case 'halign':
      o.align = value as HAlign
      return
    case 'lineHeight':
      if (Math.abs(Number(value) - 1.25) < 0.001) delete o.lineHeight
      else o.lineHeight = Number(value)
      return
    case 'rotationDeg':
      if (!Number(value)) delete o.rotationDeg
      else o.rotationDeg = Number(value)
      return
    default:
      return
  }
}

/** 画布文字的字段值域（图内那一侧由 manifest 给，这一侧由我们自己定）。 */
export function canvasFieldOf(prop: TypographyProp): EditableField | undefined {
  switch (prop) {
    case 'fontFamily':
      return { prop: 'fontFamily', type: 'enum', value: null, options: [...CANVAS_TEXT_FAMILIES] }
    case 'sizePt':
      return { prop: 'sizePt', type: 'number', value: null, min: 3, max: 96, step: 0.5, unit: 'pt' }
    case 'weight':
      return { prop: 'weight', type: 'enum', value: null, options: [...WEIGHTS] }
    case 'style':
      return { prop: 'style', type: 'enum', value: null, options: [...STYLES] }
    case 'color':
      return { prop: 'color', type: 'color', value: null }
    case 'halign':
      return { prop: 'align', type: 'enum', value: null, options: [...HALIGNS] }
    case 'lineHeight':
      return { prop: 'lineHeight', type: 'number', value: null, min: 0.8, max: 3, step: 0.05 }
    case 'rotationDeg':
      return { prop: 'rotationDeg', type: 'number', value: null, min: -180, max: 180, step: 5, unit: '°' }
    default:
      return undefined
  }
}

/* ---------------------------- 新建时的默认值 ------------------------------- */

/**
 * 新建一段画布文字时的排版默认值。**只有这一处**：`addText` / `addSubLabels`
 * / 粘贴 / Style 应用都从这里取，散在各处写 `sizePt: 10, bold: false` 的话
 * 「新建的标注和刚复制的标注长得不一样」这类问题会一直有人报。
 *
 * `fontFamily` **刻意不在里面**：新建的文字是「没设过字体」，于是它跟着
 * 文档默认族走。给它填一个显式的 `'serif'` 会把继承变成一次显式设置，
 * 以后默认族改了它就不跟了。
 */
export interface CanvasTextDefaults {
  sizePt: number
  weight: FontWeight
  style: FontStyle
  color: string
  halign: HAlign
}

export const canvasTextDefaults = (): CanvasTextDefaults => ({
  sizePt: 10,
  weight: 'normal',
  style: 'normal',
  color: '#000000',
  halign: 'left',
})

/* ------------------------- 科学文本能力（Prompt 14） ------------------------ */

/**
 * 这类对象的「数学 / 科学文本」走哪条管线。**本轮只定义能力、不实现管线**
 * （Prompt 14 接手），但它必须现在就有名字：两类对象的上下标压根不是同一
 * 件事，含糊成一个 `supportsMath: boolean` 会在 14 那边被迫拆开重来。
 *
 *   inline_markup —— 画布文字：`^{…}` / `_{…}` 行内标记，前端与 PDF 后端
 *                    各画一遍（`lib/richText.ts` ↔ `src/tavotto/richtext.py`）
 *   engine_mathtext —— 图内文字：`$…$` 交给 matplotlib 的 mathtext；
 *                    换字体时 `math_fontfamily` 要跟着换
 *                    （`engine/overrides._set_text_fontfamily`）
 */
export type MathTextMode = 'inline_markup' | 'engine_mathtext'

export const mathTextModeOf = (kind: TypographyKind): MathTextMode =>
  kind === 'canvasText' ? 'inline_markup' : 'engine_mathtext'
