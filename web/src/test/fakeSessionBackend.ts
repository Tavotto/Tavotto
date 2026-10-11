/**
 * 有状态的假后端：自动保存槽位 + 会话状态（last / prev / pending）+ 归属证据 + 找回候选，
 * 给恢复相关的单测用（`projectDocs.test` / `recoveryCandidates.test` / `LayoutDialog.recover.test`）。
 *
 * 形状对着合同（`scratchpad/recovery/CONTRACT.md` 第 1 节）：`PUT /last` 在槽位有正文时记 durable 并把旧 last 挪
 * 到 prev，没有正文时只记 pending、回 `state:'pending'`；`/owner` 回 `{owner, evidence, verified}`；
 * `/candidates` 按 mtime 新→旧分页。每个请求追加进 `calls`（`"METHOD path[ doc_id]"`），用例断言**实际序列**。
 * 故障开关（`fail`）与手动 gate（`hold`）让用例精确停在某个 await 上，不靠 sleep。
 */
export interface Ref {
  doc_id: string
  name: string
  at: number
  /** 缺省 = 回包里没有这个键（旧后端）；null = 键在、值为 null（新后端：校验时槽位不存在） */
  revision?: string | null
}

export interface Slot {
  body: string
  revision: string
  mtime: number
}

interface Session {
  last: Ref | null
  prev: Ref | null
  pending: Ref | null
  index: 'ok' | 'missing' | 'degraded' | 'recovered'
}

export interface OwnerAnswer {
  owner: 'this' | 'other' | 'unknown'
  evidence: 'owners' | 'versions' | 'assets' | null
  verified?: boolean
  /** 缺省 = 槽位当前正文的 revision（没有槽位 = null）；显式给就原样回（造「结论之后正文又变了」） */
  revision?: string | null
}

export interface CandidateRow {
  doc_id: string
  name?: string
  scope: 'project' | 'unknown'
  status?: 'ok' | 'invalid' | 'too_new' | 'too_large'
  canvases?: number
  objects?: number
  mtime?: number
  sketch?: unknown
  runtime?: { script: string; figure: string }[]
}

export type Failure = number | 'network'

