/**
 * 文档恢复验收的共用夹具（QA）：有状态假后端 + 手动 gate + 启动器 + 文档量尺。
 *
 * 只用**已有公共入口**驱动真实 store / API client / 迁移与恢复代码；唯一被替换的是 `fetch`。
 * 假后端能表达合同里的新回包（`{last, prev, pending, index}`、`state`、`metadata`、
 * `/owner` 的 `verified`、`/candidates`），旧代码不读这些字段也能跑——所以同一份用例在未修复
 * 代码上的红来自**目标行为**，不是缺模块。
 *
 * 两档 last 语义（`be.lastMode`）：
 *   - `legacy`：旧后端。`PUT /last` 一律立刻成为 last，即使槽位还没有正文（R3 的成因）；
 *   - `durable`：新合同。槽位没有有效正文 → 只记 `pending`，正文自动保存成功时在同一提交里晋升。
 */
import { vi } from 'vitest'
import type { CanvasData, PanelObject, ProjectDocument, TextObject } from '@/types/document'
import { emptyProject } from '@/types/document'

/* ------------------------------ 请求记录与 gate ------------------------------ */

export interface Req {
  seq: number
  method: string
  path: string
  /** 请求所属项目（`X-Tavotto-Project` 头或 `?pj=`）；没有是 '' */
  pj: string
  docId?: string
  body?: string
}

export interface Gate {
  /** 第一个被它截住的请求到了 */
  reached: Promise<void>
  release: () => void
  hits: number
}

interface GateInternal extends Gate {
  match: (r: Req) => boolean
  once: boolean
  spent: boolean
  promise: Promise<void>
  markReached: () => void
}

interface Failure {
  match: (r: Req) => boolean
  status: number
  left: number
}

type LastRef = { doc_id: string; name: string; at: number; revision?: string | null }

class FakeBackend {
  slots = new Map<string, string>()
  revs = new Map<string, string>()
  lastByPj = new Map<string, LastRef>()
  prevByPj = new Map<string, LastRef>()
  pendingByPj = new Map<string, LastRef>()
  /** docId → 项目键；`null` = 明确的「没开项目」；缺席 = 未知 */
  owners = new Map<string, string | null>()
  /** `pj:docId` 形式的证据 */
  versionsEvidence = new Set<string>()
  assetsEvidence = new Set<string>()
  /** 这些 id 的 GET 回 400（坏文档） */
  corrupt = new Set<string>()
  requests: Req[] = []
  deletes: string[] = []
  gates: GateInternal[] = []
  failures: Failure[] = []
  lastMode: 'legacy' | 'durable' = 'legacy'
  sessionEndpoints = true
  /** true = 模拟旧后端：指针不带 `revision` 键 */
  omitPointerRevision = false
  ownerEndpoint = true
  /** 为 false 时 /candidates 回 404（旧后端） */
  candidatesEndpoint = true
  panels: unknown[] = []
  figuresDir = '/figs/a'
  clock = 10
  private revSeq = 0
  private epoch = 0
  private seq = 0
  inFlight = 0

  reset(): void {
    this.epoch += 1
    this.slots.clear()
    this.revs.clear()
    this.lastByPj.clear()
    this.prevByPj.clear()
    this.pendingByPj.clear()
    this.owners.clear()
    this.versionsEvidence.clear()
    this.assetsEvidence.clear()
    this.corrupt.clear()
    this.requests.length = 0
    this.deletes.length = 0
    this.gates.length = 0
    this.failures.length = 0
    this.lastMode = 'legacy'
    this.sessionEndpoints = true
    this.omitPointerRevision = false
    this.ownerEndpoint = true
    this.candidatesEndpoint = true
    this.panels = [FIG1_PANEL_INFO]
    this.clock = 10
    this.inFlight = 0
  }

  /** 种一个槽位（磁盘上的自动保存正文） */
  putSlot(id: string, body: unknown): void {
    this.slots.set(id, typeof body === 'string' ? body : JSON.stringify(body))
    this.revs.set(id, `r${++this.revSeq}`)
  }

  slotBytes(id: string): string | undefined {
    return this.slots.get(id)
  }

  /** 后端「上次开着的」（过滤前的原始记录） */
  lastOf(pj: string): LastRef | undefined {
    return this.lastByPj.get(pj)
  }

  setLast(pj: string, id: string, name: string): LastRef {
    const ref = { doc_id: id, name, at: ++this.clock, revision: this.revs.get(id) ?? null }
    this.lastByPj.set(pj, ref)
    return ref
  }

