/**
 * 主页「导入我的脚本」/ 拖放区的纯判据。
 *
 * **项目是目录，不是脚本**（`/api/projects/open` 只收目录，`app.open_project`）：
 * 「导入一个 .py」= 打开它所在的文件夹，脚本与它生成的图由注册表的静态扫描
 * 认出来（`engine/discover.build_draft`）。这里只负责把「用户给的东西」变成
 * 「该打开哪个目录」，不发请求。
 *
 * 拖放拿不拿得到路径取决于宿主，不取决于我们：
 *   * 浏览器与桌面壳（WKWebView / WebView2）都**不**把本机文件的绝对路径交给页面，
 *     `DataTransfer.files` 里只有名字与内容；桌面壳为了画布里的 HTML5 拖放关掉了
 *     Tauri 自己的拖放事件（`src-tauri/src/main.rs` 的 `disable_drag_drop_handler`），
 *     那条能带路径的通道也不在。
 *   * 个别宿主会附一条 `file://` 的 `text/uri-list`：有就直接用。
 * 拿不到路径时不猜、不上传副本（脚本里的相对路径会断）：退回选择器，让用户点一下。
 */

/** 放下来的东西该怎么处理 */
export type DropTarget =
  /** 拿到了路径：打开 `folder`（放下的是 .py 就是它的上级目录，放下的是目录就是它自己） */
  | { kind: 'path'; folder: string }
  /** 是 .py，但宿主没给路径：退回选择器，并说出是哪个文件 */
  | { kind: 'no-path'; name: string }
  /** 放下的不是 .py（也不是带路径的目录） */
  | { kind: 'not-script'; name: string }
  /** 什么文件都没有（拖进来的是文字 / 链接） */
  | { kind: 'none' }

const isScriptName = (name: string) => /\.py$/i.test(name.trim())

/** 路径的上一级；分隔符两种都认（Windows 路径经 file URI 解出来是正斜杠，手输的可能是反斜杠） */
export function parentDir(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, '')
  const cut = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'))
  if (cut < 0) return trimmed
  if (cut === 0) return trimmed.slice(0, 1)
  const head = trimmed.slice(0, cut)
  // `C:\a.py` 的上级是 `C:\`，不是 `C:`（后者在 Windows 上是「C 盘的当前目录」）
  return /^[A-Za-z]:$/.test(head) ? `${head}${trimmed[cut]}` : head
}

/** 选择器 / 拖放给出的一条路径 → 该打开的目录。`.py` 取上级，其余原样（当目录打开） */
export function folderForPath(path: string): string {
  return isScriptName(path) ? parentDir(path) : path
}

/**
 * `file://` URI → 本机路径；不是 file URI 就 null。
 * `file:///C:/x/a.py` → `C:/x/a.py`；`file://server/share/a.py` → `//server/share/a.py`。
 */
export function pathFromFileUri(uri: string): string | null {
  let url: URL
  try {
    url = new URL(uri.trim())
  } catch {
    return null
  }
  if (url.protocol !== 'file:') return null
  let path: string
  try {
    path = decodeURIComponent(url.pathname)
  } catch {
    return null
  }
  if (/^\/[A-Za-z]:/.test(path)) path = path.slice(1)
  if (url.host) path = `//${url.host}${path}`
  return path || null
}

/** 只读 `DataTransfer` 里用得到的三样：jsdom 与各家宿主给的形状都满足它 */
export interface DropData {
  types: readonly string[]
  getData(format: string): string
  files: ArrayLike<{ name: string }>
}

/** 一次放下 → 怎么处理。只看第一个文件：一次导入一个脚本，多放的不猜用户要哪个 */
export function dropTargetOf(dt: DropData): DropTarget {
  if ([...dt.types].includes('text/uri-list')) {
    const first = dt
      .getData('text/uri-list')
      .split(/\r?\n/)
      .map((l) => l.trim())
      .find((l) => l && !l.startsWith('#'))
    const path = first ? pathFromFileUri(first) : null
    if (path) return { kind: 'path', folder: folderForPath(path) }
  }
  const file = dt.files.length > 0 ? dt.files[0] : null
  if (!file) return { kind: 'none' }
  return isScriptName(file.name) ? { kind: 'no-path', name: file.name } : { kind: 'not-script', name: file.name }
}

/** 拖着的东西里有没有文件（dragover 时只能看 types，看不到内容） */
export const dragHasFiles = (types: readonly string[]) =>
  [...types].includes('Files') || [...types].includes('text/uri-list')

/**
 * 同一次放下会从两条路到达主页：页面自己的 DOM `drop`（只有文件名），以及壳旁听到的
 * 系统拖放事件（带真实路径，ADR 0092）。两者先后不定，壳那条是异步 IPC。
 *
 * 能拿到真实路径的壳里，DOM 那条**先不降级**：等 `graceMs`，期间壳的事件到了就只用它；
 * 刚刚已经到过也不再降级；真没等到（旁听没装上、这次粘贴板里没路径）才走降级
 * （提示文件名 + 选择器）。浏览器 / 不支持的平台不经过这里，直接降级。
 */
export function createDropArbiter(opts: {
  graceMs: number
  now?: () => number
}) {
  const now = opts.now ?? (() => Date.now())
  let lastNative = Number.NEGATIVE_INFINITY
  let pending: ReturnType<typeof setTimeout> | null = null
  const cancel = () => {
    if (pending !== null) clearTimeout(pending)
    pending = null
  }
  return {
    /** 壳的事件到了：取消等待中的降级，照它办 */
    native(run: () => void) {
      lastNative = now()
      cancel()
      run()
    },
    /** 页面的 drop 到了：壳刚发过就什么都不做，否则等一会儿再降级 */
    dom(fallback: () => void) {
      if (now() - lastNative < opts.graceMs) return
      cancel()
      pending = setTimeout(() => {
        pending = null
        fallback()
      }, opts.graceMs)
    },
    dispose: cancel,
  }
}