export function createFakeBackend() {
  const b = {
    slots: new Map<string, Slot>(),
    sessions: new Map<string, Session>(),
    /** doc_id → pj（槽位归属记录） */
    owners: new Map<string, string>(),
    /** `${pj}|${doc_id}` → 自定义的 /owner 回答（没有就按 owners 现算） */
    ownerAnswers: new Map<string, OwnerAnswer>(),
    candidates: [] as CandidateRow[],
    clock: 1,
    calls: [] as string[],
    /** 每次 /candidates 请求属于哪个项目（pj）；不进 `calls`，免得改动既有的序列断言 */
    candidatePjs: [] as string[],
    /** 端点 → 故障（状态码或网络错误）；一次性故障用 `failOnce` */
    fail: {} as Partial<Record<'session' | 'putLast' | 'owner' | 'autosaveGet' | 'autosavePut' | 'candidates', Failure>>,
    failOnce: {} as Partial<Record<'session' | 'putLast' | 'owner' | 'autosaveGet' | 'autosavePut' | 'candidates', Failure>>,
    /** 每个 doc 的 autosave GET 单独故障 */
    failGetDoc: new Map<string, Failure>(),
    hold: { session: null as Promise<void> | null, autosaveGet: null as Promise<void> | null, candidates: null as Promise<void> | null },
    /** autosave PUT 回包带的 metadata（`undefined` = 旧后端不带） */
    metadata: 'ok' as 'ok' | 'failed' | 'owner_unknown' | undefined,
    pageSize: 50,
    /** true = autosave GET 不带 X-Tavotto-Revision（代理吃了响应头） */
    stripRevisionHeader: false,
    /** 候选扫描碰到后端上限（回包 truncated:true） */
    truncated: false,
  }

  const session = (pj: string): Session => {
    let s = b.sessions.get(pj)
    if (!s) b.sessions.set(pj, (s = { last: null, prev: null, pending: null, index: 'ok' }))
    return s
  }
  const pjOf = (u: URL, init?: RequestInit): string => {
    const h = (init?.headers ?? {}) as Record<string, string>
    return h['X-Tavotto-Project'] ?? u.searchParams.get('pj') ?? ''
  }
  const revisionOf = (text: string) => `h${text.length.toString(16)}${[...text].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7).toString(16)}`

  const schemaOf = (body: string | undefined): number => {
    try {
      return (JSON.parse(body ?? '{}') as { schema?: number }).schema ?? 3
    } catch {
      return 3
    }
  }

  const json = (v: unknown, status = 200, headers?: Record<string, string>) =>
    new Response(JSON.stringify(v), { status, headers })

  const pick = (key: keyof typeof b.fail): Failure | undefined => {
    const once = b.failOnce[key]
    if (once !== undefined) {
      delete b.failOnce[key]
      return once
    }
    return b.fail[key]
  }
  const failed = (f: Failure): Response => {
    if (f === 'network') throw new TypeError('Failed to fetch')
    return json({ error: 'boom' }, f)
  }

  const ownerFor = (pj: string, id: string): OwnerAnswer => {
    const rev = b.slots.get(id)?.revision ?? null
    const custom = b.ownerAnswers.get(`${pj}|${id}`)
    if (custom) return { ...custom, revision: custom.revision !== undefined ? custom.revision : rev }
    if (b.owners.has(id)) {
      return { owner: b.owners.get(id) === pj ? 'this' : 'other', evidence: 'owners', verified: true, revision: rev }
    }
    return { owner: 'unknown', evidence: null, verified: false, revision: rev }
  }

  const fetchImpl = (async (url: unknown, init?: RequestInit) => {
    const u = new URL(String(url), 'http://127.0.0.1')
    const method = init?.method ?? 'GET'
    const pj = pjOf(u, init)
    const auto = u.pathname.match(/^\/api\/autosave\/([^/]+)$/)
    if (auto) {
      const id = decodeURIComponent(auto[1])
      if (method === 'PUT') {
        b.calls.push(`PUT autosave ${id}`)
        const f = pick('autosavePut')
        if (f !== undefined) return failed(f)
        const text = String(init?.body)
        const revision = revisionOf(text)
        b.clock += 1
        b.slots.set(id, { body: text, revision, mtime: b.clock })
        if (b.metadata !== 'failed') {
          b.owners.set(id, pj)
          const s = session(pj)
          if (s.pending?.doc_id === id) {
            s.prev = s.last && s.last.doc_id !== id ? s.last : s.prev
            s.last = { ...s.pending, at: b.clock }
            s.pending = null
          }
        }
        return json({ ok: true, saved_at: b.clock, revision, ...(b.metadata ? { metadata: b.metadata } : {}) })
      }
      b.calls.push(`GET autosave ${id}`)
      if (b.hold.autosaveGet) await b.hold.autosaveGet
      const f = b.failGetDoc.get(id) ?? pick('autosaveGet')
      if (f !== undefined) return failed(f)
      const slot = b.slots.get(id)
      return slot
        ? new Response(slot.body, { status: 200, headers: b.stripRevisionHeader ? {} : { 'X-Tavotto-Revision': slot.revision } })
        : json({ error: 'nf' }, 404)
    }
    if (u.pathname === '/api/layout-session' && method === 'GET') {
      b.calls.push('GET session')
      const snap = { ...session(pj) }
      if (b.hold.session) await b.hold.session
      const f = pick('session')
      if (f !== undefined) return failed(f)
      return json(snap)
    }
    if (u.pathname === '/api/layout-session/last' && method === 'PUT') {
      const body = JSON.parse(String(init?.body)) as { doc_id: string; name: string }
      b.calls.push(`PUT last ${body.doc_id}`)
      const f = pick('putLast')
      if (f !== undefined) return failed(f)
      if (b.owners.has(body.doc_id) && b.owners.get(body.doc_id) !== pj) {
        return json({ error: 'foreign', code: 'layout_foreign' }, 409)
      }
      const s = session(pj)
      b.clock += 1
      const ref: Ref = { doc_id: body.doc_id, name: body.name, at: b.clock }
      if (b.slots.has(body.doc_id)) {
        if (s.last && s.last.doc_id !== body.doc_id) s.prev = s.last
        s.last = ref
        if (s.pending?.doc_id === body.doc_id) s.pending = null
        return json({ ok: true, last: ref, state: 'durable' })
      }
      s.pending = ref
      return json({ ok: true, last: s.last, state: 'pending', pending: ref })
    }
    if (u.pathname === '/api/layout-session/owner') {
      const id = u.searchParams.get('doc_id') ?? ''
      b.calls.push(`GET owner ${id}`)
      const f = pick('owner')
      if (f !== undefined) return failed(f)
      return json(ownerFor(pj, id))
    }
    if (u.pathname === '/api/layout-session/candidates') {
      const scope = u.searchParams.get('scope') as 'project' | 'unknown'
      const cursor = Number(u.searchParams.get('cursor') ?? 0)
      b.calls.push(`GET candidates ${scope}${cursor ? ` @${cursor}` : ''}`)
      b.candidatePjs.push(pj)
      if (b.hold.candidates) await b.hold.candidates
      const f = pick('candidates')
      if (f !== undefined) return failed(f)
      const all = b.candidates.filter((c) => c.scope === scope)
      const limit = Math.min(Number(u.searchParams.get('limit') ?? b.pageSize), b.pageSize)
      const page = all.slice(cursor, cursor + limit)
      const items = page.map((c) => {
        const slot = b.slots.get(c.doc_id)
        return {
          doc_id: c.doc_id,
          name: c.name ?? c.doc_id,
          updatedAt: 1000,
          mtime: c.mtime ?? slot?.mtime ?? 1,
          revision: slot?.revision ?? null,
          schema: schemaOf(slot?.body),
          status: c.status ?? 'ok',
          canvases: c.canvases ?? 1,
          objects: c.objects ?? 1,
          owner: c.scope === 'project' ? 'this' : 'unknown',
          evidence: c.scope === 'project' ? 'owners' : null,
          ...(c.sketch ? { sketch: c.sketch } : {}),
          ...(c.runtime ? { runtime: c.runtime } : {}),
        }
      })
      const next = cursor + limit < all.length ? String(cursor + limit) : null
      return json({ items, next_cursor: next, truncated: b.truncated, index: 'ok' })
    }
    return json({ error: 'nf' }, 404)
  }) as typeof fetch

  return {
    state: b,
    fetch: fetchImpl,
    session,
    revisionOf,
    /** 直接摆一个槽位（不算调用） */
    putSlot(id: string, doc: unknown, owner?: string): Slot {
      const body = typeof doc === 'string' ? doc : JSON.stringify(doc)
      b.clock += 1
      const slot = { body, revision: revisionOf(body), mtime: b.clock }
      b.slots.set(id, slot)
      if (owner !== undefined) b.owners.set(id, owner)
      return slot
    },
    /** 摆一条 durable last */
    setLast(pj: string, id: string, name = id, revision?: string | null): Ref {
      b.clock += 1
      const ref: Ref = { doc_id: id, name, at: b.clock, ...(revision !== undefined ? { revision } : {}) }
      session(pj).last = ref
      return ref
    },
    setPending(pj: string, id: string, name = id, revision?: string | null): Ref {
      b.clock += 1
      const ref: Ref = { doc_id: id, name, at: b.clock, ...(revision !== undefined ? { revision } : {}) }
      session(pj).pending = ref
      return ref
    },
    puts: () => b.calls.filter((c) => c.startsWith('PUT')),
    writes: () => b.calls.filter((c) => c.startsWith('PUT') || c.startsWith('DELETE')),
    reset() {
      b.slots.clear()
      b.sessions.clear()
      b.owners.clear()
      b.ownerAnswers.clear()
      b.candidates = []
      b.clock = 1
      b.calls.length = 0
      b.candidatePjs.length = 0
      b.fail = {}
      b.failOnce = {}
      b.failGetDoc.clear()
      b.hold.session = null
      b.hold.autosaveGet = null
      b.hold.candidates = null
      b.metadata = 'ok'
      b.pageSize = 50
      b.stripRevisionHeader = false
      b.truncated = false
    },
  }
}

export type FakeBackend = ReturnType<typeof createFakeBackend>