  /** 截住匹配的请求（默认只截第一个）；`release()` 后它们才继续（GET 的回包在截住前就定好了） */
  hold(match: (r: Req) => boolean, opts: { once?: boolean } = {}): Gate {
    let release!: () => void
    let markReached!: () => void
    const promise = new Promise<void>((r) => (release = r))
    const reached = new Promise<void>((r) => (markReached = r))
    const g: GateInternal = {
      match,
      once: opts.once ?? true,
      spent: false,
      promise,
      reached,
      release,
      markReached,
      hits: 0,
    }
    this.gates.push(g)
    return g
  }

  /** 让匹配的请求回给定状态码，`times` 次后恢复正常 */
  fail(match: (r: Req) => boolean, status: number, times = 1): void {
    this.failures.push({ match, status, left: times })
  }

  count(match: (r: Req) => boolean): number {
    return this.requests.filter(match).length
  }

  private ownerConflict(id: string, pj: string): boolean {
    return this.owners.has(id) && this.owners.get(id) !== pj
  }

  private slotOk(id: string): boolean {
    const raw = this.slots.get(id)
    if (raw === undefined || this.corrupt.has(id)) return false
    try {
      const v = JSON.parse(raw) as { schema?: number }
      return typeof v.schema === 'number' && v.schema <= 3
    } catch {
      return false
    }
  }

  async handle(url: unknown, init?: RequestInit): Promise<Response> {
    this.inFlight += 1
    try {
      return await this.handleInner(url, init)
    } finally {
      this.inFlight -= 1
    }
  }

