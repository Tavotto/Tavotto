/**
 * 「双击工作区空白 → 适应当前画布」的触发判定（纯函数，便于测试）。
 *
 * 只有同时满足才触发：选择工具、非平移/绘制/裁剪/文字编辑/拖动中、
 * 双击点不在任何对象上、且落在页面之外的灰色工作区。
 */
export interface FitGuardCtx {
  tool: string
  spaceDown: boolean
  editingText: boolean
  cropping: boolean
  /** 有正在进行的拖动/框选/绘制交互 */
  interacting: boolean
  /** 双击目标在某个画布对象（含图内元素宿主面板）内 */
  onObject: boolean
  /** 双击点的文档 mm 坐标 */
  point: { x: number; y: number }
  page: { w: number; h: number }
}

export function shouldFitOnDoubleClick(c: FitGuardCtx): boolean {
  if (c.tool !== 'select' || c.spaceDown) return false
  if (c.editingText || c.cropping || c.interacting || c.onObject) return false
  const inPage =
    c.point.x >= 0 && c.point.y >= 0 && c.point.x <= c.page.w && c.point.y <= c.page.h
  return !inPage
}
