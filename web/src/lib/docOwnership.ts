/**
 * 「这份排版属于哪个项目」的前端判据——**唯一出处**（#715 验收 P1）。
 *
 * 为什么要有它：稳定端口（#718，ADR 0108 §一）之后，同一台机器上先后打开的不同项目共用同一个
 * origin、同一份 localStorage。全局键 `tavotto.currentDoc` / `tavotto.docIndex` 因此跨项目存活：
 * 关掉项目 F、打开新项目 G，启动恢复（`restoreSession`）在 G 还没有记录时退回全局 `currentDoc`，
 * 把 F 的排版装进 G、再按 G 记成「G 上次开着的」并往 G 的目录里打时间线节点（Windows 真机验收，
 * 2026-10-01）。以前每次启动端口都变，localStorage 恰好每次都是空的，这条路从没走到过。
 *
 * 判据只依据本机「最近文档」索引里**写下那一刻**记的 `projectId`（`documentStore.flushAutosaveNow`
 * 按文档换进来那一刻的项目记，审计 T04）。三档：
 *
 *  - `string`：记着属于这个项目；
 *  - `null`：条目在、没记项目（写下时没开项目，或 T04 之前的旧条目）；
 *  - `undefined`：索引里没有这份（换了 origin、被 12 条上限挤掉）。
 *
 * 「不知道」**不折成**「属于当前项目」，也不折成「属于别的项目」：`isForeignDocument` 只在**确知**
 * 属于别的项目时为真；需要正面证据的地方（全局 `currentDoc` 的退路）自己判 `=== pj`。
 * 后端那一侧的同一条判据是 `engine/layoutsession.owner_conflict`（槽位归属 `owners`），两侧互为纵深，
 * 谁先拦下都算数。
 */

/** 本机「最近文档」索引的键。`documentStore` 读写它；这里只读归属。 */
export const DOC_INDEX_KEY = 'tavotto.docIndex'

/** 本机索引里这份文档记在哪个项目名下（三档见模块注释）。 */
export function recordedProjectOf(docId: string): string | null | undefined {
  try {
    const raw = localStorage.getItem(DOC_INDEX_KEY)
    const arr = raw ? (JSON.parse(raw) as unknown) : null
    if (!Array.isArray(arr)) return undefined
    for (const e of arr) {
      if (!e || typeof e !== 'object' || (e as { id?: unknown }).id !== docId) continue
      const pid = (e as { projectId?: unknown }).projectId
      return typeof pid === 'string' && pid ? pid : null
    }
    return undefined
  } catch {
    return undefined
  }
}

/**
 * 这份排版**确知**属于别的项目（`pj` 为空 = 没开项目：记着任何项目都算别的）。
 * 恢复、绑定「上次开着的」、迁移本机缓存之前都要过这一道。
 */
export function isForeignDocument(docId: string, pj: string | null): boolean {
  const owner = recordedProjectOf(docId)
  return typeof owner === 'string' && owner !== pj
}
