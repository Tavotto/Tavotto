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

import { currentLocale, t } from '@/i18n'
import { CodexShellError } from '@/lib/codexInstall'

/**
 * 运行在 Tavotto 桌面壳里（Tauri 2 注入的 IPC 标记在）——不管引擎在哪台机器上。
 * 只有「壳本身」的能力按它判：系统菜单转发、关窗询问。
 */
export function hasDesktopShell(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/**
 * 这个窗口是壳的「远程实例」窗口（ADR 0105）：页面来自经 `ssh -L` 转发过来的
 * 服务器上的引擎，**引擎看到的是服务器的文件系统**。标记由壳在建窗口时注入
 * （`src-tauri/src/main.rs` 的 `REMOTE_WINDOW_MARKER`，两侧严格同源）。
 */
export function isRemoteEngineWindow(): boolean {
  return (
    hasDesktopShell() &&
    (window as unknown as Record<string, unknown>).__TAVOTTO_REMOTE_ENGINE__ === true
  )
}

/**
 * 桌面壳、**且引擎就是壳自己拉起的本机 sidecar**。本机文件类能力（原生选择器、
 * 在文件管理器中显示、拖放真实路径、Codex 集成、应用内更新）一律按它判：
 * 远程实例窗口里这些路径指的是服务器上的文件，交给本机的对话框或 Finder
 * 只会错位——那里走浏览器模式的回退，壳的 ACL 也不给那个窗口这些命令。
 */
export function isDesktop(): boolean {
  return hasDesktopShell() && !isRemoteEngineWindow()
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

/**
 * 系统菜单转发过来的动作 id——与 `src-tauri/src/main.rs` 里 `menu-*` 的
 * MenuItem id **严格同源**（`tests/test_desktop_i18n.py` 两侧比集合）。
 * 帮助菜单的两条链接（`help-*`）由壳自己打开，不在这里。
 */
export const MENU_ACTIONS = [
  'menu-settings',
  'menu-check-updates',
  'menu-open-project',
  'menu-save',
  'menu-save-layout',
  'menu-export',
  'menu-undo',
  'menu-redo',
  'menu-duplicate',
  'menu-delete',
  'menu-align-left',
  'menu-align-hcenter',
  'menu-align-right',
  'menu-align-top',
  'menu-align-vcenter',
  'menu-align-bottom',
  'menu-align-hdist',
  'menu-align-vdist',
  'menu-zoom-in',
  'menu-zoom-out',
  'menu-zoom-actual',
  'menu-zoom-fit',
  'menu-toggle-left',
  'menu-toggle-right',
  'menu-shortcut-help',
  'menu-diagnostics',
] as const
export type MenuAction = (typeof MENU_ACTIONS)[number]

const isMenuAction = (id: string): id is MenuAction =>
  (MENU_ACTIONS as readonly string[]).includes(id)

/**
 * 订阅系统菜单事件。返回取消函数；浏览器模式下是空订阅。
 * 菜单只转发动作，状态与行为全部复用现有 store action——绝不复制文档状态。
 * 认不出的 id 丢掉（新壳配旧前端时不该落进 switch 的哪个分支）。
 */
export async function onDesktopMenu(
  handler: (action: MenuAction) => void,
): Promise<() => void> {
  // 远程实例窗口也收菜单：壳把菜单动作发给**当前聚焦**的那个窗口
  if (!hasDesktopShell()) return () => {}
  const { listen } = await import('@tauri-apps/api/event')
  return listen<string>('tavotto:menu', (e) => {
    if (isMenuAction(e.payload)) handler(e.payload)
  })
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

/* -------------------------------------------------------------------------- */
/*  主页拖放：系统交来的真实路径（ADR 0092）                                        */
/* -------------------------------------------------------------------------- */

/**
 * 壳分派好的一次放下（与 `src-tauri/src/drop_paths.rs` 的 `DropTarget` 严格同源，
 * `tests/test_desktop_file_drop.py` 逐个比 kind）。路径都是壳 canonicalize 过、真实存在的
 * 本地绝对路径；打开项目仍走 `/api/projects/open`（会话认证不绕过）。
 */
export type NativeFileDrop =
  | { kind: 'script'; folder: string; script: string; name: string; ignored: number }
  | { kind: 'folder'; folder: string; name: string; ignored: number }
  | { kind: 'unsupported'; name: string }

/**
 * 这个壳能不能在拖放时交出真实路径（macOS 上旁听装好了才是 true）。浏览器模式、
 * 其它平台、命令被拒（ACL 漏登记）一律 false——调用方走「提示文件名 + 选择器」的降级。
 */
export async function nativeFileDropAvailable(): Promise<boolean> {
  if (!isDesktop()) return false
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    return (await invoke<boolean>('native_file_drop')) === true
  } catch {
    return false
  }
}

/**
 * 订阅系统拖放（`tavotto:file-drop`）。壳在每次有文件放进窗口、页面又接受了这次拖动时
 * 都发；**只有主页订阅**——编辑器不订阅，事件落空，画布的 HTML5 拖放照旧。浏览器模式是空订阅。
 */
export async function onNativeFileDrop(
  handler: (drop: NativeFileDrop) => void,
): Promise<() => void> {
  if (!isDesktop()) return () => {}
  const { listen } = await import('@tauri-apps/api/event')
  return listen<NativeFileDrop>('tavotto:file-drop', (e) => handler(e.payload))
}

/* -------------------------------------------------------------------------- */
/*  关窗询问闸（issue #223）                                                    */
/* -------------------------------------------------------------------------- */

/**
 * 对壳的关窗询问的答复。**闭集**，与 `src-tauri/src/main.rs` 的 `CloseDecision`
 * 严格同源（`tests/test_desktop_close_guard.py` 逐个比两侧）。
 *
 * - `hold`：我接手了，正在问用户。壳的看门狗从此不再强关，用户想多久都行。
 * - `close`：关吧。
 * - `cancel`：窗口留着。
 */
export type CloseDecision = 'hold' | 'close' | 'cancel'

/**
 * 告诉壳「我在，关窗前先问我」。
 *
 * **必须在 `onDesktopCloseRequested()` 注册成功之后才调**：反过来的话，两者
 * 之间的那次关闭会被拦下来问一个没人听的问题，用户看到的是按钮按了不动，
 * 直到壳的看门狗超时。
 *
 * 浏览器模式返回 false（那边由 `beforeunload` 兜着）；老版本的壳没有这个命令，
 * ACL 会直接拒 —— 同样返回 false，退回改造前的行为，绝不抛。
 */
export async function armDesktopCloseGuard(): Promise<boolean> {
  // 关窗询问按窗口各算各的（壳里每个窗口一道闸），远程实例窗口同样要问
  if (!hasDesktopShell()) return false
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('arm_close_guard')
    return true
  } catch {
    return false
  }
}

/**
 * 订阅「用户点了窗口关闭按钮」。窗口此刻已经被壳拦住，处理器**必须**在两秒内
 * 用 `resolveDesktopCloseRequest()` 答一句，否则壳会当 webview 死了并放行关闭。
 */
export async function onDesktopCloseRequested(handler: () => void): Promise<() => void> {
  if (!hasDesktopShell()) return () => {}
  const { listen } = await import('@tauri-apps/api/event')
  return listen('tavotto:close-requested', () => handler())
}

/** 答复壳的关窗询问。浏览器模式 / 老壳返回 false（不抛）。 */
export async function resolveDesktopCloseRequest(decision: CloseDecision): Promise<boolean> {
  if (!hasDesktopShell()) return false
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('resolve_close_request', { decision })
    return true
  } catch {
    return false
  }
}

