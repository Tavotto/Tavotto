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

/**
 * 「目录资产根」：快照图的 `file`（`snapshots/<sha256>.webp`）相对它解析。内置种子 = 随前端打进包里的
 * 目录；远程目录由拉取它的 PR 给出那一版的资产根（ADR 0155 §6）。末尾带 `/`。
 */
export const BUNDLED_ASSET_BASE = '/journal-catalog/'

/** 快照图的完整地址；`file` 已由目录校验保证是 `snapshots/<sha256>.webp`（不含路径与 URL）。 */
export const snapshotUrl = (base: string, file: string): string => base + file

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
  /** 快照图的资产根（见 `BUNDLED_ASSET_BASE`） */
  assetBase: string
  /** 交一份新目录（可带它的资产根）；校验失败返回错误，目录原样不动 */
  setCatalog: (raw: unknown, assetBase?: string) => JournalCatalogError | null
}

export const useJournalCatalogStore = create<JournalCatalogState>((set) => ({
  catalog: readCache() ?? loadCatalog(),
  assetBase: BUNDLED_ASSET_BASE,
  setCatalog: (raw, assetBase = BUNDLED_ASSET_BASE) => {
    try {
      const catalog = loadCatalog(raw)
      try {
        window.localStorage.setItem(JOURNAL_CATALOG_CACHE_KEY, JSON.stringify(raw))
      } catch {
        /* 存不下不影响本次使用 */
      }
      set({ catalog, assetBase })
      return null
    } catch (e) {
      if (e instanceof JournalCatalogError) return e
      throw e
    }
  },
}))