  private async handleInner(url: unknown, init?: RequestInit): Promise<Response> {
    // 上一个用例遗留下来的请求（旧模块实例里还没跑完的推送 / 重试）在 `reset()` 之后才到达：
    // 不许碰新用例的后端状态，也不许被新用例的 gate 截走——整套件并行（机器负载高）时它们会晚到
    const epoch = this.epoch
    // 压力复现用：QA_JITTER=毫秒 让每个请求随机多等一会儿（模拟整套件并行时的机器负载）
    const jitter = Number(typeof process !== 'undefined' ? process.env.QA_JITTER : 0) || 0
    if (jitter > 0) await new Promise((r) => setTimeout(r, Math.random() * jitter))
    if (epoch !== this.epoch) return new Response(JSON.stringify({ error: 'stale epoch' }), { status: 503 })
    const u = new URL(String(url), 'http://127.0.0.1')
    const method = init?.method ?? 'GET'
    const pj = new Headers(init?.headers).get('X-Tavotto-Project') ?? u.searchParams.get('pj') ?? ''
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status })
    const auto = u.pathname.match(/^\/api\/autosave\/([^/]+)$/)
    const req: Req = {
      seq: ++this.seq,
      method,
      path: u.pathname,
      pj,
      ...(auto ? { docId: decodeURIComponent(auto[1]) } : {}),
      ...(typeof init?.body === 'string' ? { body: init.body } : {}),
    }
    this.requests.push(req)

    // 回包先定（截住期间别处的写入不影响这份回包 = 「在路上的旧回包」）
    let response: (() => Response) | null = null

    const failure = this.failures.find((f) => f.left > 0 && f.match(req))
    if (failure) {
      failure.left -= 1
      const status = failure.status
      response = () => json({ error: 'injected' }, status)
    }

    let apply: (() => Response) | null = null

    if (!response && auto) {
      const id = req.docId!
      if (method === 'PUT') {
        const body = String(init?.body)
        apply = () => {
          this.slots.set(id, body)
          const revision = `r${++this.revSeq}`
          this.revs.set(id, revision)
          if (this.knownProject(pj)) this.owners.set(id, pj)
          const pend = this.pendingByPj.get(pj)
          if (this.lastMode === 'durable' && pend?.doc_id === id) this.promote(pj, id, pend.name)
          return json({ ok: true, saved_at: 1, revision, metadata: 'ok' })
        }
      } else if (method === 'DELETE') {
        apply = () => {
          this.deletes.push(id)
          this.slots.delete(id)
          return json({ ok: true })
        }
      } else {
        // 正文读取在 gate 放行之后才定（读的是此刻磁盘上的那一份）：截住期间别的项目覆盖了它，这里读到的就是新的
        response = () => {
          const raw = this.slots.get(id)
          if (raw === undefined) return json({}, 404)
          if (this.corrupt.has(id)) return json({ error: 'bad', code: 'invalid_document' }, 400)
          return new Response(raw, { status: 200, headers: { 'X-Tavotto-Revision': this.revs.get(id) ?? 'r0' } })
        }
      }
    } else if (!response && u.pathname.startsWith('/api/layout-session')) {
      if (!this.sessionEndpoints) response = () => json({ error: 'not found' }, 404)
      else if (u.pathname === '/api/layout-session' && method === 'GET') {
        const last = this.lastByPj.get(pj) ?? null
        const strip = <T extends LastRef | null>(r: T): T => {
          if (!r || !this.omitPointerRevision) return r
          const { revision: _omitted, ...rest } = r
          void _omitted
          return rest as T
        }
        const out = {
          last: strip(last && !this.ownerConflict(last.doc_id, pj) ? { ...last } : null),
          prev: strip(this.filtered(this.prevByPj.get(pj), pj)),
          pending: strip(this.pendingByPj.get(pj) ? { ...this.pendingByPj.get(pj)! } : null),
          index: 'ok',
        }
        response = () => json(out)
      } else if (u.pathname === '/api/layout-session/last' && method === 'PUT') {
        const b = JSON.parse(String(init?.body)) as { doc_id: string; name: string }
        apply = () => {
          if (this.ownerConflict(b.doc_id, pj)) {
            return json({ error: 'foreign', code: 'layout_foreign' }, 409)
          }
          if (this.lastMode === 'legacy' || this.slotOk(b.doc_id)) {
            const ref = this.promote(pj, b.doc_id, b.name)
            return json({ ok: true, last: ref, state: 'durable' })
          }
          const ref = { doc_id: b.doc_id, name: b.name, at: ++this.clock, revision: this.revs.get(b.doc_id) ?? null }
          this.pendingByPj.set(pj, ref)
          return json({
            ok: true,
            last: this.filtered(this.lastByPj.get(pj), pj),
            pending: ref,
            state: 'pending',
          })
        }
      } else if (u.pathname === '/api/layout-session/owner') {
        if (!this.ownerEndpoint) response = () => json({ error: 'nf' }, 404)
        else {
          const id = u.searchParams.get('doc_id') ?? ''
          // 与真后端一致：revision = 回答这一刻槽位正文的 revision（没有槽位是 null），在 gate 放行之后才取
          response = () => json({ ...this.ownerVerdict(id, pj), revision: this.revs.get(id) ?? null })
        }
      } else if (u.pathname === '/api/layout-session/candidates') {
        if (!this.candidatesEndpoint) response = () => json({ error: 'nf' }, 404)
        else {
          const scope = u.searchParams.get('scope') ?? 'project'
          const out = this.candidates(pj, scope)
          response = () => json(out)
        }
      } else response = () => json({}, 404)
    } else if (!response) {
      response = () => this.misc(u, method)
    }

    for (const g of this.gates) {
      if (g.spent || !g.match(req)) continue
      g.hits += 1
      if (g.once) g.spent = true
      g.markReached()
      await g.promise
    }
    return (apply ?? response ?? (() => json({}, 404)))()
  }

  private knownProject(pj: string): boolean {
    return pj !== '' && pj !== 'closed'
  }

  private filtered(ref: LastRef | undefined, pj: string): LastRef | null {
    return ref && !this.ownerConflict(ref.doc_id, pj) ? { ...ref } : null
  }

  private promote(pj: string, id: string, name: string): LastRef {
    const old = this.lastByPj.get(pj)
    if (old && old.doc_id !== id) this.prevByPj.set(pj, old)
    const ref = { doc_id: id, name, at: ++this.clock, revision: this.revs.get(id) ?? null }
    this.lastByPj.set(pj, ref)
    if (this.pendingByPj.get(pj)?.doc_id === id) this.pendingByPj.delete(pj)
    return ref
  }

  private ownerVerdict(id: string, pj: string) {
    if (this.owners.has(id)) {
      return { owner: this.owners.get(id) === pj ? 'this' : 'other', evidence: 'owners', verified: true }
    }
    if (this.versionsEvidence.has(`${pj}:${id}`)) return { owner: 'this', evidence: 'versions', verified: false }
    if (this.assetsEvidence.has(`${pj}:${id}`)) return { owner: 'this', evidence: 'assets', verified: false }
    return { owner: 'unknown', evidence: null, verified: false }
  }

  private candidates(pj: string, scope: string) {
    const items: unknown[] = []
    for (const [id, raw] of this.slots) {
      let pd: ProjectDocument | null = null
      try {
        pd = JSON.parse(raw) as ProjectDocument
      } catch {
        pd = null
      }
      const verdict = this.ownerVerdict(id, pj)
      if (verdict.owner === 'other') continue
      if (scope === 'project' && verdict.owner !== 'this') continue
      if (scope === 'unknown' && verdict.owner !== 'unknown') continue
      items.push({
        doc_id: id,
        name: pd?.project?.name ?? '',
        updatedAt: pd?.updatedAt ?? 0,
        mtime: 0,
        revision: this.revs.get(id) ?? 'r0',
        schema: pd?.schema ?? 0,
        status: pd && !this.corrupt.has(id) ? 'ok' : 'invalid',
        canvases: pd?.canvases?.length ?? 0,
        objects: pd?.canvases?.reduce((n, c) => n + c.objects.length, 0) ?? 0,
        owner: verdict.owner,
        evidence: verdict.evidence,
        runtime: [],
        panels: [],
      })
    }
    return { items, next_cursor: null, index: 'ok' }
  }

  private misc(u: URL, method: string): Response {
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status })
    if (u.pathname === '/api/panels') return json({ panels: this.panels, figures_dir: this.figuresDir })
    if (u.pathname === '/api/runtime/assets') return json({ assets: [] })
    if (u.pathname.startsWith('/api/projects')) return json({ recent: [], pinned: [], projects: [] })
    if (u.pathname.startsWith('/api/preferences/')) {
      return method === 'GET' ? json({ defaults: null }) : json({ ok: true })
    }
    return json({}, 404)
  }
}

