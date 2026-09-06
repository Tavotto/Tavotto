import { LEGEND_ENTRY_STYLE_PROPS } from '@/lib/legendModel'
import { TEXT_EFFECTS } from '@/lib/textEffects'
import type { RoleProfile } from './types'

/**
 * 背景 / 描边的从属字段只在对应开关开着时渲染（关着的时候它们写了也不生效）。
 * 从 `lib/textEffects` 那张表算出来，不手抄——手抄的那份会在引擎多发一条
 * `bbox_*` 时忘记更新，症状是开关关着却多出一行孤零零的参数。
 */
const TEXT_EFFECT_VISIBILITY: NonNullable<RoleProfile['visibleWhen']> = Object.fromEntries(
  Object.entries(TEXT_EFFECTS).flatMap(([sw, deps]) =>
    deps.map((dep) => [dep, (read: (prop: string) => unknown) => read(sw) === true] as const),
  ),
)

/** 次刻度开着（`minor_visible`）才有意义的从属字段共用这一条 */
const MINOR_ON = (read: (prop: string) => unknown): boolean => read('minor_visible') === true

/** title / text / axis_label / legend_text 共用的模板 */
const TEXT_PROFILE: RoleProfile = {
  // 字体/字号/字形/颜色/对齐通常被 TextStyleBar 承接（进 presentFields 之前
  // 就被滤掉）；点名在这里是给**没凑齐工具条判据**的文字元素兜底——
  // 它们的字号/颜色也必须在首屏，不能因为少一个 weight 字段就掉进「更多」
  primary: ['text', 'fontfamily', 'fontsize', 'weight', 'style', 'color', 'ha'],
  more: [
    'va', 'rotation', 'linespacing', 'alpha',
    'bbox_visible', 'bbox_facecolor', 'bbox_alpha', 'bbox_edgecolor',
    'bbox_linewidth', 'bbox_pad', 'bbox_rounded',
    'stroke_enabled', 'stroke_color', 'stroke_width',
    'labelpad', 'visible',
  ],
  visibleWhen: TEXT_EFFECT_VISIBILITY,
}

/**
 * 角色 → 首屏模板。
 *
 * 这里只写**顺序与归属**：模板点名的属性 manifest 里没有就自动跳过
 * （能力仍由引擎说了算），模板没点名的属性按 registry 的兜底规则进
 * more/advanced——未知字段绝不丢失。
 *
 * 挑选标准（docs/ux/INSPECTOR_REDESIGN.md §2.3）：科研用户拿到该元素后
 * 最可能要改的 4–8 个属性进 primary；中频进 more；层级 / 裸坐标这类
 * 低频诊断进 advanced。文字类角色（title / text / axis_label / legend_text）
 * 的高频属性由共享文字控件（TextControls）承接，不在这张表里。
 */
