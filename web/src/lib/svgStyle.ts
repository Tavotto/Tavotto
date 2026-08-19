/**
 * SVG 局部样式预览的**能力表与适配器**（假实时的第二条腿）。
 *
 * 契约：默认「不支持」。只有在这张表里、且经过真实 matplotlib SVG fixture
 * 验证过的 role+prop 才允许在 DOM 上抢先显示；其余一律原路走后端。
 * 表是白名单不是黑名单——多认一个字段的代价是用户看到一个**假的**预览，
 * 然后被权威 SVG 纠正回去，那比慢半秒糟糕得多。
 *
 * 适配器不是「把整棵子树刷成同一个颜色」。matplotlib 的样式几乎全部落在
 * **后代节点的 inline style** 上，而且同一个 gid 子树里 fill / stroke 的
 * 语义各不相同（箭头杆 `fill: none`、箭头帽 `fill: <色>`）。因此通用规则是：
 *
 *   **只改「本来就声明了该属性、且值不是 none」的那些叶子。**
 *
 * 这条规则同时解决了几个坑：
 *   * `fill: none` 的线不会被 facecolor 误填成实心；
 *   * 散点的 `<defs>` 模板 path 只声明 stroke，fill 落在引用它的 `<use>` 上，
 *     两边各自被正确命中（`<use>` 影子树里被引用元素自带的样式优先，
 *     所以模板 path 的 stroke-width 必须一起改，只改 `<use>` 是不够的）；
 *   * clipPath / 嵌套 `<g>` 这些没有样式声明的节点天然不会被碰到。
 *
 * 文字是唯一的例外：matplotlib 把颜色写在字形组 `<g style="fill: …">` 上，
 * 而**默认黑色时这个属性根本不出现**，所以文字必须允许「新增」而不只是替换。
 *
 * 已验证不可预览、必须退回后端的（fixture 实测，不是推测）：
 *   * `image.alpha` —— 透明度被烤进 PNG 栅格数据，SVG 上没有任何旋钮；
 *   * `errorbar.*` / `bar_series.*` —— 它们是 manifest 的伪元素（SeriesGroup），
 *     成员在 SVG 里拿的是 matplotlib 自动 id，gid 整个不存在 → 查不到节点，
 *     按「查不到就回退」处理（见 findGidNode 的调用方）；
 *   * `patch.facecolor` **在 `fill=False` 的那些形状上** —— 表里有这一行，
 *     但那个 artist 的 SVG 是 `fill: none`，一个叶子都改不到。能力表是按
 *     role+prop 发的，管不了这种「同 role 不同形状」，所以调用方必须再跑一遍
 *     `canStyleEditApply`（与 applyStyleEdit 共用 styleTargets 那一份实现）。
 */

/**
 * 一个可逆的 DOM 样式改动：记的是**整条 style 属性的原文**，不是单个属性值。
 *
 * 为什么不逐条记：CSSOM 会把颜色规范化（`#1f77b4` 读回来是
 * `rgb(31, 119, 180)`，浏览器与 jsdom 一致）。逐条还原写回去的是规范化后的
 * 形式——视觉一样，但 SVG 文本已经不是 matplotlib 给的那份了。整条属性
 * 原样存回去才是**逐字节**还原，实现也更短。
 */
export interface StyleEdit {
  el: SVGElement | HTMLElement
  /** null = 改之前压根没有 style 属性，还原时要整条删掉 */
  prev: string | null
}

export type AdapterKind =
  | 'stroke'
  | 'fill'
  | 'strokeFill'
  | 'strokeWidth'
  | 'opacity'
  | 'textFill'
  | 'textOpacity'
  | 'display'

/**
 * role → prop → 适配器。**新增一行之前必须先有 fixture 测试**，
 * 否则等于把「不确定」当成「支持」。
 */
export const STYLE_ADAPTERS: Record<string, Record<string, AdapterKind>> = {
  line: { color: 'stroke', linewidth: 'strokeWidth', alpha: 'opacity', visible: 'display' },
  // errorbar 的 gid 在 SVG 里不存在（伪元素）：留在表里是因为适配器本身对
  // 「描边类 artist」是对的，真正拦住它的是 gid 查不到 → 回退，不会画错
  errorbar: { color: 'stroke', linewidth: 'strokeWidth', alpha: 'opacity', visible: 'display' },
  arrow_patch: {
    color: 'strokeFill',
    linewidth: 'strokeWidth',
    alpha: 'opacity',
    visible: 'display',
  },
  bar: {
    facecolor: 'fill',
    edgecolor: 'stroke',
    linewidth: 'strokeWidth',
    alpha: 'opacity',
    visible: 'display',
  },
  bar_series: {
    facecolor: 'fill',
    edgecolor: 'stroke',
    linewidth: 'strokeWidth',
    alpha: 'opacity',
    visible: 'display',
  },
  fill: {
    facecolor: 'fill',
    edgecolor: 'stroke',
    linewidth: 'strokeWidth',
    alpha: 'opacity',
    visible: 'display',
  },
  // 脚本 add_patch 的独立形状（`ax.fill()` 的 Polygon、手搓的 PathPatch）。
  // **`fill` 那个开关不在表里**：空心 patch 的 SVG 写的是 `fill: none`，而
  // 通用规则只改「本来就画着的叶子」——把 none 换成颜色是新增语义，只能
  // 让 matplotlib 自己重画。少认它一条，好过给一个假的填充预览。
  patch: {
    facecolor: 'fill',
    edgecolor: 'stroke',
    linewidth: 'strokeWidth',
    alpha: 'opacity',
    visible: 'display',
  },
  scatter: {
    facecolor: 'fill',
    edgecolor: 'stroke',
    linewidth: 'strokeWidth',
    alpha: 'opacity',
    visible: 'display',
  },
  text: { color: 'textFill', alpha: 'textOpacity', visible: 'display' },
  title: { color: 'textFill', alpha: 'textOpacity', visible: 'display' },
  axis_label: { color: 'textFill', alpha: 'textOpacity', visible: 'display' },
  legend_text: { color: 'textFill', alpha: 'textOpacity', visible: 'display' },
  // 位图只有显隐可预览：alpha 被烤进 PNG 数据里，SVG 上无从下手
  image: { visible: 'display' },
}