/* -------------------------------------------------------------------------- */
/*  引擎意外退出与「上次没有正常退出」（ADR 0129）                              */
/* -------------------------------------------------------------------------- */

/** 引擎意外退出的类型（闭集，与 `src-tauri/src/exit_marker.rs` 的 `EngineExit` 同源；不带 status 文本与日志）。 */
export const ENGINE_EXIT_KINDS = ['signal', 'nonzero', 'zero'] as const
export type EngineExitKind = (typeof ENGINE_EXIT_KINDS)[number]
/** 上次会话的不正常结局（闭集，与 `exit_marker.rs` 的 `Previous` 同源）。 */
export const PREVIOUS_EXITS = ['app_unclean', 'engine_exited'] as const
export type PreviousExit = (typeof PREVIOUS_EXITS)[number]
/** 壳在引擎意外退出时发给主窗口的事件（`main.rs` 的 `ENGINE_EXITED_EVENT`，两侧严格同源）。 */
export const ENGINE_EXITED_EVENT = 'tavotto:engine-exited'

const asEngineExit = (v: unknown): EngineExitKind | null =>
  (ENGINE_EXIT_KINDS as readonly unknown[]).includes(v) ? (v as EngineExitKind) : null

/**
 * 订阅「引擎意外退出」。只有本机引擎的主窗口有这条事件（远程实例窗口的引擎不是壳拉起的）；
 * 浏览器模式 / 远程窗口返回空的取消函数。类型认不出一律忽略。
 */
