/**
 * 测试替身：助手草稿 / 待确认账本的**后端**（ADR 0132），语义与 `src/tavotto/engine/ai_history.py`
 * 的 `ai_drafts` / `ai_pending` 一致（按 (项目, fileId[, rid]) 隔离、全文覆盖、条件删除、`accept` 一个事务里
 * 判与删、20 KB 上限 413、每面板 8 条账本）。真后端的语义在 `tests/test_ai_drafts.py` 钉死；这里的替身只
 * 让前端用例能看见「后端此刻存着什么」，也能模拟失败。
 */
export interface DraftBackend {
  /** 命中草稿相关路由返回 Response，否则 null（调用方继续处理别的路径） */
  handle: (url: string, init?: RequestInit) => Response | null
  drafts: Map<string, string>
  pendings: Map<string, { rid: string; text: string; sent: unknown }[]>
  calls: { method: string; path: string; keepalive: boolean; body: Record<string, unknown> }[]
  /** 让后端「不可用」（fetch 抛网络错误） */
  setDown: (down: boolean) => void
  /** 直接读 / 写（模拟另一个标签页） */
  draft: (pj: string, file: string) => string | undefined
  pending: (pj: string, file: string) => string[]
  put: (pj: string, file: string, text: string) => void
}

const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })
const MAX = 20 * 1024
const key = (pj: string, f: string) => `${pj}\u0000${f}`

export function createDraftBackend(): DraftBackend {
  const drafts = new Map<string, string>()
  const pendings = new Map<string, { rid: string; text: string; sent: unknown }[]>()
  const calls: DraftBackend['calls'] = []
  let down = false
  const bytes = (t: string) => new TextEncoder().encode(t).length

  const handle = (url: string, init?: RequestInit): Response | null => {
    const u = new URL(url, 'http://x')
    if (!u.pathname.startsWith('/api/ai/drafts')) return null
    if (down) throw new TypeError('Failed to fetch')
    const hdr = (init?.headers ?? {}) as Record<string, string>
    const pj = hdr['X-Tavotto-Project'] ?? u.searchParams.get('pj') ?? ''
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {}
    calls.push({ method, path: u.pathname, keepalive: !!init?.keepalive, body })
    const m = /^\/api\/ai\/drafts\/pending\/([^/]+)(\/accept)?$/.exec(u.pathname)
    if (m) {
      const rid = decodeURIComponent(m[1])
      const file = String(body.file ?? u.searchParams.get('file') ?? '')
      const list = pendings.get(key(pj, file)) ?? []
      if (m[2]) {
        const p = list.find((x) => x.rid === rid)
        if (!p) return json({ cleared: false, draft_deleted: false })
        pendings.set(key(pj, file), list.filter((x) => x.rid !== rid))
        const del = drafts.get(key(pj, file)) === p.text
        if (del) drafts.delete(key(pj, file))
        return json({ cleared: true, draft_deleted: del })
      }
      if (method === 'PUT') {
        const next = [...list.filter((x) => x.rid !== rid), { rid, text: String(body.text), sent: body.sent }]
        pendings.set(key(pj, file), next.slice(-8))
        return json({ ok: true })
      }
      if (method === 'DELETE') {
        pendings.set(key(pj, file), list.filter((x) => x.rid !== rid))
        return json({ cleared: list.some((x) => x.rid === rid) })
      }
    }
    if (u.pathname === '/api/ai/drafts') {
      if (method === 'GET') {
        const file = u.searchParams.get('file') ?? ''
        const text = drafts.get(key(pj, file))
        return json({
          draft: text === undefined ? null : { text, revision: 1, updated_ms: 1 },
          pendings: (pendings.get(key(pj, file)) ?? []).map((p) => ({ ...p, created_ms: 1 })),
        })
      }
      const file = String(body.file ?? '')
      if (method === 'PUT') {
        const text = String(body.text)
        if (text === '' || bytes(text) > MAX) drafts.delete(key(pj, file))
        else drafts.set(key(pj, file), text)
        if (bytes(text) > MAX) return json({ error: 'too large', code: 'ai_draft_too_large', params: { limit_kb: 20 } }, 413)
        return json({ revision: 1 })
      }
      if (method === 'DELETE') {
        const del = drafts.get(key(pj, file)) === body.if_text
        if (del) drafts.delete(key(pj, file))
        return json({ deleted: del })
      }
    }
    return json({ error: 'nf' }, 404)
  }

  return {
    handle,
    drafts,
    pendings,
    calls,
    setDown: (d) => {
      down = d
    },
    draft: (pj, f) => drafts.get(key(pj, f)),
    pending: (pj, f) => (pendings.get(key(pj, f)) ?? []).map((p) => p.rid),
    put: (pj, f, t) => void drafts.set(key(pj, f), t),
  }
}
