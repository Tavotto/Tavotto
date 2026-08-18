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

/* -------------------------------------------------------------------------- */
/*  应用内更新（桌面壳）                                                        */
/* -------------------------------------------------------------------------- */

/**
 * 桌面版的升级归 Tauri 层（Python updater 在桌面模式整个停用，见 desktop.py）。
 * 这里是它在前端的唯一入口：检查 → 下载安装 → 重启，三步各自可见、可失败，
 * **绝不静默进行**——什么时候换版本必须是用户按下按钮的结果。
 *
 * 更新包的签名由壳里的公钥校验（tauri.conf.json 的 plugins.updater.pubkey），
 * 私钥只在 CI 里；校验不过 downloadAndInstall 当场抛错，装不上去。
 */
export interface DesktopUpdateInfo {
  version: string
  notes?: string
  /** Release 里写的发布时间，原样透出，不在前端解析格式 */
  date?: string
}

interface UpdateHandle {
  version: string
  downloadAndInstall: (cb?: (e: unknown) => void) => Promise<void>
}

/** check() 拿到的句柄——下载安装要用同一个，不能到时候重新查一次 */
let pendingUpdate: UpdateHandle | null = null

/**
 * 查有没有新版。没有新版返回 null；**浏览器模式也返回 null**——那条路由
 * Python updater 负责（/api/update/*），两条不能同时插手。
 */
export async function checkDesktopUpdate(): Promise<DesktopUpdateInfo | null> {
  if (!isDesktop()) return null
  const { check } = await import('@tauri-apps/plugin-updater')
  const update = await check()
  pendingUpdate = update ? (update as unknown as UpdateHandle) : null
  if (!update) return null
  return {
    version: update.version,
    notes: update.body ?? undefined,
    date: update.date ?? undefined,
  }
}

/**
 * 下载并安装上一次查到的那一版。onProgress 收到 0–1 的进度；服务端没给
 * Content-Length 时收到 null——进度条该显示成不确定态，而不是假装卡在
 * 某个百分比上。
 *
 * 没有句柄就抛，不在这里偷偷补一次 check：用户看到的版本号与真正装上去的
 * 那一版必须是同一个。
 */
export async function installDesktopUpdate(
  onProgress?: (fraction: number | null) => void,
): Promise<void> {
  const update = pendingUpdate
  if (!update) throw new Error('没有待安装的更新，请先检查更新')
  let total = 0
  let got = 0
  await update.downloadAndInstall((event) => {
    const e = event as { event: string; data?: { contentLength?: number; chunkLength?: number } }
    if (e.event === 'Started') {
      total = e.data?.contentLength ?? 0
      got = 0
      onProgress?.(total ? 0 : null)
    } else if (e.event === 'Progress') {
      got += e.data?.chunkLength ?? 0
      onProgress?.(total ? Math.min(1, got / total) : null)
    } else if (e.event === 'Finished') {
      onProgress?.(1)
    }
  })
  pendingUpdate = null
}

/** 装完重启到新版本。浏览器模式退化成刷新页面。 */
export async function relaunchDesktop(): Promise<void> {
  if (!isDesktop()) {
    location.reload()
    return
  }
  const { relaunch } = await import('@tauri-apps/plugin-process')
  await relaunch()
}

/** 仅供测试：清掉 check 留下的句柄 */
export function __resetDesktopUpdate(): void {
  pendingUpdate = null
}