export async function onEngineExited(cb: (kind: EngineExitKind) => void): Promise<() => void> {
  if (!isDesktop()) return () => {}
  try {
    const { listen } = await import('@tauri-apps/api/event')
    return await listen<{ kind?: unknown }>(ENGINE_EXITED_EVENT, (e) => {
      const kind = asEngineExit(e.payload?.kind)
      if (kind) cb(kind)
    })
  } catch {
    return () => {}
  }
}

/** 回读「本次会话里引擎是否已意外退出」（防事件早于监听）。非桌面 / 老壳 / 出错一律 null。 */
export async function getEngineExited(): Promise<EngineExitKind | null> {
  if (!isDesktop()) return null
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    return asEngineExit(await invoke('current_engine_exit'))
  } catch {
    return null
  }
}

/**
 * 取走「上次没有正常退出」的结局：读一次即清空（页面重载不重复提示）。
 * 非桌面 / 远程窗口 / 老壳 / 出错一律 null——没有这条信息就当上次正常。
 */
export async function takePreviousExit(): Promise<PreviousExit | null> {
  if (!isDesktop()) return null
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const v = await invoke('take_previous_exit')
    return (PREVIOUS_EXITS as readonly unknown[]).includes(v) ? (v as PreviousExit) : null
  } catch {
    return null
  }
}

/**
 * 重新启动本机引擎（壳关旧句柄、重新起、换 nonce、导航）。成功时页面随即被换掉。
 * 返回壳是否动了手：重复点击 / 正在重启 / 引擎并没退出 = false；非桌面 / 出错 = false（不抛）。
 */
export async function restartEngine(): Promise<boolean> {
  if (!isDesktop()) return false
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    return (await invoke('restart_engine')) === true
  } catch {
    return false
  }
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
 * 原生文件选择器，只收 `.py`（主页「导入我的脚本」用）。取消返回 null；
 * 浏览器模式返回 null，调用方回退到服务器端目录浏览器（选脚本所在的文件夹）。
 * 与 `pickDirectory` 同一条 `dialog:allow-open` 权限，壳侧不用加 ACL。
 */
export async function pickScriptFile(title?: string): Promise<string | null> {
  if (!isDesktop()) return null
  const { open } = await import('@tauri-apps/plugin-dialog')
  const picked = await open({
    directory: false,
    multiple: false,
    title,
    filters: [{ name: 'Python', extensions: ['py'] }],
  })
  return typeof picked === 'string' ? picked : null
}

/**
 * 原生文件选择器，不限类型（ADR 0106「找到这个文件…」：脚本要读的数据可以是任何格式）。
 * 取消返回 null；浏览器模式返回 null，调用方改让用户粘贴路径。同一条 `dialog:allow-open` 权限。
 */
