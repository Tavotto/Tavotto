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

import { t } from '@/i18n'
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
/** beta 分支专用开关（不合进 main） */
const TAVOTTO_BETA = true as boolean

export async function checkDesktopUpdate(): Promise<DesktopUpdateInfo | null> {
  // beta 分支专用：Tavotto Beta 不查更新——否则正式版一发，它会把自己「更新」成
  // 正式版的内容，却还装在 Tavotto Beta.app 这个路径下
  if (TAVOTTO_BETA) return null
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