/** 这个 role+prop 能不能局部预览（唯一出处，调用方不要自己判断类型） */
export function canPreviewStyle(role: string, prop: string): boolean {
  return !!STYLE_ADAPTERS[role]?.[prop]
}

export function adapterFor(role: string, prop: string): AdapterKind | null {
  return STYLE_ADAPTERS[role]?.[prop] ?? null
}

/* -------------------------------------------------------------------------- */
/*  DOM 读写                                                                   */
/* -------------------------------------------------------------------------- */

type Styled = SVGElement | HTMLElement

/** 该元素当前声明的某条样式（inline style 优先，其次表现属性）；没有则 null */
function declared(el: Styled, prop: string): string | null {
  const inline = el.style?.getPropertyValue(prop)
  if (inline) return inline.trim()
  const attr = el.getAttribute(prop)
  return attr == null || attr === '' ? null : attr.trim()
}

/** 声明了该属性、且不是 `none` —— 「本来就画着的那些叶子」 */
function paints(el: Styled, prop: string): boolean {
  const v = declared(el, prop)
  return v != null && v !== 'none'
}

function setStyle(el: Styled, prop: string, value: string, edits: StyleEdit[]): void {
  // 只在**第一次碰这个元素**时记账：同一 session 里反复改（拖滑块）不能把
  // 「上一帧的预览值」当成 base，否则还原会还到一个中间值上
  if (!edits.some((e) => e.el === el)) {
    edits.push({ el, prev: el.getAttribute('style') })
  }
  el.style.setProperty(prop, value)
}

/** gid 子树里的全部候选叶子（含 gid 节点自身：`<image>` 的样式就在它自己身上） */
function leaves(root: Element): Styled[] {
  const out: Styled[] = [root as Styled]
  for (const el of Array.from(root.querySelectorAll('*'))) out.push(el as Styled)
  return out
}

/**
 * 文字的字形组：`<g id=gid>` 底下那些带 transform 的 `<g>`。
 * 颜色/透明度写在这一层（`<use>` 引用的字形 path 从这里继承 fill）。
 * matplotlib 在默认黑色时**不输出** fill，所以这里允许新增属性。
 */
function glyphGroups(root: Element): Styled[] {
  const out: Styled[] = []
  for (const el of Array.from(root.querySelectorAll('g'))) {
    if (el.hasAttribute('transform')) out.push(el as unknown as Styled)
  }
  // 一个字都没有（空文字）时退回 gid 节点自身，至少不会静默什么都不做
  return out.length ? out : [root as Styled]
}

export interface StyleContext {
  /** SVG user unit 与 matplotlib pt 的比值（linewidth 换算，见 unitsPerPt） */
  unitsPerPt: number
}

/**
 * 把一条 override 值抢先画到 SVG 上，返回可还原的改动清单。
 * 返回空数组 = 这次预览什么都没改（值类型不对 / 子树里没有可改的叶子），
 * 调用方据此判断「预览没生效」，据实回退到后端而不是假装已经显示了。
 */
export function applyStyleEdit(
  node: Element,
  kind: AdapterKind,
  value: unknown,
  ctx: StyleContext,
): StyleEdit[] {
  const edits: StyleEdit[] = []
  for (const t of styleTargets(node, kind, value, ctx)) setStyle(t.el, t.prop, t.value, edits)
  return edits
}

/**
 * 这次预览**会不会真的改到东西**（只算，不碰 DOM）。
 *
 * 为什么需要单独一个判据：能力表按 role+prop 发，但同一个 role 的两个 artist
 * 在 SVG 上可以长得完全不同——`fill=False` 的 PathPatch 写的是 `fill: none`，
 * 而「只改本来就画着的叶子」这条规则不许把它填实。调用方
 * （`svgPreviewStore.previewStyle`）据此回退后端；不判的话它会把渲染策略降成
 * `'none'`，用户拖着改颜色**整轮什么都不会发生**——比每次都发后端更糟。
 *
 * 与 `applyStyleEdit` **共用 `styleTargets` 这一份实现**：分成两份迟早分叉，
 * 而分叉的表现正是「界面说预览生效了，画面纹丝不动」。
 */
