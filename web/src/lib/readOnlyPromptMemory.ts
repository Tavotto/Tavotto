/**
 * 「没有写入权限的项目要不要另存一份」本次会话已经问过的项目（按项目路径）：刷新项目状态、切走再切回都不重复弹。
 * 只是内存里的「这次先别再问」；跨启动的记忆是后端项目设置 `readonly_save_as_declined`（只在用户点了「继续只读打开」时写）。
 * 单独成文件：组件文件只导出组件，fast refresh 才生效。
 */
const asked = new Set<string>()

export const wasAsked = (root: string) => asked.has(root)
export const markAsked = (root: string) => void asked.add(root)

/** 仅供测试：清掉「本次会话已问过」 */
export function resetReadOnlyPromptMemory() {
  asked.clear()
}
