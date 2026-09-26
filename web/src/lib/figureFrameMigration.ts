/**
 * 升级前放上排版的面板迁到 ADR 0098 的图幅语义（用户 2026-09-26 选 C）。判据的说明见
 * `lib/figureFrame.ts` 的模块头；这里单独成一个叶子模块，是因为读档入口
 * （`types/document.migrateToProject`）要调它，而 `figureFrame` 要从 `types/document` 取换算。
 */
import type { CanvasObject, PanelObject, PanelOverride } from '@/types/document'

/** 面板上的记号：这个面板按 ADR 0098 的图幅语义放上来 / 迁移过 */
export const FIGURE_FRAME_VERSION = 1

/** 升级前的排版：图幅按 figsize（引擎 `overrides.HANDLERS[("figure", "frame")]`） */
export const LEGACY_FRAME_OVERRIDE: PanelOverride = { gid: 'figure', prop: 'frame', value: 'figsize' }

export const isLegacyFrameOverride = (o: PanelOverride) =>
  o.gid === LEGACY_FRAME_OVERRIDE.gid && o.prop === LEGACY_FRAME_OVERRIDE.prop

export const hasLegacyFrame = (panel: PanelObject) => panel.overrides.some(isLegacyFrameOverride)

/** 这个面板此刻的样子来自引擎（而不是磁盘原件）：runtime 面板，或带着图内修改 */
function renderedByEngine(panel: PanelObject): boolean {
  if (panel.fileKind === 'runtime') return true
  return panel.fileKind === 'pdf' && panel.overrides.length > 0
}

/**
 * 升级前放上去的面板打上记号，必要时补 figsize 那条 override（见 `lib/figureFrame.ts` 模块头）。
 * 纯函数：没有要改的返回原数组（引用不变），否则返回新数组，改过的面板是新对象。
 */
export function migrateFigureFrames(objects: CanvasObject[]): CanvasObject[] {
  let changed = false
  const out = objects.map((o) => {
    if (o.type !== 'panel' || o.figureFrame === FIGURE_FRAME_VERSION) return o
    changed = true
    const next: PanelObject = { ...o, figureFrame: FIGURE_FRAME_VERSION }
    if (renderedByEngine(o) && !hasLegacyFrame(o)) {
      next.overrides = [...o.overrides, { ...LEGACY_FRAME_OVERRIDE }]
    }
    return next
  })
  return changed ? out : objects
}