export const be = new FakeBackend()

export function installBackend(): void {
  globalThis.fetch = ((url: unknown, init?: RequestInit) => be.handle(url, init)) as typeof fetch
}

/* ---------------------------------- 夹具文档 ---------------------------------- */

export const FIG1_PANEL_INFO = {
  id: 'Fig1.pdf', name: 'Fig1', folder: '.', kind: 'pdf',
  native_w_mm: 80, native_h_mm: 60, mtime: 1, script: 'fig1.py',
}

/** 用户已经改过的面板：几何非默认、overrides 非空（正常恢复必须原样带回） */
export const SAVED_PANEL: PanelObject = {
  id: 'panel_saved', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf',
  nativeW: 80, nativeH: 60, x: 13, y: 24, w: 80, h: 60,
  script: 'fig1.py', overrides: [{ gid: 'figure', prop: 'facecolor', value: '#f00' }],
  figureFrame: 1,
}

export const text = (id: string, t: string, x = 0, y = 0): TextObject => ({
  id, type: 'text', text: t, sizePt: 9, bold: false,
  color: '#000', align: 'left', x, y, w: 20, h: 8,
})

/** 两张画布的已保存排版：第一张有改过的面板，第二张有一段文字 */
export function savedProject(name = '用户已修改的排版'): ProjectDocument {
  const pd = emptyProject()
  pd.project.name = name
  pd.canvases[0].objects = [structuredClone(SAVED_PANEL)]
  const c2: CanvasData = {
    id: 'c_second', name: 'Fig 2', page: { w: 120, h: 90 },
    objects: [text('t_c2', 'second canvas', 5, 6)], guides: [],
  }
  pd.canvases.push(c2)
  pd.updatedAt = 100
  return pd
}

/** 摆一份已保存排版：磁盘槽位 + 后端归属 + last；返回写进去的文档 */
export function seedSaved(
  id = 'd_saved',
  pj = 'p_a',
  opts: { last?: boolean; owner?: boolean; name?: string } = {},
): ProjectDocument {
  const pd = savedProject(opts.name)
  be.putSlot(id, pd)
  if (opts.owner ?? true) be.owners.set(id, pj)
  if (opts.last ?? true) be.setLast(pj, id, pd.project.name)
  return pd
}

type Shape = {
  name: string
  canvases: {
    id: string
    page: { w: number; h: number }
    objects: Record<string, unknown>[]
  }[]
}

const GEOM_KEYS = ['id', 'type', 'x', 'y', 'w', 'h', 'overrides', 'fileId', 'script', 'text'] as const