export function canStyleEditApply(
  node: Element,
  kind: AdapterKind,
  value: unknown,
  ctx: StyleContext,
): boolean {
  return styleTargets(node, kind, value, ctx).length > 0
}

interface StyleTarget {
  el: Styled
  prop: string
  value: string
}

/** 这次预览要落到哪些叶子上、各写什么值。空数组 = 这次预览改不到任何东西。 */
function styleTargets(
  node: Element,
  kind: AdapterKind,
  value: unknown,
  ctx: StyleContext,
): StyleTarget[] {
  if (kind === 'display') {
    return [{ el: node as Styled, prop: 'display', value: value ? '' : 'none' }]
  }

  if (kind === 'textFill' || kind === 'textOpacity') {
    const prop = kind === 'textFill' ? 'fill' : 'opacity'
    const v = kind === 'textFill' ? colorOf(value) : numberOf(value)
    if (v == null) return []
    return glyphGroups(node).map((el) => ({ el, prop, value: String(v) }))
  }

  if (kind === 'strokeWidth') {
    const n = numberOf(value)
    if (n == null) return []
    const w = String(n * ctx.unitsPerPt)
    // 线宽只对「真的描着边」的叶子有意义。matplotlib 在线宽等于默认值时
    // 不输出 stroke-width，所以判据是 stroke 而不是 stroke-width——
    // 只认后者的话，柱形的默认 1.0pt 边框会拖不动
    return leaves(node)
      .filter((el) => paints(el, 'stroke'))
      .map((el) => ({ el, prop: 'stroke-width', value: w }))
  }

  if (kind === 'opacity') {
    const n = numberOf(value)
    if (n == null) return []
    const a = String(n)
    const out: StyleTarget[] = []
    for (const el of leaves(node)) {
      // matplotlib 自己就是分开写 fill-opacity / stroke-opacity 的
      // （见 fill_between 的输出）；粗暴地盖一个 `opacity` 会连带把
      // 已有的 fill-opacity 语义改掉，也无法准确还原
      if (paints(el, 'fill')) out.push({ el, prop: 'fill-opacity', value: a })
      if (paints(el, 'stroke')) out.push({ el, prop: 'stroke-opacity', value: a })
    }
    return out
  }

  const c = colorOf(value)
  if (c == null) return []
  const props: ('fill' | 'stroke')[] =
    kind === 'stroke' ? ['stroke'] : kind === 'fill' ? ['fill'] : ['stroke', 'fill']
  const out: StyleTarget[] = []
  for (const el of leaves(node)) {
    for (const prop of props) {
      if (paints(el, prop)) out.push({ el, prop, value: c })
    }
  }
  return out
}

/** 逐条还原（倒序：同一元素被记过多次时最早那条才是 base） */
export function restoreStyleEdits(edits: readonly StyleEdit[]): void {
  for (let i = edits.length - 1; i >= 0; i--) {
    const { el, prev } = edits[i]
    if (prev == null) el.removeAttribute('style')
    else el.setAttribute('style', prev)
  }
}

/* -------------------------------------------------------------------------- */
/*  单位                                                                       */
/* -------------------------------------------------------------------------- */

/**
 * SVG 的 user unit 与 matplotlib pt 的比值。
 *
 * matplotlib 存 SVG 时 viewBox 就是以 pt 为单位的画布尺寸（figsize 英寸 ×72），
 * 所以这个比值实测恒为 1、`stroke-width` 与 `linewidth` 数值相同。但**不硬编码
 * 成 1**：图幅可以被 size_mm override 改掉，比值由 viewBox 与 manifest 的
 * size_mm 现算，matplotlib 哪天换了单位这里也不会悄悄画错。
 * 算不出来时回 1（退化成今天的实测值，不至于让预览整个失效）。
 */
export function unitsPerPt(svg: SVGSVGElement | null, sizeMm?: readonly number[]): number {
  if (!svg || !sizeMm || !sizeMm.length) return 1
  const vb = (svg.getAttribute('viewBox') ?? '').split(/[\s,]+/).map(Number)
  if (vb.length < 4 || !Number.isFinite(vb[2]) || vb[2] <= 0) return 1
  const widthPt = (Number(sizeMm[0]) / 25.4) * 72
  if (!Number.isFinite(widthPt) || widthPt <= 0) return 1
  return vb[2] / widthPt
}

/* -------------------------------------------------------------------------- */
/*  取值                                                                       */
/* -------------------------------------------------------------------------- */

/** 只认 `#rgb` / `#rrggbb`：override 里的颜色一律是十六进制（manifest 用 to_hex） */
function colorOf(v: unknown): string | null {
  if (typeof v !== 'string') return null
  return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v.trim()) ? v.trim() : null
}

function numberOf(v: unknown): number | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN
  return Number.isFinite(n) ? n : null
}
