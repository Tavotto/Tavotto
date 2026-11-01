/**
 * 期刊目录的**前端持有者**（ADR 0155 §4b）：选择器与出处卡读目录只经这里。
 *
 * 取用顺序是 服务端目录（验签、校验通过）→ 本地缓存 → 内置种子。服务端拉取与验签是另一个 PR；
 * 这里先把「本地缓存」这一档做成一个槽：`tavotto.journalCatalog` 里存着一份整份通过 `loadCatalog` 校验的
 * 目录就用它，**校验不过整份弃用、退回种子**（不做部分采信）。远程 PR 把拉到的目录交给 `setCatalog`，
 * 同一道校验、同一个槽，不另起第二条读取路径。
 */
import { create } from 'zustand'
import { JournalCatalogError, loadCatalog, type JournalCatalog } from '@/lib/journals'

export const JOURNAL_CATALOG_CACHE_KEY = 'tavotto.journalCatalog'

function readCache(): JournalCatalog | null {
  try {
    const raw = window.localStorage.getItem(JOURNAL_CATALOG_CACHE_KEY)
    return raw ? loadCatalog(JSON.parse(raw)) : null
  } catch {
    return null
  }
}

interface JournalCatalogState {
  /** 生效的目录：缓存通过校验就是缓存，否则是内置种子 */
  catalog: JournalCatalog
  /** 交一份新目录；校验失败返回错误，目录原样不动 */
  setCatalog: (raw: unknown) => JournalCatalogError | null
}

export const useJournalCatalogStore = create<JournalCatalogState>((set) => ({
  catalog: readCache() ?? loadCatalog(),
  setCatalog: (raw) => {
    try {
      const catalog = loadCatalog(raw)
      try {
        window.localStorage.setItem(JOURNAL_CATALOG_CACHE_KEY, JSON.stringify(raw))
      } catch {
        /* 存不下不影响本次使用 */
      }
      set({ catalog })
      return null
    } catch (e) {
      if (e instanceof JournalCatalogError) return e
      throw e
    }
  },
}))
