import type { ElementWriter } from './elementWrite'
import type { TypographyAdapter } from './typographyAdapter'

/**
 * 图例卡的 Typography：字体 / 粗斜 / 颜色仍批量作用于全部图例项，**字号改走图例整体的
 * `fontsize`**——与画布浮动栏（`ElementBar`）、拖角整体缩放同一个属性、同一个写入器。
 *
 * 为什么不是图例项的字号：ADR 0034 的 2026-09-25 修订把图例 `fontsize` 定成 matplotlib
 * 原生语义（改 `Legend._fontsize` 并整盒重排，边距 / 行距 / 示意线长都以它为单位）。
 * 逐条改文字只是字变了、框不跟着变——那条修订修的正是这个缺陷；卡片还走老路时，从属性页改
 * 字号既把它带了回来，又让浮动栏读到另一个值（属性页 10 pt、浮动栏还是 8 pt）。
 *
 * 写图例 `fontsize` 时，各图例项上的旧 `fontsize` override 在同一次 commit 里被清掉
 * （`upsertOverrides` 按 manifest 的 `alias_covers`，`lib/aliasOverrides.ts`），老文档里旧卡片
 * 逐条写下的字号不会压在新值上。
 *
 * 读数以图例字号为准，两种情况显示「多个值」（两个读数都不是全貌，绝不拿一个冒充）：
 *   * 图例项自己带着字号 override（旧卡片留下的老文档）且与图例字号不一致；
 *   * 图例字号没改过、脚本给各项设了不同的字号。
 * 不拿 manifest 里各项的字号去比图例字号的 override：刚写下、还没渲染回来时各项仍是旧值，
 * 那样比会在每次改字号时闪一下「多个值」。
 *
 * 引擎不给图例 `fontsize` 时（老引擎 / 不可编辑）原样返回批量适配器，行为同从前。
 */
export function withLegendFontSize(
  entries: TypographyAdapter,
  legend: ElementWriter,
  opts: {
    /** 图例那条 `fontsize` 此刻有没有 override */
    legendOverridden: boolean
    /** 一次撤销清掉图例与各项的 `fontsize`（`store/actions.clearLegendFontSize`） */
    reset: () => void
  },
): TypographyAdapter {
  if (!legend.fieldOf('fontsize')) return entries
  return {
    ...entries,
    fieldOf: (prop) => (prop === 'sizePt' ? legend.fieldOf('fontsize') : entries.fieldOf(prop)),
    valueOf: (prop) => {
      if (prop !== 'sizePt') return entries.valueOf(prop)
      const own = Number(legend.read('fontsize'))
      const items = entries.valueOf('sizePt')
      if (entries.overrideStateOf('sizePt') !== 'none') {
        if (items.kind === 'mixed') return items
        if (items.kind === 'uniform' && Math.abs(Number(items.value) - own) > 0.005) return { kind: 'mixed' }
      } else if (!opts.legendOverridden && items.kind === 'mixed') {
        return items
      }
      return { kind: 'uniform', value: own }
    },
    write: (prop, value, immediate) =>
      prop === 'sizePt' ? legend.write('fontsize', value, immediate) : entries.write(prop, value, immediate),
    writeOnce: (prop, value) =>
      prop === 'sizePt' ? legend.writeOnce('fontsize', value) : entries.writeOnce(prop, value),
    // 字号是这组控件里唯一会 scrub 的量：一轮拖动压成图例写入器上的一条历史
    beginGesture: () => legend.beginGesture(),
    endGesture: () => {
      legend.endGesture()
      entries.endGesture()
    },
    overrideStateOf: (prop) =>
      prop === 'sizePt' && opts.legendOverridden ? 'all' : entries.overrideStateOf(prop),
    reset: (prop) => (prop === 'sizePt' ? opts.reset() : entries.reset(prop)),
  }
}