/** 文档的「编辑内容形状」：画布数、对象 id / 几何 / overrides / 来源——用来比「恢复的就是原来那份」 */
export function shapeOf(pd: ProjectDocument): Shape {
  return {
    name: pd.project.name,
    canvases: pd.canvases.map((c) => ({
      id: c.id,
      page: { w: c.page.w, h: c.page.h },
      objects: c.objects.map((o) => {
        const rec = o as unknown as Record<string, unknown>
        const out: Record<string, unknown> = {}
        for (const k of GEOM_KEYS) if (k in rec) out[k] = rec[k]
        return out
      }),
    })),
  }
}

/* ----------------------------------- 启动器 ----------------------------------- */

export const STATUS_A = { open: true, id: 'p_a', name: 'A', figures_dir: '/figs/a' }
export const STATUS_B = { open: true, id: 'p_b', name: 'B', figures_dir: '/figs/b' }

export async function boot(opts: { pj?: string; open?: boolean } = {}) {
  vi.resetModules()
  const pj = opts.pj ?? 'p_a'
  const session = await import('@/lib/session')
  session.setCurrentProjectId(pj)
  const doc = await import('@/store/documentStore')
  const proj = await import('@/store/projectStore')
  const refs = await import('@/lib/projectDocs')
  const handoff = await import('@/lib/openRequest')
  const native = await import('@/store/nativeSessionStore')
  const assets = await import('@/store/assetStore')
  if (opts.open ?? true) {
    proj.useProjectStore.setState({
      phase: 'open',
      project: { open: true, id: pj, figures_dir: be.figuresDir },
      lastDocumentIssue: null,
    })
  }
  return { doc, proj, refs, handoff, native, assets, session }
}
export type App = Awaited<ReturnType<typeof boot>>

/** 换 origin：本机存储全空 */
export function newOrigin(): void {
  localStorage.clear()
  sessionStorage.clear()
}

export function resetAll(): void {
  newOrigin()
  be.reset()
  installBackend()
}

/** 当前 store 里的排版形状（`buildProject()`：画布热态与 canvases 合并后的真值） */
export function currentShape(app: App): Shape & { documentId: string } {
  const s = app.doc.useDocumentStore.getState()
  return { documentId: s.documentId, ...shapeOf(s.buildProject()) }
}

export function localSnapshot(): Record<string, string> {
  const out: Record<string, string> = {}
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)!
    out[k] = localStorage.getItem(k)!
  }
  return out
}

/* ----------------------------------- 等待 ----------------------------------- */

export async function settle(): Promise<void> {
  const end = Date.now() + 3000 // 被 gate 截住没放的请求不让它永远等
  for (let quiet = 0; quiet < 3 && Date.now() < end; ) {
    await new Promise((r) => setTimeout(r, 5))
    quiet = be.inFlight === 0 ? quiet + 1 : 0
  }
}

/** 等一个可观察条件成立（异步完成用，不是时序判据）；超时就让断言说话 */
export async function until(pred: () => boolean, ms = 4000): Promise<boolean> {
  const end = Date.now() + ms
  while (!pred() && Date.now() < end) await new Promise((r) => setTimeout(r, 5))
  return pred()
}

/** 给一个本应立即落定的 promise 一个上限：落不了就返回 'pending'（死等的证据） */
export async function settledWithin<T>(p: Promise<T>, ms = 2000): Promise<T | 'pending'> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<'pending'>((r) => (timer = setTimeout(() => r('pending'), ms)))
  try {
    return await Promise.race([p, timeout])
  } finally {
    clearTimeout(timer)
  }
}

/* --------------------------- 失败信号（新旧状态都认） --------------------------- */

/**
 * 「恢复没有静默假空白」的可观察信号：旧的 `lastDocumentIssue` / `docNotice`，
 * 或修复后的恢复门 `restoreGate.state === 'needs_user'`（动态读，旧代码里没有这个字段）。
 */
export function failureSignal(app: App): string | null {
  const ds = app.doc.useDocumentStore.getState() as unknown as {
    docNotice: unknown
    restoreGate?: { state?: string }
    restoreIssue?: unknown
  }
  const ps = app.proj.useProjectStore.getState()
  if (ps.lastDocumentIssue) return `lastDocumentIssue=${JSON.stringify(ps.lastDocumentIssue)}`
  if (ds.docNotice) return 'docNotice'
  if (ds.restoreGate?.state === 'needs_user') return 'restoreGate=needs_user'
  if (ds.restoreIssue) return 'restoreIssue'
  return null
}
