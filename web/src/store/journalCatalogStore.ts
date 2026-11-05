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
export const BUNDLED_ASSET_BASE = '/assets/journal-catalog/'

/** 远程目录的资产根：快照图经本机后端取（验过哈希、带设备令牌，webview 不直连远端；ADR 0155 §10）。 */
export const REMOTE_ASSET_BASE = '/api/journal-catalog/assets/'
const CACHE_ASSET_KEY = 'tavotto.journalCatalog.assetBase'

/** 快照图的完整地址；`file` 已由目录校验保证是 `snapshots/<sha256>.webp`（不含路径与 URL）。 */
export const snapshotUrl = (base: string, file: string): string => base + file

function readCache(): { catalog: JournalCatalog; assetBase: string } | null {
  try {
    const raw = window.localStorage.getItem(JOURNAL_CATALOG_CACHE_KEY)
    if (!raw) return null
    const base = window.localStorage.getItem(CACHE_ASSET_KEY)
    return {
      catalog: loadCatalog(JSON.parse(raw)),
      assetBase: base === REMOTE_ASSET_BASE ? REMOTE_ASSET_BASE : BUNDLED_ASSET_BASE,
    }
  } catch {
    return null
  }
}

const initial = readCache()

interface JournalCatalogState {
  /** 生效的目录：缓存通过校验就是缓存，否则是内置种子 */
  catalog: JournalCatalog
  /** 快照图的资产根（见 `BUNDLED_ASSET_BASE`） */
  assetBase: string
  /**
   * 远程目录里「已经能直接用」的刊 id（种子里的 + 已下载的）；`null` = 不分（种子目录，或还没同步过远程索引）。
   * 不在其中的刊在选择器里照常可见，选中时才向服务端下载它的标准（服务端按设备令牌对该刊首次读取计数）。
   */
  loaded: ReadonlySet<string> | null
  /** 交一份新目录（可带它的资产根）；校验失败返回错误，目录原样不动 */
  setCatalog: (raw: unknown, assetBase?: string) => JournalCatalogError | null
  setLoaded: (ids: readonly string[] | null) => void
}

export const useJournalCatalogStore = create<JournalCatalogState>((set) => ({
  catalog: initial?.catalog ?? loadCatalog(),
  assetBase: initial?.assetBase ?? BUNDLED_ASSET_BASE,
  loaded: null,
  setLoaded: (ids) => set({ loaded: ids ? new Set(ids) : null }),
  setCatalog: (raw, assetBase = BUNDLED_ASSET_BASE) => {
    try {
      const catalog = loadCatalog(raw)
      try {
        window.localStorage.setItem(JOURNAL_CATALOG_CACHE_KEY, JSON.stringify(raw))
        window.localStorage.setItem(CACHE_ASSET_KEY, assetBase)
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
