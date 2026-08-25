import type { RoleProfile } from './types'

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
  legend_text: TEXT_PROFILE,
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
  legend: {
    primary: ['loc', 'fontsize', 'frameon', 'edgecolor', 'facecolor'],
    more: [
      'ncol', 'labelspacing', 'borderpad', 'handlelength',
      'title', 'title_fontsize', 'framealpha', 'entry_order', 'visible',
    ],
  },
  axes: {
    // 尺寸（mm）由 AxesSizeMm 组件承接；裸 position rect 是 figure 分数
    // 坐标的诊断视图，进 advanced（manifest-first 泄漏，见审计 P6）。
    // ticks_* / spine_* / grid_* 的四边开关由 TickAndSpineDiagram 承接。
    primary: ['xlim', 'ylim', 'xscale', 'yscale', 'grid_x', 'grid_y'],
    more: [
      'invert_x', 'invert_y', 'aspect',
      'grid_color', 'grid_linestyle', 'grid_linewidth', 'grid_alpha',
      'spine_color', 'spine_linewidth', 'facecolor', 'visible',
    ],
  },
  axes3d: {
    primary: ['elev', 'azim', 'roll', 'proj_type'],
    more: ['visible'],
  },
  ticks: {
    primary: ['major_mode', 'major_step', 'major_values', 'fontsize', 'color'],
    more: [
      'format', 'direction', 'length', 'width',
      'minor_visible', 'minor_mode', 'minor_step', 'minor_format',
      'rotation', 'visible',
    ],
    visibleWhen: {
      // 间距只在 step 模式下生效、固定值只在 fixed 模式下生效：
      // 摆一个此刻写了不生效的控件比藏起来更不诚实；已被用户改过的
      // 照样显示（registry 兜底），不会因折叠而不可发现。
      major_step: (read) => read('major_mode') === 'step',
      major_values: (read) => read('major_mode') === 'fixed',
      minor_step: (read) => read('minor_mode') === 'step',
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
  },
  figure: {
    primary: ['size_mm'],
  },
}
