/**
 * 测试替身：助手草稿 / 待确认账本的**后端**（ADR 0132），语义与 `src/tavotto/engine/ai_history.py`
 * 的 `ai_drafts` / `ai_pending` 一致（按 (项目, fileId[, rid]) 隔离、全文覆盖、条件删除、`accept` 一个事务里
 * 判与删、20 KB 上限 413、每面板 8 条账本）。真后端的语义在 `tests/test_ai_drafts.py` 钉死；这里的替身只
 * 让前端用例能看见「后端此刻存着什么」，也能模拟失败。
 */
import { textHash } from '@/lib/aiDraft'
import { sameScript } from '@/store/aiStore'

export interface DraftBackend {
  /** 命中草稿相关路由返回 Response，否则 null（调用方继续处理别的路径） */
  handle: (url: string, init?: RequestInit) => Response | Promise<Response> | null
  drafts: Map<string, string>
  pendings: Map<string, { rid: string; text: string; sent: unknown; script?: string | null; draftStamp?: number | null }[]>
  calls: { method: string; path: string; keepalive: boolean; body: Record<string, unknown> }[]
  /** 让后端「不可用」（fetch 抛网络错误） */
  setDown: (down: boolean) => void
  /** 扣住草稿 / 账本的读（GET）：对账「还在读」的那一段；返回放行函数 */
  holdReads: (scope?: 'all' | 'draft' | 'list') => () => void
  /** 扣住草稿的 PUT（请求「在途」：后端还没收到）；返回放行函数 */
  holdDraftPuts: () => () => void
  /** 只扣住**下一个**草稿 PUT（之后的照常落地）：模拟「A 在途、B 先落地」；返回放行函数 */
  holdNextDraftPut: () => () => void
  /** 另一个标签页以指定写入戳写草稿（走与 PUT 相同的接受 / 拒绝规则）；回是否被接受 */
  putStamped: (pj: string, file: string, text: string, stamp: number) => boolean
  /** 只让草稿的 PUT 失败（回 500）；别的照常 */
  setDraftPutFails: (fails: boolean) => void
  /** 只让「读项目级账本列表」失败（草稿的读照常成功） */
  setPendingListFails: (fails: boolean) => void
  /** 只让「读这个文件的草稿」失败（项目级账本列表照常成功） */
  setDraftGetFails: (fails: boolean) => void
  /** 这个 rid 已被后端真实登记（旧请求其实到达了）：带它的 `supersedes` 一律 409 `ai_supersede_conflict`，整次写入不生效 */
  markRegistered: (rid: string) => void
  /** 回了 409 `ai_pending_unresolved` 之后立刻调用（带挡路的 rid）：模拟「回 409 之后、本页再读之前」别的标签页做了什么 */
  afterPendingUnresolved: (fn: ((rids: string[]) => void) | null) => void
  /** 回了 409 `ai_supersede_conflict` 之后立刻调用：模拟「回 409 之后、本页处理之前」发生了什么（如切项目） */
  afterSupersedeConflict: (fn: (() => void) | null) => void
  /** 只让「写账本」失败（PUT pending 回 500），别的照常 */
  setPendingWriteFails: (fails: boolean) => void
  /** 模拟「这个面板未解决的提交已满」：新 rid 一律 409 `ai_pending_limit`（params.count = 传入的数）；null = 不限 */
  setPendingLimit: (limit: number | null) => void
  /** 接下来 n 次写账本：**写入成功，但响应丢了**（客户端收到网络错误） */
  losePendingResponses: (n: number) => void
  /** 模拟后端 `/api/ai/run` 明确拒绝时的服务端清理：删掉这个 rid 的待确认行（客户端没有 DELETE） */
  dropRejectedPending: (rid: string) => void
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
  const pendings = new Map<
    string,
    { rid: string; text: string; sent: unknown; script?: string | null; draftStamp?: number | null }[]
  >()
  let pendingWriteFails = false
  let pendingLimit: number | null = null
  let loseNextPendingResponses = 0
  let pendingListFails = false
  let draftGetFails = false
  const registered = new Set<string>()
  let onUnresolved: ((rids: string[]) => void) | null = null
  let onSupersedeConflict: (() => void) | null = null
  let draftPutFails = false
  let hold: Promise<void> | null = null
  let holdNext: Promise<void> | null = null
  const stamps = new Map<string, number>()
  let readHold: Promise<void> | null = null
  let readHoldScope: 'all' | 'draft' | 'list' = 'all'
  const calls: DraftBackend['calls'] = []
  let down = false
  const bytes = (t: string) => new TextEncoder().encode(t).length