export async function pickAnyFile(title?: string): Promise<string | null> {
  if (!isDesktop()) return null
  const { open } = await import('@tauri-apps/plugin-dialog')
  const picked = await open({ directory: false, multiple: false, title })
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
 * 「在 Finder 中打开」这类入口能不能摆出来：只有桌面壳做得到。
 *
 * 浏览器模式**不走后端补这一条**：服务器不一定和浏览器在同一台机器上（`ssh -L`
 * 转发时它在远端，文件管理器会开在别人看不见的屏幕上），为此新增一个会 spawn
 * 进程的端点也不值。所以浏览器里直接不摆这一项，而不是摆一个点了没用的。
 */
export function canRevealInFileManager(): boolean {
  return isDesktop()
}

/** 系统文件管理器叫什么（决定菜单文案）：Finder / 文件资源管理器 / 其余统称文件管理器 */
export type FileManagerKind = 'finder' | 'explorer' | 'files'

export function fileManagerKind(
  userAgent: string = typeof navigator !== 'undefined' ? navigator.userAgent : '',
): FileManagerKind {
  if (/Macintosh|Mac OS X/.test(userAgent)) return 'finder'
  if (/Windows/.test(userAgent)) return 'explorer'
  return 'files'
}

/**
 * 在系统文件管理器里打开项目文件夹：有顶层脚本就选中（按名字排第一的）那个 `.py`，
 * 没有就选中文件夹本身。壳只 reveal、不 open，且只收此刻存在的绝对目录
 * （`src-tauri/src/main.rs::reveal_project_dir`）。
 * 成功返回 true；浏览器模式、老壳（ACL 拒）或失败返回 false——调用方必须把完整路径
 * 告诉用户，不许静默。
 */
export async function revealProjectFolder(path: string): Promise<boolean> {
  if (!isDesktop() || !path) return false
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('reveal_project_dir', { path })
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
 * 桌面模式下 sidecar 优先绑上次记住的端口（ADR 0108），但端口被占时本次仍会换一个，
 * 那一次前端 localStorage 的偏好就读不到——壳记的那份不随 origin 走，而它必须知道
 * 哪次是真正的选择，否则一次「跟随系统」的汇报就把用户选过的语言洗掉了。
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
/*  窗框：系统标题栏 / 菜单栏与页面顶栏融成一片（2026-10-13）                        */
/* -------------------------------------------------------------------------- */

/**
 * 主窗口的窗框形态，由壳在建窗口时注入（`src-tauri/src/window_chrome.rs` 的 `MARKER_GLOBAL`
 * 与两个取值，两侧严格同源，`tests/test_desktop_window_chrome.py` 看护）：
 *
 * - `overlay`（macOS）：系统标题栏透明叠在页面上，红绿灯在页面顶栏那一行——顶栏让出左侧
 *   `TRAFFIC_LIGHTS_INSET`，空白处是拖动区。
 * - `menu-button`（Windows）：菜单栏藏起来，页面顶栏给一个「菜单」按钮弹出同一份菜单；
 *   标题栏底色随页面主题。
 * - `native`：浏览器、远程实例窗口（不注入标记）、以及不认这个标记的旧壳——一切照旧。
 */
export type WindowChrome = 'overlay' | 'menu-button' | 'native'

export function windowChrome(): WindowChrome {
  if (!isDesktop()) return 'native'
  const v = (window as unknown as Record<string, unknown>).__TAVOTTO_WINDOW_CHROME__
  return v === 'overlay' || v === 'menu-button' ? v : 'native'
}

/**
 * `overlay` 下顶栏左侧给红绿灯让出的宽度（CSS 像素）。与壳的 `TRAFFIC_LIGHTS`（关闭钮 x = 16，
 * 三颗按钮右缘实测在 76）配套，再留 12：主页胶囊的悬停底色不贴着绿灯。
 */
export const TRAFFIC_LIGHTS_INSET = 88

/**
 * 拖动区标记（Tauri 的 `data-tauri-drag-region`）：`deep` = 子树里非交互的地方都能拖，按钮 /
 * 输入框 / 带 tabindex 的元素照常点击。只在 `overlay` 下给——别的窗框有系统标题栏可拖。
 */
export function dragRegionProps(mode: 'deep' | 'self' = 'deep'): Record<string, string> {
  if (windowChrome() !== 'overlay') return {}
  return { 'data-tauri-drag-region': mode === 'deep' ? 'deep' : '' }
}

/**
 * Windows 顶栏「菜单」按钮：在 (x, y)（页面 CSS 像素，一般是按钮左下角）弹出应用菜单。
 * 选中的项照旧经 `tavotto:menu` 回来（`useDesktopMenu`）。不在 `menu-button` 窗框里返回 false。
 */
export async function showDesktopAppMenu(x: number, y: number): Promise<boolean> {
  if (windowChrome() !== 'menu-button') return false
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('show_app_menu', { x, y })
    return true
  } catch {
    return false
  }
}

/** `rgb(r, g, b)` / `rgba(...)` / `#rrggbb` → `#rrggbb`；认不出来回 null */
export function cssColorToHex(value: string): string | null {
  const v = value.trim().toLowerCase()
  if (/^#[0-9a-f]{6}$/.test(v)) return v
  const m = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*[\d.]+\s*)?\)$/.exec(v)
  if (!m) return null
  const parts = m.slice(1, 4).map(Number)
  if (parts.some((n) => n > 255)) return null
  return '#' + parts.map((n) => n.toString(16).padStart(2, '0')).join('')
}

