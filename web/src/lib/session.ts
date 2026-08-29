/**
 * 本标签页绑定的项目。
 *
 * 用 sessionStorage 而不是 localStorage 是这件事的全部关键：sessionStorage
 * **按标签页隔离**，所以「不同标签页开不同项目目录」自然成立——A 标签页切
 * 项目不会把 B 标签页正在编辑的图库换掉。后端一个进程同时端着多个项目，
 * 请求靠 pj 认领（app.py 的 _request_ctx）。
 *
 * pj 必须同时走两条路：
 *   * 请求头 X-Tavotto-Project —— fetch 统一带上；
 *   * 查询参数 pj —— `<img src>` 与 EventSource 加不了请求头。
 * 只做一条会让一半 API 串到别的项目上（图能出、但出的是另一个图库的）。
 */
const KEY = 'tavotto:project'

let current: string | null = boot()

/**
 * 地址栏里的 `?pj=` 优先于本标签页记着的项目——「在新标签页打开」就是靠它
 * 把项目传过去的。认下之后立刻从地址栏抹掉：项目归属存在 sessionStorage 里，
 * 地址栏留着它只会在用户之后手动切项目时变成一个撒谎的 URL。
 */
function boot(): string | null {
  let stored: string | null = null
  try {
    stored = window.sessionStorage.getItem(KEY)
  } catch {
    return null // 隐私模式下不可用：退化成「跟随后端默认项目」
  }
  try {
    const fromUrl = new URLSearchParams(window.location.search).get('pj')
    if (!fromUrl) return stored
    window.sessionStorage.setItem(KEY, fromUrl)
    const url = new URL(window.location.href)
    url.searchParams.delete('pj')
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
    return fromUrl
  } catch {
    return stored
  }
}

export function currentProjectId(): string | null {
  return current
}

export function setCurrentProjectId(id: string | null): void {
  current = id || null
  try {
    if (current) window.sessionStorage.setItem(KEY, current)
    else window.sessionStorage.removeItem(KEY)
  } catch {
    /* 存不下也不影响本次会话：current 在内存里 */
  }
}

/** 给任意 API 路径挂上 pj（已有查询串时追加）。 */
export function apiUrl(path: string): string {
  return apiUrlFor(path, current)
}

/** 给 fetch 的 init 补上项目请求头，保留调用方自己的 headers。 */
export function withProject(init?: RequestInit): RequestInit | undefined {
  return withProjectFor(init, current)
}

/* --------------------------------------------------------------------------
 * 显式指定项目的两个变体。
 *
 * 一次写入属于**排队那一刻**的那个项目，不属于"socket 打开那一刻碰巧是哪个"。
 * 两者之间可以隔着一次项目切换：`dropProject()` 先冲刷再忘掉 pj，而冲刷排出去
 * 的那次 PUT 若在 await 之后才真的发出，读到的 `current` 已经是 null——这份
 * 自动保存就落进了后端的默认项目。落错项目的文档在原项目里表现为"没保存"。
 *
 * 所以凡是「排队 → 稍后发出」的写入都走这两个，把项目跟着载荷一起带走。
 * -------------------------------------------------------------------------- */

export function apiUrlFor(path: string, pj: string | null): string {
  if (!pj) return path
  return `${path}${path.includes('?') ? '&' : '?'}pj=${encodeURIComponent(pj)}`
}

export function withProjectFor(
  init: RequestInit | undefined,
  pj: string | null,
): RequestInit | undefined {
  if (!pj) return init
  return { ...init, headers: { ...(init?.headers ?? {}), 'X-Tavotto-Project': pj } }
}
