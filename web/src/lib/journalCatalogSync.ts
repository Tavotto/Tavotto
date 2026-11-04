/**
 * 远程期刊目录的前端一侧（ADR 0155 §4b / §10）：拉取、验签、缓存都在本机后端（`engine/journalremote.py`），
 * 这里只做两件事——启动时请后端同步一次**索引**（整份、服务端不计数），选中某本刊时请后端下载**这一本**的标准。
 *
 * 后端回来的目录交给 `journalCatalogStore.setCatalog`，同一道 `loadCatalog` 校验，**不过整份弃用**。
 * 所有失败都静默：不弹错、不抛，目录原样不动（退回缓存 / 种子）。额度用完 / 要联网的胶囊提示文案待用户过目，
 * 这里只返回结果，不自己加任何界面文字。
 */
import { currentLocale } from '@/i18n'
import { journalCatalogUi } from '@/lib/journalUi'
import type { JournalCatalog } from '@/lib/journals'
import { REMOTE_ASSET_BASE, useJournalCatalogStore } from '@/store/journalCatalogStore'

export type EnsureState = 'ready' | 'quota' | 'offline' | 'unknown' | 'rejected'

export interface EnsureOutcome {
  state: EnsureState
  /** 额度用完时建议等待的秒数（服务端给的）；没有则 null */
  retryAfterS: number | null
}

interface SyncReply {
  fetch?: string
  rev?: number | null
  catalog?: unknown
  loaded?: unknown
}

interface EnsureReply {
  state?: string
  retry_after_s?: number | null
  bundle?: { journal_id: string; journal: unknown; publisher_id: string; publisher: unknown } | null
}

async function post(path: string, body: unknown): Promise<unknown> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

/** 启动时静默同步一次索引。入口关着（`journalCatalogUi()` 为假）时一个请求都不发。 */
export async function syncJournalCatalog(force = false): Promise<void> {
  if (!journalCatalogUi()) return
  const store = useJournalCatalogStore.getState()
  try {
    const have = store.catalog.catalog_rev
    const reply = (await post('/api/journal-catalog/sync', { have, force, locale: currentLocale() })) as SyncReply
    const loaded = Array.isArray(reply.loaded) ? reply.loaded.filter((x): x is string => typeof x === 'string') : null
    // 倒退（后端的缓存比本机已有的旧）不采信；没有目录本体 = 无更新
    if (reply.catalog && typeof reply.rev === 'number' && reply.rev >= have) {
      // `loaded` 只在目录本体也被采信时才记：它报的刊必须是本机手里有内容的刊（目录没被采信 = 手里还是旧的）
      if (store.setCatalog(reply.catalog, REMOTE_ASSET_BASE)) return
    }
    if (loaded) store.setLoaded(loaded)
  } catch {
    /* 离线 / 后端没响应：保持现状 */
  }
}

/**
 * 保证某本刊的标准在本机可用，选中它之前调一次。种子里的刊和已下载过的刊不发任何网络请求
 * （后端本地命中；服务端也就不会计数）。成功时把这本刊并入当前目录。
 */
export async function ensureJournalReady(journalId: string): Promise<EnsureOutcome> {
  const store = useJournalCatalogStore.getState()
  if (!journalCatalogUi() || store.assetBase !== REMOTE_ASSET_BASE) {
    // 没用远程目录（入口关着，或目录还是种子）：行为与此前相同
    return { state: 'ready', retryAfterS: null }
  }
  if (store.loaded?.has(journalId)) return { state: 'ready', retryAfterS: null }
  try {
    const reply = (await post('/api/journal-catalog/journal', { id: journalId, locale: currentLocale() })) as EnsureReply
    const retryAfterS = typeof reply.retry_after_s === 'number' ? reply.retry_after_s : null
    if (reply.state === 'ready') {
      const b = reply.bundle
      if (b) {
        // await 之后重新读当前目录再并入：两次下载重叠时，先完成的那本不能被后完成的用旧快照盖掉
        const cur: JournalCatalog = useJournalCatalogStore.getState().catalog
        const merged = {
          ...cur,
          publishers: { ...cur.publishers, [b.publisher_id]: b.publisher },
          journals: { ...cur.journals, [b.journal_id]: b.journal },
        }
        // 并入后整份重新校验；不过则整包弃用（目录不动），按「被拒」处理
        if (store.setCatalog(merged, REMOTE_ASSET_BASE)) return { state: 'rejected', retryAfterS: null }
        const now = useJournalCatalogStore.getState()
        now.setLoaded([...(now.loaded ?? []), journalId])
      }
      return { state: 'ready', retryAfterS: null }
    }
    const state = (['quota', 'offline', 'unknown', 'rejected'] as const).find((s) => s === reply.state)
    return { state: state ?? 'offline', retryAfterS }
  } catch {
    return { state: 'offline', retryAfterS: null }
  }
}
