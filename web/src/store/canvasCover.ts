/**
 * 画布此刻被盖住（「数据」临时标签激活：内容区盖在画布位置上，底下的画布与文档还在）。
 * 一个无依赖的小标志，让画布动作的让位判据（`gestureCoordinator.yieldsCanvasShortcuts`，键盘 / 系统菜单 / 剪贴板事件共用的那一条）
 * 与几个会改文档的入口函数（撤销重做 / 删除 / 创建副本，顶栏与命令面板也走它们）读同一个事实，
 * 而不必 import `dataFigureStore`（它带着整个 api 层）。由 `dataFigureStore` 在 `draftActive` 变化时写。
 */
let covered = false
export const isCanvasCovered = (): boolean => covered
export const setCanvasCovered = (v: boolean): void => {
  covered = v
}
