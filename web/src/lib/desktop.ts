/**
 * 桌面（Tauri 壳）适配层——组件不得直接 import 任何 @tauri-apps 包，一律经这里。
 * 每个能力在浏览器模式下都有安全回退；@tauri-apps 模块全部按需动态 import，
 * 浏览器模式的 bundle 路径上一行 Tauri 代码都不会执行。
 *
 * 认证模型（与 src/magplot/desktop.py 对应）：壳把一次性 nonce 放在首个页面的
 * URL fragment 里（fragment 不进 HTTP 请求行，也就不进任何访问日志），页面
 * 启动时先经 POST /api/desktop/bootstrap 换成 HttpOnly 会话 cookie，再进界面。
 */

/** Tauri 2 注入的 IPC 标记；存在即运行在 Magplot 桌面壳里 */
export function isDesktop(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export type BootstrapResult = 'ok' | 'failed' | 'skipped'

/**
 * 一次性桌面会话建立。必须在任何 API 调用之前完成（main.tsx 等它 resolve 后
 * 才 render）；fragment 先清后请求，nonce 不留在地址栏与会话历史里。
 * 浏览器模式（无 fragment）直接 skipped，零开销。
 */
export async function bootstrapDesktopSession(): Promise<BootstrapResult> {
  const m = /[#&]dnonce=([A-Za-z0-9_-]+)/.exec(window.location.hash)
  if (!m) return 'skipped'
  history.replaceState(null, '', window.location.pathname + window.location.search)
  try {
    const res = await fetch('/api/desktop/bootstrap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nonce: m[1] }),
    })
    return res.ok ? 'ok' : 'failed'
  } catch {
    return 'failed'
  }
}

/** 系统菜单动作 id（与 src-tauri/src/main.rs 的 MenuItem id 严格同源） */
export type MenuAction = 'menu-open-project' | 'menu-export' | 'menu-undo' | 'menu-redo'

/**
 * 订阅系统菜单事件。返回取消函数；浏览器模式下是空订阅。
 * 菜单只转发动作，状态与行为全部复用现有 store action——绝不复制文档状态。
 */
export async function onDesktopMenu(
  handler: (action: MenuAction) => void,
): Promise<() => void> {
  if (!isDesktop()) return () => {}
  const { listen } = await import('@tauri-apps/api/event')
  return listen<string>('magplot:menu', (e) => handler(e.payload as MenuAction))
}

/**
 * 原生目录选择器（「打开项目」用）。用户取消返回 null——取消不是错误。
 * 浏览器模式返回 null，调用方回退到服务器端目录浏览器。
 */
export async function pickDirectory(title?: string): Promise<string | null> {
  if (!isDesktop()) return null
  const { open } = await import('@tauri-apps/plugin-dialog')
  const picked = await open({ directory: true, multiple: false, title })
  return typeof picked === 'string' ? picked : null
}

/**
 * 在系统文件管理器里显示导出的文件（桌面里不该出现浏览器式下载页 / PDF 标签页）。
 * 成功返回 true；浏览器模式或失败返回 false，调用方保留原有 <a> 行为。
 */
export async function revealExportedFile(dir: string, name: string): Promise<boolean> {
  if (!isDesktop() || !dir) return false
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('reveal_export', { dir, name })
    return true
  } catch {
    return false
  }
}