export const ROLE_PROFILES: Record<string, RoleProfile> = {
  // 文字类：内容 + 字体/字号/字形/颜色/对齐（TextControls 行）是首屏；
  // 垂直对齐 / 旋转 / 行距 / 透明度 / 背景 / 描边进「更多」
  title: TEXT_PROFILE,
  text: TEXT_PROFILE,
  axis_label: TEXT_PROFILE,
  // 图例项 = 一段文字 + 一个条目（ADR 0034）：文字那半与别的文字一样，
  // 条目那半（与图中对象的绑定、示意线样式）也在首屏——用户选中一条图例项
  // 最可能要改的正是它的线型 / 线宽 / 标记。图例标题没有条目字段，模板里
  // 点名的属性 manifest 没给就自动跳过。
  legend_text: {
    primary: [
      ...TEXT_PROFILE.primary,
      'binding',
      'handle_color',
      'handle_linestyle',
      'handle_linewidth',
      'handle_marker',
      'handle_markersize',
    ],
    more: [...(TEXT_PROFILE.more ?? [])],
    visibleWhen: {
      ...TEXT_EFFECT_VISIBILITY,
      // 示意线的样式只在**断开链接后**出现（审计 T18）：链接中它由图中对象
      // 派生，摆一个此刻写了就会改变关系的控件，比收起来更不诚实。判据读的
      // 是 `binding` 字段（override 优先），与链接开关显示的状态同一个值；
      // 没有源的项引擎不发 `binding`，读出 undefined ≠ follow_source，样式
      // 照常在——那种项本来就没什么可跟随的。
      ...Object.fromEntries(
        LEGEND_ENTRY_STYLE_PROPS.map((prop) => [
          prop,
          (read: (p: string) => unknown) => read('binding') !== 'follow_source',
        ]),
      ),
      // 标记大小还要有标记
      handle_markersize: (read) =>
        read('binding') !== 'follow_source' && read('handle_marker') !== 'None',
    },
  },
  line: {
    primary: ['label', 'color', 'linewidth', 'linestyle', 'marker', 'markersize'],
    more: ['alpha', 'markerfacecolor', 'markeredgecolor', 'visible'],
  },
  linecoll: {
    primary: ['color', 'linewidth', 'linestyle'],
    more: ['alpha', 'visible'],
  },
  scatter: {
    primary: ['facecolor', 'cmap', 'vmin', 'vmax', 'marker', 'size', 'edgecolor', 'linewidth', 'alpha'],
    more: ['label', 'hatch', 'linestyle', 'visible'],
  },
  fill: {
    primary: ['facecolor', 'edgecolor', 'linewidth', 'hatch', 'alpha'],
    more: ['label', 'linestyle', 'visible'],
  },
  bar_series: {
    primary: ['label', 'facecolor', 'edgecolor', 'linewidth', 'hatch', 'alpha'],
    more: ['bar_width', 'visible'],
  },
  bar: {
    primary: ['facecolor', 'edgecolor', 'linewidth', 'hatch', 'alpha'],
    more: ['visible'],
  },
  patch: {
    primary: ['facecolor', 'fill', 'edgecolor', 'linewidth', 'hatch', 'alpha'],
    more: ['linestyle', 'visible'],
  },
  errorbar: {
    primary: ['color', 'linewidth', 'capsize', 'cap_thickness', 'alpha'],
    more: ['visible'],
  },
  arrow: {
    primary: ['arrowstyle', 'color', 'linewidth', 'linestyle'],
    more: ['mutation_scale', 'alpha', 'visible'],
  },
  // 图例（ADR 0034）：首屏是位置、列数、边框（审计 T17「主区保留位置、字体、
  // 列数和条目」）。字号由图例卡的 Typography 接管、条目顺序由图例卡的条目
  // 列表接管（`LEGEND_CARD_PROPS`）、五条间距由排版详情卡接管
  // （`LEGEND_SPACING_PROPS`）——三者都在 presentFields 之前就被让出来了，
  // 所以这张表里不再点名它们；`visibleWhen` 仍然管着它们（卡与通用列表共用
  // `registry.fieldVisible` 这一条判据）。
  legend: {
    primary: [
      'loc', 'ncol',
      'frameon', 'frame_linewidth', 'frame_rounded', 'edgecolor', 'facecolor',
    ],
    more: [
      'title', 'title_fontsize', 'fontsize', 'framealpha',
      'entry_order', 'visible',
    ],
    visibleWhen: {
      // 列距只在多列时有地方可摆；边框的四条只在边框开着时有意义
      columnspacing: (read) => Number(read('ncol')) > 1,
      frame_linewidth: (read) => read('frameon') !== false,
      frame_rounded: (read) => read('frameon') !== false,
      edgecolor: (read) => read('frameon') !== false,
      facecolor: (read) => read('frameon') !== false,
    },
  },
  axes: {
    // 子图页按任务分三段（审计 T12），各由一张卡承接、从通用列表里让出来：
    //   范围 / 坐标变换  —— ElementInspector 的 AxesRangeCard
    //                      （xlim / ylim / xscale / yscale / invert_* / aspect）
    //   刻度与网格      —— TickAndSpineDiagram（ticks_* / spine_<side> / grid_x / grid_y）
    //   边框            —— SpineFrameCard（spine_color / spine_linewidth / 逐边）
    // 这里点名的顺序只对**没被卡承接**的场合生效（卡不渲染时字段仍不丢）。
    // 尺寸（mm）由 AxesSizeMm 组件承接；裸 position rect 是 figure 分数
    // 坐标的诊断视图，进 advanced（manifest-first 泄漏，见审计 P6）。
    primary: ['xlim', 'ylim', 'xscale', 'yscale', 'invert_x', 'invert_y', 'aspect', 'grid_x', 'grid_y'],
    more: [
      'grid_color', 'grid_linestyle', 'grid_linewidth', 'grid_alpha',
      'spine_color', 'spine_linewidth', 'facecolor', 'visible',
    ],
  },
  axes3d: {
    primary: ['elev', 'azim', 'roll', 'proj_type'],
    more: ['visible'],
  },
  // 刻度组页把这些再分成「刻度 / 文字」两段（ElementInspector 的 TickPage）：
  // 这张表只管每个字段可不可见（模式从属）与段内顺序，不管落在哪一段
  ticks: {
    primary: ['major_mode', 'major_step', 'major_values', 'fontsize', 'color'],
    more: [
      'format', 'direction', 'length', 'width', 'minor_length', 'minor_width',
      'minor_visible', 'minor_mode', 'minor_step', 'minor_format',
      'rotation', 'visible',
    ],
    visibleWhen: {
      // 间距只在 step 模式下生效、固定值只在 fixed 模式下生效：
      // 摆一个此刻写了不生效的控件比藏起来更不诚实；已被用户改过的
      // 照样显示（registry 兜底），不会因折叠而不可发现。
      major_step: (read) => read('major_mode') === 'step',
      major_values: (read) => read('major_mode') === 'fixed',
      // 次刻度关着时它的长度 / 线宽 / 方式 / 间距 / 格式一并收起（审计 T13）。
      // 刻度卡（不走桶）与通用列表共用这一份判据（`registry.fieldVisible`），
      // 不各写一套——与文字的背景 / 描边从属字段同一种机制
      minor_length: MINOR_ON,
      minor_width: MINOR_ON,
      minor_mode: MINOR_ON,
      minor_format: MINOR_ON,
      minor_step: (read) => MINOR_ON(read) && read('minor_mode') === 'step',
    },
  },
  colorbar: {
    primary: ['cmap', 'vmin', 'vmax', 'orientation', 'tick_fontsize'],
    more: ['label', 'extend', 'tick_color', 'outline_visible', 'outline_width', 'visible'],
  },
  image: {
    primary: ['cmap', 'vmin', 'vmax', 'alpha'],
    more: ['interpolation', 'gradient_color', 'visible'],
    advanced: ['origin'],
    pairRows: [['vmin', 'vmax']],
  },
  figure: {
    primary: ['size_mm'],
  },
}