  const handle = (url: string, init?: RequestInit): Response | Promise<Response> | null => {
    const u = new URL(url, 'http://x')
    if (!u.pathname.startsWith('/api/ai/drafts')) return null
    if (down) throw new TypeError('Failed to fetch')
    const hdr = (init?.headers ?? {}) as Record<string, string>
    const pj = hdr['X-Tavotto-Project'] ?? u.searchParams.get('pj') ?? ''
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {}
    const entry = { method, path: u.pathname, keepalive: !!init?.keepalive, body }
    calls.push(entry)
    const isList = u.pathname === '/api/ai/drafts/pending'
    if (method === 'GET' && readHold && (readHoldScope === 'all' || (readHoldScope === 'list') === isList)) {
      // 读被扣住：放行后再走一遍（不重复记 calls）
      return readHold.then(() => {
        calls.splice(calls.indexOf(entry), 1) // 放行后会重走一遍 handle（再记一次），这里去掉被扣住时记的那条
        return handle(url, init) as Response
      })
    }
    if (method === 'GET' && !isList && draftGetFails) return json({ error: 'boom', code: 'internal_error' }, 500)
    if (u.pathname === '/api/ai/drafts/pending' && method === 'GET') {
      if (pendingListFails) return json({ error: 'boom', code: 'internal_error' }, 500)
      const prefix = key(pj, '')
      const all = [...pendings.entries()]
        .filter(([k]) => k.startsWith(prefix))
        .flatMap(([k, list]) => list.map((p) => ({ file: k.slice(prefix.length), ...p, script: p.script ?? null, created_ms: 1 })))
      return json({ pendings: all })
    }
    const m = /^\/api\/ai\/drafts\/pending\/([^/]+)(\/accept)?$/.exec(u.pathname)
    if (m) {
      const rid = decodeURIComponent(m[1])
      const file = String(body.file ?? u.searchParams.get('file') ?? '')
      const list = pendings.get(key(pj, file)) ?? []
      if (m[2]) {
        const p = list.find((x) => x.rid === rid)
        if (!p) return json({ cleared: false, draft_deleted: false })
        pendings.set(key(pj, file), list.filter((x) => x.rid !== rid))
        // 与后端 `accept_pending` 同语义：只有账本记了提交时的草稿戳、且草稿当前的戳仍等于它（自提交后没被改过）才删，不比文本
        const del =
          p.draftStamp != null && drafts.has(key(pj, file)) && stamps.get(key(pj, file)) === p.draftStamp
        if (del) {
          drafts.delete(key(pj, file))
          stamps.set(key(pj, file), (stamps.get(key(pj, file)) ?? 0) + 1) // 墓碑只 +1：取代的恰好是被删的那一版，之后的新编辑不被挡掉
        }
        return json({ cleared: true, draft_deleted: del })
      }
      if (method === 'PUT') {
        if (pendingWriteFails) return json({ error: 'boom', code: 'internal_error' }, 500)
        if (pendingLimit !== null && !list.some((x) => x.rid === rid))
          return json({ error: 'full', code: 'ai_pending_limit', params: { count: pendingLimit } }, 409)
        // `supersedes`：与后端一致，在写新行的同一个「事务」里删旧行
        const gone = new Set(Array.isArray(body.supersedes) ? (body.supersedes as string[]) : [])
        if ([...gone].some((x) => registered.has(x))) {
          const res = json({ error: '上一次的修改其实已经开始了', code: 'ai_supersede_conflict', params: {} }, 409)
          onSupersedeConflict?.()
          return res
        }
        // 与后端 `put_pending` 同一道闸：同面板 / 同脚本上还有没落定的（且这次没取代它）→ 409 ai_pending_unresolved，什么都不写
        const isNew = !list.some((x) => x.rid === rid)
        if (isNew) {
          const script = (body.script as string | null) ?? null
          const prefix = key(pj, '')
          const blockers = [...pendings.entries()]
            .filter(([k]) => k.startsWith(prefix))
            .flatMap(([k, rows]) => rows.map((r) => ({ file: k.slice(prefix.length), ...r })))
            .filter((r) => r.rid !== rid && !gone.has(r.rid))
            .filter((r) => r.file === file || (!!script && !!r.script && sameScript(r.script, script)))
          if (blockers.length) {
            const res = json(
              {
                error: '还有没落定的',
                code: 'ai_pending_unresolved',
                params: {},
                rids: blockers.map((r) => r.rid),
                pendings: blockers.map((r) => ({ file: r.file, rid: r.rid, text: r.text, sent: r.sent, script: r.script ?? null })),
              },
              409,
            )
            onUnresolved?.(blockers.map((r) => r.rid))
            return res
          }
        }
        const next = [
          ...list.filter((x) => x.rid !== rid && !gone.has(x.rid)),
          {
            rid,
            text: String(body.text),
            sent: body.sent,
            script: (body.script as string | null) ?? null,
            draftStamp: typeof body.draft_stamp === 'number' ? body.draft_stamp : null,
          },
        ]
        pendings.set(key(pj, file), next.slice(-8))
        if (loseNextPendingResponses > 0) {
          loseNextPendingResponses--
          throw new TypeError('Failed to fetch') // 已经写入，只是响应丢了
        }
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
          stamp: stamps.get(key(pj, file)) ?? 0,
          draft: text === undefined ? null : { text, revision: 1, updated_ms: 1, stamp: stamps.get(key(pj, file)) ?? 0 },
          pendings: (pendings.get(key(pj, file)) ?? []).map((p) => ({ ...p, created_ms: 1 })),
        })
      }
      const file = String(body.file ?? '')
      if (method === 'PUT') {
        if (draftPutFails) return json({ error: 'boom', code: 'internal_error' }, 500)
        // 与后端 `put_draft` 同语义：带戳的写入只有比已存戳更新才接受；空文本 / 超限 = 删但留墓碑（戳还在）
        const apply = (): Response => {
          const text = String(body.text)
          const stamp = typeof body.stamp === 'number' ? body.stamp : Date.now() * 1000
          const prev = stamps.get(key(pj, file))
          if (prev !== undefined && stamp <= prev)
            return json({ stale: true, revision: 1, stamp: prev, text_hash: textHash(drafts.get(key(pj, file)) ?? '') })
          stamps.set(key(pj, file), stamp)
          if (text === '' || bytes(text) > MAX) drafts.delete(key(pj, file))
          else drafts.set(key(pj, file), text)
          if (bytes(text) > MAX) return json({ error: 'too large', code: 'ai_draft_too_large', params: { limit_kb: 20 }, stamp }, 413)
          return json({ revision: 1, stamp })
        }
        if (holdNext) {
          const wait = holdNext
          holdNext = null
          return wait.then(apply)
        }
        return hold ? hold.then(apply) : apply()
      }
      if (method === 'DELETE') {
        const del = drafts.get(key(pj, file)) === body.if_text
        if (del) {
          drafts.delete(key(pj, file))
          stamps.set(key(pj, file), (stamps.get(key(pj, file)) ?? 0) + 1) // 墓碑只 +1：取代的恰好是被删的那一版，之后的新编辑不被挡掉
        }
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
    holdReads: (scope = 'all') => {
      readHoldScope = scope
      let release!: () => void
      readHold = new Promise<void>((r) => {
        release = () => {
          readHold = null
          r()
        }
      })
      return release
    },
    holdNextDraftPut: () => {
      let release!: () => void
      holdNext = new Promise<void>((r) => {
        release = () => r()
      })
      return release
    },
    putStamped: (pj, f, t, stamp) => {
      const prev = stamps.get(key(pj, f))
      if (prev !== undefined && stamp <= prev) return false
      stamps.set(key(pj, f), stamp)
      if (t === '') drafts.delete(key(pj, f))
      else drafts.set(key(pj, f), t)
      return true
    },
    holdDraftPuts: () => {
      let release!: () => void
      hold = new Promise<void>((r) => {
        release = () => {
          hold = null
          r()
        }
      })
      return release
    },
    setDraftPutFails: (f) => {
      draftPutFails = f
    },
    setPendingListFails: (f) => {
      pendingListFails = f
    },
    setDraftGetFails: (f) => {
      draftGetFails = f
    },
    markRegistered: (rid) => {
      registered.add(rid)
    },
    afterPendingUnresolved: (fn) => {
      onUnresolved = fn
    },
    afterSupersedeConflict: (fn) => {
      onSupersedeConflict = fn
    },
    setPendingLimit: (n) => {
      pendingLimit = n
    },
    losePendingResponses: (n) => {
      loseNextPendingResponses = n
    },
    dropRejectedPending: (rid) => {
      for (const [k, list] of pendings) pendings.set(k, list.filter((x) => x.rid !== rid))
    },
    setPendingWriteFails: (f) => {
      pendingWriteFails = f
    },
    draft: (pj, f) => drafts.get(key(pj, f)),
    pending: (pj, f) => (pendings.get(key(pj, f)) ?? []).map((p) => p.rid),
    put: (pj, f, t) => void drafts.set(key(pj, f), t), // 测试直接塞值：不动戳（视为「更早」的存量）
  }
}
