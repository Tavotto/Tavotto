/**
 * 桌面（Tauri 壳）适配层——组件不得直接 import 任何 @tauri-apps 包，一律经这里。
 * 每个能力在浏览器模式下都有安全回退；@tauri-apps 模块全部按需动态 import，
 * 浏览器模式的 bundle 路径上一行 Tauri 代码都不会执行。
 *
 * 认证模型（与 src/tavotto/security.py 对应，桌面与浏览器模式共用一道边界）：
 * 启动方把一次性 nonce 放在首个页面的 URL fragment 里（fragment 不进 HTTP
 * 请求行，也就不进任何访问日志），页面启动时先经 POST /api/session/bootstrap
 * 换成 HttpOnly 会话 cookie，再进界面。
 */

/** Tauri 2 注入的 IPC 标记；存在即运行在 Tavotto 桌面壳里 */
import { t } from '@/i18n'
import { CodexShellError } from '@/lib/codexInstall'

export function isDesktop(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export type BootstrapResult = 'ok' | 'failed' | 'skipped' | 'unauthenticated'

/**
 * 一次性会话建立（桌面与浏览器模式同一条路）。必须在任何 API 调用之前完成
 * （main.tsx 等它 resolve 后才 render）；fragment 先清后请求，nonce 不留在
 * 地址栏与会话历史里。
 *
 * 无 fragment 时问一次 /api/session/ping：401 = 认证开着但这个浏览器没有
 * 会话（cookie 过期 / 服务器重启过 / 手敲地址）——返回 'unauthenticated'，
 * 让 main.tsx 给出「重新运行 tavotto」的可操作提示，而不是白屏 + 一串 401。
 * ping 通（或根本没启用认证，如测试与 dev proxy）返回 'skipped'。
 *
 * nonce 已被用过（用户把终端里打印的链接点了第二次）时同一浏览器往往已经
 * 持有有效 cookie：bootstrap 403 后再 ping 一次，通了照样 'ok'。
 */
export async function bootstrapDesktopSession(): Promise<BootstrapResult> {
  const m = /[#&]dnonce=([A-Za-z0-9_-]+)/.exec(window.location.hash)
  if (!m) {
    try {
      const ping = await fetch('/api/session/ping')
      if (ping.status === 401) return 'unauthenticated'
    } catch {
      /* 网络层失败交给正常的 API 错误路径 */
    }
    return 'skipped'
  }
  history.replaceState(null, '', window.location.pathname + window.location.search)
  try {
    const res = await fetch('/api/session/bootstrap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nonce: m[1] }),
    })
    if (res.ok) return 'ok'
    const ping = await fetch('/api/session/ping')
    return ping.ok ? 'ok' : 'failed'
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
  return listen<string>('tavotto:menu', (e) => handler(e.payload as MenuAction))
}

/** 桌面交接事件的载荷（与 src-tauri/src/main.rs 的 OpenRequest 严格同源） */
export interface DesktopOpenPayload {
  project: string
  stem?: string | null
  /** 多 Figure 交接的选择信息（脚本相对路径）——Figure 选择器在前端 */
  pick?: string | null
  /** `tavotto run` 的一次性交接 ID（ADR 0021 §4）——确认界面在前端。
   *  与 stem / pick **不互斥** */
  native?: string | null
}

/**
 * 订阅「把这张图交给我打开」。壳在**已经开着窗口**时收到第二次启动的
 * `--open/--stem` 就发这个事件（单实例转发 argv）；首启不发——那一次项目走
 * sidecar 的 `--figures`、stem 走落地 URL 的 `?open=`、`tavotto run` 的交接
 * ID 走 `?native=`（壳的 `landing_query`），三条路最终都汇进
 * lib/openRequest.ts 的同一个 applyOpenRequest。
 */
export async function onDesktopOpen(
  handler: (payload: DesktopOpenPayload) => void,
): Promise<() => void> {
  if (!isDesktop()) return () => {}
  const { listen } = await import('@tauri-apps/api/event')
  return listen<DesktopOpenPayload>('tavotto:open', (e) => handler(e.payload))
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

/**
 * 把界面语言告诉壳，让原生菜单跟着换。
 *
 * 原生菜单是 Rust 在 webview 起来之前建的，那套文案在 `src-tauri/src/i18n.rs`
 * 里另有一份（见那里的说明），i18next 够不着。所以由前端**主动报**：i18n 就绪
 * 时一次、用户切语言时一次。Rust 顺手把选择记在应用配置目录里，下次启动的
 * 菜单一开始就是对的。
 *
 * 浏览器模式下没有原生菜单，直接返回 false——**不抛**：语言切换是纯界面动作，
 * 不该因为壳不在就失败。
 *
 * `explicit` 区分「用户在设置里换了语言」与「i18n 就绪时汇报当前生效的那门」。
 * 桌面模式下 sidecar 绑 `127.0.0.1:0`，端口每次都变，前端 localStorage 的偏好
 * 活不过一次重启——壳记的那份是唯一活得下来的存储，而它必须知道哪次是真正的
 * 选择，否则一次「跟随系统」的汇报就把用户选过的语言洗掉了。
 */
export async function setDesktopMenuLocale(
  locale: string,
  explicit = false,
): Promise<boolean> {
  if (!isDesktop()) return false
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('set_menu_locale', { locale, explicit })
    return true
  } catch {
    // 老版本的壳没有这个命令（ACL 会直接拒），菜单保持旧语言即可
    return false
  }
}

/* -------------------------------------------------------------------------- */
/*  Codex 集成的安装 / 诊断（ADR 0012）                                          */
/* -------------------------------------------------------------------------- */

/**
 * 跑一次 `tavotto-cli codex <action> --json`，返回它打出来的那一行 JSON 原文。
 *
 * **这里不解析、不判断、更不安装**：壳只负责 spawn，解析在
 * `lib/codexInstall.ts`，安装步骤只在 `engine/codexinstall.py`（ADR 0012 的
 * 「不写第二套安装器」）。`action` 是闭集，Rust 侧再挡一次。
 *
 * 浏览器模式下抛 `CodexShellError('not_desktop')`——**不是静默返回空**：
 * 按钮本来就只在桌面模式渲染，真走到这里说明有人绕过了那个判断，
 * 悄悄回一个「成功」会把它藏起来。
 */
export async function runCodexIntegration(action: 'install' | 'doctor'): Promise<string> {
  if (!isDesktop()) throw new CodexShellError('not_desktop')
  const { invoke } = await import('@tauri-apps/api/core')
  try {
    return await invoke<string>('codex_integration', { action })
  } catch (e) {
    // Rust 回的是稳定 code（cli_not_found / spawn_failed / bad_output /
    // bad_action）；ACL 拒绝或老壳没有这个命令时回的是别的字符串，
    // 一律当 spawn_failed——**不把它当句子显示给用户**。
    const code = typeof e === 'string' ? e : ''
    throw new CodexShellError(code || 'spawn_failed')
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
  if (!update) throw new Error(t('update.noPendingUpdate', { ns: 'errors' }))
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
