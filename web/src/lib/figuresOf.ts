import type { CanvasObject, PanelObject } from '@/types/document'

/**
 * 一张画布上「算数」的图：面板（figure）。**判据只有这一处**——runtime 图与磁盘图同为 `panel`、同等计；
 * 标注 / 文字 / 形状 / 箭头不是图，不计；**隐藏的图也计**（2026-10-21 用户拍板：隐藏是排版操作，不改模式、不发通知）。
 */
export function figuresOf(objects: readonly CanvasObject[]): PanelObject[] {
  return objects.filter((o): o is PanelObject => o.type === 'panel')
}