/**
 * Windows：系统标题栏与页面顶栏同色（底色 / 文字色，`#rrggbb`）。Windows 10 上壳照收、系统不认，
 * 标题栏维持原样。不在 `menu-button` 窗框里返回 false。
 */
export async function setDesktopTitlebarColors(background: string, text: string): Promise<boolean> {
  if (windowChrome() !== 'menu-button') return false
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('set_titlebar_colors', { background, text })
    return true
  } catch {
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
 * 检查与下载都走壳自己的命令（`desktop_update_check` / `desktop_update_install`，ADR 0121）：
 * 国内镜像与 GitHub 按顺序尝试，下载失败也会换来源重来——这些发生在 Rust 侧，前端只看到
 * 「查到了 / 装上了 / 失败了」和实际用的来源。**不再直接用 `@tauri-apps/plugin-updater`**
 * （它的多 endpoints 只覆盖 check）。
 *
 * 更新包的签名由壳里的公钥校验（tauri.conf.json 的 plugins.updater.pubkey），
 * 私钥只在 CI 里；来源不影响信任，校验不过当场失败，装不上去。
 */
export type UpdateSource = 'mirror' | 'github'

export interface DesktopUpdateInfo {
  version: string
  notes?: string
  /** Release 里写的发布时间，原样透出，不在前端解析格式 */
  date?: string
  /** 这一版是从哪个来源查到的（只放折叠详情里，不进主文案） */
  source?: UpdateSource
}

/** 壳回报的单个来源失败记录（只有闭集枚举，不带原始错误文本） */
export interface UpdateAttempt {
  source: UpdateSource
  stage: 'check' | 'download' | 'install'
  kind: string
}

/**
 * 更新失败。`message` 是给人看的一句话（已翻译）；`code` 与 `attempts` 是稳定的机器可读部分，
 * 进「技术详情」，不当句子显示。
 */
export class DesktopUpdateError extends Error {
  readonly code: string
  readonly attempts: UpdateAttempt[]
  constructor(code: string, message: string, attempts: UpdateAttempt[] = []) {
    super(message)
    this.name = 'DesktopUpdateError'
    this.code = code
    this.attempts = attempts
  }
}

function toUpdateError(e: unknown, fallbackKey: 'update.desktopCheckFailed' | 'update.desktopInstallFailed') {
  const raw = (e && typeof e === 'object' ? e : {}) as { code?: unknown; attempts?: unknown }
  const code = typeof raw.code === 'string' ? raw.code : typeof e === 'string' ? e : 'unknown'
  const attempts = Array.isArray(raw.attempts) ? (raw.attempts as UpdateAttempt[]) : []
  // 壳回的是稳定 code；ACL 拒绝 / 老壳没有这个命令时回的是别的字符串——一律走通用句子，
  // **不把它当句子显示给用户**
  const message =
    code === 'no_pending_update'
      ? t('update.noPendingUpdate', { ns: 'errors' })
      : code === 'busy'
        ? t('update.busy', { ns: 'errors' })
        : t(fallbackKey, { ns: 'errors' })
  return new DesktopUpdateError(code, message, attempts)
}

/** check() 拿到的版本——下载安装要的是壳里留着的同一个句柄，不能到时候重新查一次 */
let pendingUpdate: DesktopUpdateInfo | null = null

/**
 * 本次窗口会话里最近一次应用内更新的结果（只放闭集枚举与稳定码，不放错误文本），给「发送问题反馈」的
 * `detail-shell.json` 用（ADR 0118 修订）：走的是国内镜像还是 GitHub、有没有换来源重试。没发生过 = 不写这个键。
 * 纯内存；壳没有把「上一次更新结果」存盘，所以重启之后这里是空的，包里如实标 unknown。
 */
export interface UpdateOutcome {
  check?: { at: number; ok: boolean; found?: boolean; source?: UpdateSource; code?: string; attempts?: UpdateAttempt[] }
  install?: { at: number; ok: boolean; source?: UpdateSource; code?: string; attempts?: UpdateAttempt[] }
}
let updateOutcome: UpdateOutcome = {}
export function getUpdateOutcome(): UpdateOutcome | null {
  return updateOutcome.check || updateOutcome.install ? { ...updateOutcome } : null
}

/**
 * 查有没有新版。没有新版返回 null；**浏览器模式也返回 null**——那条路由
 * Python updater 负责（/api/update/*），两条不能同时插手。
 */
export async function checkDesktopUpdate(): Promise<DesktopUpdateInfo | null> {
  if (!isDesktop()) return null
  pendingUpdate = null
  const { invoke } = await import('@tauri-apps/api/core')
  let found: DesktopUpdateInfo | null
  try {
    // 界面语言交给壳参与来源顺序（简体中文界面 → 镜像优先）
    found = await invoke<DesktopUpdateInfo | null>('desktop_update_check', { uiLang: currentLocale() })
  } catch (e) {
    const err = toUpdateError(e, 'update.desktopCheckFailed')
    updateOutcome = { ...updateOutcome, check: { at: Date.now(), ok: false, code: err.code, attempts: err.attempts } }
    throw err
  }
  updateOutcome = { ...updateOutcome, check: { at: Date.now(), ok: true, found: !!found, source: found?.source } }
  if (!found) return null
  pendingUpdate = {
    version: found.version,
    notes: found.notes ?? undefined,
    date: found.date ?? undefined,
    source: found.source,
  }
  return pendingUpdate
}

/**
 * 下载并安装上一次查到的那一版。onProgress 收到 0–1 的进度；服务端没给
 * Content-Length 时收到 null——进度条该显示成不确定态，而不是假装卡在
 * 某个百分比上。**换来源重下时壳会再发一次 Started，进度随之归零。**
 * 返回实际用的来源。
 *
 * 没有句柄就抛，不在这里偷偷补一次 check：用户看到的版本号与真正装上去的
 * 那一版必须是同一个。
 */
export async function installDesktopUpdate(
  onProgress?: (fraction: number | null) => void,
): Promise<UpdateSource> {
  if (!pendingUpdate) throw new Error(t('update.noPendingUpdate', { ns: 'errors' }))
  const { invoke, Channel } = await import('@tauri-apps/api/core')
  let total = 0
  let got = 0
  const channel = new Channel<{ event: string; data?: { contentLength?: number | null; chunkLength?: number } }>()
  channel.onmessage = (e) => {
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
  }
  let source: UpdateSource
  try {
    source = await invoke<UpdateSource>('desktop_update_install', { onEvent: channel })
  } catch (e) {
    const err = toUpdateError(e, 'update.desktopInstallFailed')
    updateOutcome = { ...updateOutcome, install: { at: Date.now(), ok: false, code: err.code, attempts: err.attempts } }
    throw err
  }
  updateOutcome = { ...updateOutcome, install: { at: Date.now(), ok: true, source } }
  pendingUpdate = null
  return source
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
  updateOutcome = {}
}
