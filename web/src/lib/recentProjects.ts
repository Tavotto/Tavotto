/**
 * 最近项目列表的纯计算：同名区分、失效分组、筛选、「像不像一条路径」。
 *
 * 这些判据被 Project Picker 与顶栏的项目切换器**共用**（T02）：同一个用户
 * 在两处看到的「这个 figs 是哪个 figs」必须是同一个答案，所以只在这里算一份。
 * 一个字都不碰后端：`RecentProject` 就是 `/api/projects/recent` 回来的形状。
 */

export interface RecentLike {
  path: string
  name: string
  exists: boolean
  /** 教程副本：列表里显示「教程」而不是路径，所以不参与同名区分 */
  tutorial?: boolean
}

/** 按 `/` 或 `\` 切段，去掉空段（`/a/b/` → `['a','b']`） */
export function pathSegments(path: string): string[] {
  return path.split(/[\\/]+/).filter(Boolean)
}

/** 一条路径本来用的分隔符：Windows 路径全是反斜杠时沿用它，否则用 `/` */
export function separatorOf(path: string): string {
  return path.includes('\\') && !path.includes('/') ? '\\' : '/'
}

/**
 * 同名项目的**可辨认后缀**：`path → 提示`。
 *
 * 一组同名条目里，取**最短**的尾段数 k（至少两段：目录名本身 + 父目录），
 * 使得组内每一条的最后 k 段互不相同；提示写成 `…/parent/name`。某条路径
 * 本来就不够 k 段时它显示整条路径（没有省略号）。只有一条的名字没有提示：
 * 单独一个「figs」不需要靠父目录来认。
 *
 * 为什么是「组内最短能区分」而不是「固定显示上一级」：截图里六个 `figs`
 * 的上一级全叫 `pytest-of-…`，只显示上一级等于什么都没显示。
 */
export function disambiguateRecent(entries: readonly RecentLike[]): Map<string, string> {
  const groups = new Map<string, RecentLike[]>()
  for (const e of entries) {
    if (e.tutorial) continue
    const g = groups.get(e.name)
    if (g) g.push(e)
    else groups.set(e.name, [e])
  }
  const out = new Map<string, string>()
  for (const group of groups.values()) {
    if (group.length < 2) continue
    const segs = group.map((e) => pathSegments(e.path))
    const longest = Math.max(...segs.map((s) => s.length))
    for (let k = 2; k <= longest; k++) {
      const tails = segs.map((s) => s.slice(-k).join('/'))
      if (new Set(tails).size !== tails.length) continue
      group.forEach((e, i) => {
        const sep = separatorOf(e.path)
        const tail = segs[i].slice(-k).join(sep)
        out.set(e.path, segs[i].length <= k ? e.path : `…${sep}${tail}`)
      })
      break
    }
    // 走到这里还没 break 只可能是两条路径完全相同——最近列表按路径去重，
    // 这种条目不存在；万一有，就让它们各显示整条路径
    for (const e of group) if (!out.has(e.path)) out.set(e.path, e.path)
  }
  return out
}

/**
 * 输入框里的这段文字是「要打开的路径」还是「筛选词」。
 * 只认绝对路径与 `~`：相对路径打开出来的目录取决于后端进程的 cwd，
 * 用户看不见那个 cwd，所以不当路径处理。
 */
export function looksLikePath(text: string): boolean {
  const s = text.trim()
  if (!s) return false
  return /^(\/|~(?:[\\/]|$)|[A-Za-z]:[\\/]|\\\\)/.test(s)
}

/**
 * 名字或路径里包含筛选词（不分大小写）；空词匹配全部。
 * 教程副本只按名字匹配：它的路径在列表里根本不显示，按一段用户看不见的
 * 文字命中会让筛选结果显得莫名其妙。
 */
export function matchesRecent(entry: RecentLike, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  if (entry.name.toLowerCase().includes(q)) return true
  return !entry.tutorial && entry.path.toLowerCase().includes(q)
}

/**
 * 按筛选词过滤，再按「目录还在不在」分成两组；各组保持原来的最近顺序。
 * 失效项单独成组是为了不让它们把可打开的项目挤到视口外（截图 74：六个
 * 失效的 figs 占了半屏）。
 */
export function splitRecent<T extends RecentLike>(
  entries: readonly T[],
  query = '',
): { available: T[]; missing: T[] } {
  const available: T[] = []
  const missing: T[] = []
  for (const e of entries) {
    if (!matchesRecent(e, query)) continue
    if (e.exists) available.push(e)
    else missing.push(e)
  }
  return { available, missing }
}

/**
 * 输入框回车该打开什么：像路径就打开这条路径；否则筛选后**恰好一个**可打开
 * 的项目时打开它；其余情况没有目标（按钮禁用）。失效项不算候选——它打不开。
 */
export function submitTargetFor<T extends RecentLike>(
  query: string,
  available: readonly T[],
): { kind: 'path'; path: string } | { kind: 'recent'; entry: T } | null {
  const s = query.trim()
  if (!s) return null
  if (looksLikePath(s)) return { kind: 'path', path: s }
  return available.length === 1 ? { kind: 'recent', entry: available[0] } : null
}
