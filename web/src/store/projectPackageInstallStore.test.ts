import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { projectFileEditor } from '@/lib/projectFileSession'
import { currentProjectId, setCurrentProjectId } from '@/lib/session'
import { emptyProject, type ProjectDocument } from '@/types/document'
import { resetRestoreStateForTests, setRestoreRetryDelays } from './documentRestore'
import { saveNowWithResult, startAutosave, useDocumentStore } from './documentStore'
import { useProjectStore } from './projectStore'
import { useProjectPackageInstallStore } from './projectPackageInstallStore'

vi.mock('@/lib/timelineThumb', () => ({ captureThumbSources: vi.fn(() => new Map()), composeTimelineThumb: vi.fn(async () => null) }))

const s = () => useDocumentStore.getState()
const p = () => useProjectPackageInstallStore.getState()
const hex = (n: number) => n.toString(16).padStart(32, '0')
let serial = 0
let revision = 0
let packageDocument: ProjectDocument
let installState: 'resources_installed' | 'installation_unconfirmed'
let configuration: 'ready' | 'pending' | 'needs_review'
let failWriteProject: string | null
let failOpen: boolean
let installFailure: 'conflict' | 'network' | 'lost-after-publish' | 'server' | 'malformed' | null
let failReadProject: string | null
let stopAutosave: (() => void) | undefined
interface Request { path: string; method: string; pj: string | null; body: unknown }
const requests: Request[] = []
const publishedDestinations: string[] = []
const slots = new Map<string, { doc: ProjectDocument; revision: string; axis: unknown }>()
const lastDocuments = new Map<string | null, { id: string; format: 'legacy' | 'v4'; name: string }>()
const key = (pj: string | null, id: string) => JSON.stringify([pj, id])
interface Held {
  path: string
  method?: string
  wait: Promise<void>
  entered: Promise<void>
  release: () => void
  reach: () => void
  used: boolean
  after?: () => boolean
}
const held: Held[] = []
function hold(path: string, method?: string, after?: () => boolean): Held {
  let release!: () => void, reach!: () => void
  const entry = { path, method, wait: new Promise<void>(resolve => { release = resolve }), entered: new Promise<void>(resolve => { reach = resolve }),
    release: () => release(), reach: () => reach(), used: false, after }
  held.push(entry)
  return entry
}
const puts = (pj = 'installed-project') => requests.filter(row => row.path.startsWith('/api/autosave/') && row.method === 'PUT' && row.pj === pj)
const installs = () => requests.filter(row => row.path.endsWith('/package/install'))
const opens = () => requests.filter(row => row.path === '/api/projects/open')
const file = (name = 'project.zip') => new File(['archive fixture, never parsed by the frontend'], name)
const doc = (empty = false) => {
  const document = emptyProject(); document.schema = 4
  document.project.name = 'installed document'
  if (!empty) document.canvases[0].objects = [{ id: 'text', type: 'text', text: 'package content', sizePt: 9, bold: false,
    color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8 }]
  return document
}
const receipt = () => ({ state: installState, path: '/new/中文项目', document_format: 'v4', doc: structuredClone(packageDocument),
  references: [], execution: null, remap_fingerprint: 'fp', missing: [], warnings: [], execution_coverage: null,
  configuration, configuration_review: { version: 1, existing_local_metadata: false, workdir: null, defaults: [], issues: [] }, document_saved: false })

beforeEach(async () => {
  setCurrentProjectId(null); resetRestoreStateForTests(); setRestoreRetryDelays([0, 0])
  await s().switchDocument(emptyProject(), `package-reset-${++serial}`)
  p().reset(); projectFileEditor.clear(); localStorage.clear(); slots.clear(); lastDocuments.clear(); requests.length = 0; publishedDestinations.length = 0
  packageDocument = doc(); installState = 'resources_installed'; configuration = 'ready'; failWriteProject = null; failOpen = false; installFailure = null
  failReadProject = null
  vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = new URL(String(input), 'http://test'), versioned = url.pathname.startsWith('/api/documents/v4/')
    const path = url.pathname.replace('/api/documents/v4/', '/api/'), method = init?.method ?? 'GET', pj = url.searchParams.get('pj')
    requests.push({ path, method, pj, body: init?.body })
    const wait = held.find(entry => !entry.used && (!entry.after || entry.after()) && (path === entry.path || (entry.path.endsWith('*') && path.startsWith(entry.path.slice(0, -1)))) && (!entry.method || method === entry.method))
    if (wait) { wait.used = true; wait.reach(); await wait.wait }
    const reply = (body: unknown, status = 200, rev?: string) => Response.json(body, {
      status, headers: { ...(versioned ? { 'X-Tavotto-Document-Format': 'v4' } : {}), ...(rev ? { 'X-Tavotto-Revision': rev } : {}) },
    })
    if (path.endsWith('/package/inspect')) return reply({ document_format: 'v4', doc: packageDocument, manifest: {}, resources: [], missing: [],
      warnings: [], figure_snapshots: 0, execution_coverage: null })
    if (path.endsWith('/package/install')) {
      if (installFailure === 'network') throw new TypeError('Failed to fetch')
      if (installFailure === 'conflict') return reply({ code: 'destination_exists' }, 409)
      if (installFailure === 'server') return reply({ code: 'session_unavailable' }, 503)
      if (installFailure === 'malformed') return reply({ document_format: 'v4', state: 'resources_installed' }, 201)
      if (installFailure === 'lost-after-publish') {
        const options = JSON.parse(String((init!.body as FormData).get('options')))
        publishedDestinations.push(options.destination)
        throw new TypeError('Response lost after the server published the directory')
      }
      const options = JSON.parse(String((init!.body as FormData).get('options')))
      return reply({ ...receipt(), path: options.destination }, installState === 'resources_installed' ? 201 : 503)
    }
    if (path === '/api/projects/open') return failOpen ? reply({ code: 'session_unavailable' }, 503)
      : JSON.parse(String(init?.body)).path === '/other'
        ? reply({ open: true, id: 'other-project', name: 'other', figures_dir: '/other' })
        : reply({ open: true, id: 'installed-project', name: '中文项目', figures_dir: '/new/中文项目' })
    if (path === '/api/layout-session/last') {
      const body = JSON.parse(String(init?.body)), format = versioned ? 'v4' : 'legacy'
      if (!body.repair) lastDocuments.set(pj, { id: body.doc_id, format, name: body.name })
      const selected = lastDocuments.get(pj), slot = slots.get(key(pj, body.doc_id))
      if (body.repair && (!selected || selected.id !== body.doc_id || selected.format !== format)) return reply({ ok: true, state: 'superseded', last: null })
      const pointer = { doc_id: body.doc_id, name: body.name, at: 1, revision: slot?.revision ?? null }
      return reply({ ok: true, state: slot ? 'durable' : 'pending', last: slot ? pointer : null, pending: slot ? null : pointer })
    }
    if (path === '/api/layout-session') {
      const selected = lastDocuments.get(pj), slot = selected && slots.get(key(pj, selected.id))
      const here = selected?.format === (versioned ? 'v4' : 'legacy')
      const pointer = selected && { doc_id: selected.id, name: selected.name, at: 1, revision: slot?.revision ?? null }
      return reply({ last: here && slot ? pointer : null, pending: here && !slot ? pointer : null, prev: null, index: 'ok',
        open_intent: { version: 1, status: 'ok', snapshot: `intent:${pj}:${selected?.id ?? ''}`, sequence: 1,
          latest: selected ? { document_format: selected.format, doc_id: selected.id, pointer: slot ? 'last' : 'pending' } : null } })
    }
    if (path === '/api/layout-session/owner') {
      const slot = slots.get(key(pj, url.searchParams.get('doc_id')!))
      return reply({ owner: slot ? 'this' : 'unknown', evidence: slot ? 'owners' : null, verified: !!slot, revision: slot?.revision ?? null })
    }
    if (path === '/api/layout-session/candidates') return reply({ items: [], complete: true, index: 'ok' })
    const metadata = path.match(/^\/api\/layout-session\/project-file\/([^/]+)$/)
    if (metadata) {
      const slot = slots.get(key(pj, metadata[1]))
      return reply({ version: 1, status: 'ok', binding: null, intent_sequence: 0,
        edit: { known: true, axis: slot?.axis ?? null, autosave_revision: slot?.revision ?? null } })
    }
    const auto = path.match(/^\/api\/autosave\/([^/]+)$/)
    if (auto) {
      const address = key(pj, auto[1]), slot = slots.get(address)
      if (method === 'GET') return pj === failReadProject ? reply({ code: 'disk_read_failed' }, 503)
        : slot ? reply(slot.doc, 200, slot.revision) : reply({}, 404)
      if (method === 'PUT') {
        if (pj === failWriteProject) return reply({ code: 'disk_write_failed' }, 500)
        const document = JSON.parse(String(init?.body)), edit = JSON.parse(url.searchParams.get('project_file_edit') ?? 'null')
        const saved = { doc: document, revision: hex(++revision), axis: edit?.axis ?? null }
        slots.set(address, saved)
        return reply({ ok: true, saved_at: 1, revision: saved.revision,
          project_file: { state: 'accepted', version: 1, status: 'ok', binding: null, intent_sequence: 0,
            edit: { known: true, axis: saved.axis, autosave_revision: saved.revision } } })
      }
    }
    if (path === '/api/projects/recent') return reply({ recent: [], pinned: [] })
    if (path === '/api/projects') return reply({ projects: [], default: null })
    if (path === '/api/panels') return reply({ figures_dir: '/new/中文项目', panels: [] })
    return reply({}, 404)
  }))
  setCurrentProjectId('old-project')
  useProjectStore.setState({ phase: 'open', project: { open: true, id: 'old-project', name: 'old', figures_dir: '/old' }, switching: false })
  stopAutosave = startAutosave()
})
afterEach(async () => {
  stopAutosave?.(); stopAutosave = undefined
  for (const entry of held) entry.release()
  held.length = 0
  await saveNowWithResult()
  p().reset(); setCurrentProjectId(null); resetRestoreStateForTests()
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})

async function published() {
  expect(await p().inspect(file())).toBe(true)
  expect(await p().install({ destination: '/new/中文项目' })).toBe(true)
}
async function oldDraft() {
  expect(await s().switchDocument(doc(), `old-content-${serial}`, undefined, { format: 'v4' })).toBe(true)
  await saveNowWithResult()
}
async function savedAway() {
  await published()
  expect(await p().openInstalled()).toBe(true)
  const id = s().documentId
  s().commit(literal('later edit'), draft => { draft.guides.push({ axis: 'x', pos: 99 }) })
  expect((await saveNowWithResult()).wrote).toBe(true)
  await useProjectStore.getState().open('/other')
  expect(currentProjectId()).toBe('other-project')
  p().reopen()
  return id
}

describe('显式项目包安装与单writer采用', () => {
  it('后端已发布但响应丢失且已关闭，内存仍保请求目标并可重开未知完成状态', async () => {
    expect(await p().inspect(file())).toBe(true)
    installFailure = 'lost-after-publish'
    const destination = '/new/lost-response-path', blocked = hold('/api/package/install')
    const pending = p().install({ destination })
    await blocked.entered
    p().cancel()
    blocked.release()
    expect(await pending).toBe(false)
    expect(publishedDestinations).toEqual([destination])
    expect(p().receipts).toEqual([])
    expect(p().phase).toBe('idle')
    expect(JSON.stringify(p())).toContain(destination)
    p().reopen()
    expect(p().phase).toBe('unconfirmed')
    expect(p().unconfirmedAttempt).toMatchObject({ destination, state: 'completion_unknown' })
    expect(p().installation).toBeNull()
    expect(p().saved).toBeNull()
    expect(await p().openInstalled()).toBe(false)
    expect(await p().continueEditing()).toBe(false)
    expect(await p().install({ destination: '/different' })).toBe(false)
    expect(installs()).toHaveLength(1)
    expect(opens()).toHaveLength(0)
  })

  it('实际请求发出前即保独立attempt，重开在途记录后晚到receipt只更新事实不自动采用', async () => {
    expect(await p().inspect(file())).toBe(true)
    const destination = '/new/in-flight', blocked = hold('/api/package/install')
    const pending = p().install({ destination })
    await blocked.entered
    const attempt = p().attempts[0]
    expect(attempt).toEqual({ attemptId: expect.any(Number), destination, state: 'installing' })
    p().cancel(); p().reopen(attempt.attemptId)
    expect(p().phase).toBe('unconfirmed')
    expect(p().unconfirmedAttempt).toEqual(attempt)
    expect(await p().openInstalled()).toBe(false)
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().phase).toBe('unconfirmed')
    expect(p().attempts[0]).toMatchObject({ attemptId: attempt.attemptId, state: 'confirmed' })
    expect(p().unconfirmedAttempt).toMatchObject({ attemptId: attempt.attemptId, state: 'confirmed', destination })
    expect(p().receipts[0]).toMatchObject({ attemptId: attempt.attemptId, installation: { path: destination }, saved: null })
    expect(p().installation).toBeNull()
    expect(opens()).toHaveLength(0)
    p().reopen(attempt.attemptId)
    expect(p().phase).toBe('published')
    expect(p().installation?.path).toBe(destination)
    expect(p().unconfirmedAttempt).toBeNull()
  })

  it.each(['network', 'server', 'malformed'] as const)('%s无可靠receipt不能猜未发布或已发布，只保unknown请求路径', async failure => {
    expect(await p().inspect(file())).toBe(true)
    installFailure = failure
    expect(await p().install({ destination: '/new/unknown' })).toBe(false)
    expect(p().phase).toBe('unconfirmed')
    expect(p().attempts).toEqual([{ attemptId: expect.any(Number), destination: '/new/unknown', state: 'completion_unknown' }])
    expect(p().unconfirmedAttempt).toEqual(p().attempts[0])
    expect(p().receipts).toEqual([])
    expect(p().saved).toBeNull()
    expect(p().installation).toBeNull()
    expect(installs()).toHaveLength(1)
    expect(opens()).toHaveLength(0)
  })

  it('明确409保rejected事实，不伪造unknown或发布receipt', async () => {
    expect(await p().inspect(file())).toBe(true)
    installFailure = 'conflict'
    expect(await p().install({ destination: '/exists' })).toBe(false)
    expect(p().attempts).toEqual([{ attemptId: expect.any(Number), destination: '/exists', state: 'rejected' }])
    expect(p().phase).toBe('error')
    expect(p().unconfirmedAttempt).toBeNull()
    expect(p().receipts).toEqual([])
  })

  it.each(['same', 'different'] as const)('未知请求完成后用户明确%s目标重试分新ID，保留旧unknown而不自动重发', async choice => {
    expect(await p().inspect(file())).toBe(true)
    installFailure = 'network'
    expect(await p().install({ destination: '/new/first-unknown' })).toBe(false)
    const original = p().attempts[0]
    expect(installs()).toHaveLength(1)
    installFailure = null
    const destination = choice === 'same' ? original.destination : '/new/second'
    expect(await p().install({ destination })).toBe(true)
    expect(installs()).toHaveLength(2)
    expect(p().attempts).toHaveLength(2)
    expect(p().attempts[0]).toEqual(original)
    expect(p().attempts[1]).toMatchObject({ destination, state: 'confirmed' })
    expect(p().attempts[1].attemptId).not.toBe(original.attemptId)
    expect(p().receipts[0].attemptId).toBe(p().attempts[1].attemptId)
    expect(p().unconfirmedAttempt).toBeNull()
    expect(await p().install({ destination })).toBe(false)
    expect(installs()).toHaveLength(2)
  })

  it('关闭并切项目后重开unknown只展示路径，旧File/inspection不能偷偷重绑新scope', async () => {
    expect(await p().inspect(file())).toBe(true)
    installFailure = 'network'
    expect(await p().install({ destination: '/new/old-scope' })).toBe(false)
    const original = p().attempts[0]
    p().cancel(); setCurrentProjectId('other-project'); p().reopen(original.attemptId)
    expect(p().unconfirmedAttempt).toEqual(original)
    expect(p().phase).toBe('unconfirmed')
    installFailure = null
    expect(await p().install({ destination: '/new/new-scope' })).toBe(false)
    expect(await p().openInstalled()).toBe(false)
    expect(await p().continueEditing()).toBe(false)
    expect(installs()).toHaveLength(1)
    expect(opens()).toHaveLength(0)
    expect(await p().inspect(file('explicit.zip'))).toBe(true)
    expect(await p().install({ destination: '/new/new-scope' })).toBe(true)
    expect(p().attempts[0]).toEqual(original)
    expect(p().attempts[1].attemptId).not.toBe(original.attemptId)
    expect(installs().map(row => row.pj)).toEqual(['old-project', 'other-project'])
  })

  it('旧请求网络失败晚于新inspection，只补unknown历史，不覆盖新表单或错误状态', async () => {
    expect(await p().inspect(file())).toBe(true)
    installFailure = 'network'
    const blocked = hold('/api/package/install'), pending = p().install({ destination: '/new/old-request' })
    await blocked.entered
    p().cancel(); setCurrentProjectId('other-project')
    packageDocument = doc(true); packageDocument.project.name = 'new inspection after install'
    expect(await p().inspect(file('new.zip'))).toBe(true)
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().phase).toBe('review')
    expect(p().inspection?.doc.project.name).toBe('new inspection after install')
    expect(p().error).toBeNull()
    expect(p().unconfirmedAttempt).toBeNull()
    expect(p().attempts[0]).toMatchObject({ destination: '/new/old-request', state: 'completion_unknown' })
  })

  it('两次独立请求完成顺序相反仍按请求ID关联，不覆盖旧unknown或新发布状态', async () => {
    expect(await p().inspect(file('first.zip'))).toBe(true)
    const blocked = hold('/api/package/install'), first = p().install({ destination: '/new/first' })
    await blocked.entered
    const firstId = p().attempts[0].attemptId
    p().cancel()
    expect(await p().inspect(file('second.zip'))).toBe(true)
    expect(await p().install({ destination: '/new/second' })).toBe(true)
    const secondId = p().attempts[1].attemptId
    expect(secondId).not.toBe(firstId)
    blocked.release()
    expect(await first).toBe(false)
    expect(p().phase).toBe('published')
    expect(p().installation?.path).toBe('/new/second')
    expect(p().attempts.map(row => [row.attemptId, row.destination, row.state])).toEqual([
      [firstId, '/new/first', 'confirmed'], [secondId, '/new/second', 'confirmed'],
    ])
    expect(p().receipts.map(row => [row.attemptId, row.installation.path])).toEqual([[secondId, '/new/second'], [firstId, '/new/first']])
    expect(opens()).toHaveLength(0)
  })

  it('已保存结果从其他项目继续编辑应回到目标的最新工作稿', async () => {
    const workingId = await savedAway()
    expect(p().phase).toBe('saved')
    const writes = puts().length
    expect(await p().continueEditing()).toBe(true)
    expect(currentProjectId()).toBe('installed-project')
    expect(s().documentId).toBe(workingId)
    expect(s().doc.guides).toContainEqual({ axis: 'x', pos: 99 })
    expect(p().phase).toBe('idle')
    expect(puts()).toHaveLength(writes)
    expect(installs()).toHaveLength(1)
    expect(requests.some(row => row.path.includes('/probe') || row.path.endsWith('/run'))).toBe(false)
  })

  it('对应项目当前另一工作稿原地继续，零打开/正文写入且undo引用不变', async () => {
    await published(); expect(await p().openInstalled()).toBe(true)
    const later = doc(); later.project.name = 'another working document'
    await s().switchDocument(later, `later-${serial}`, undefined, { format: 'v4' })
    s().commit(literal('latest unsaved'), draft => { draft.guides.push({ axis: 'y', pos: 34 }) })
    p().reopen()
    const id = s().documentId, past = s().past, count = requests.length
    expect(await p().continueEditing()).toBe(true)
    expect(requests).toHaveLength(count)
    expect(s().documentId).toBe(id)
    expect(s().past).toBe(past)
    expect(s().doc.guides).toContainEqual({ axis: 'y', pos: 34 })
    expect(p().phase).toBe('idle')
    expect(p().saved).not.toBeNull()
  })

  it('目标项目上次工作稿已换ID也沿原恢复读取，不重套包正文或旧saved文档', async () => {
    await published(); expect(await p().openInstalled()).toBe(true)
    const originalSaved = p().saved
    const later = doc(); later.project.name = 'latest replacement'
    later.canvases[0].guides.push({ axis: 'y', pos: 56 })
    await s().switchDocument(later, `latest-${serial}`, undefined, { format: 'v4' })
    expect((await saveNowWithResult()).wrote).toBe(true)
    const latestId = s().documentId
    await useProjectStore.getState().open('/other'); p().reopen()
    const writes = puts().length
    expect(await p().continueEditing()).toBe(true)
    expect(s().documentId).toBe(latestId)
    expect(s().projectMeta.name).toBe('latest replacement')
    expect(s().doc.guides).toContainEqual({ axis: 'y', pos: 56 })
    expect(p().saved).toBe(originalSaved)
    expect(puts()).toHaveLength(writes)
    expect(installs()).toHaveLength(1)
  })

  it('继续编辑前旧项目最新非空稿保存失败，保内存/undo/本机稿且不打开目标', async () => {
    await savedAway()
    await s().switchDocument(doc(), `other-content-${serial}`, undefined, { format: 'v4' })
    s().commit(literal('other unsaved'), draft => { draft.guides.push({ axis: 'x', pos: 73 }) })
    p().reopen()
    const id = s().documentId, past = s().past, before = opens().length
    failWriteProject = 'other-project'
    expect(await p().continueEditing()).toBe(false)
    expect(opens()).toHaveLength(before)
    expect(currentProjectId()).toBe('other-project')
    expect(s().documentId).toBe(id)
    expect(s().past).toBe(past)
    expect(s().doc.guides).toContainEqual({ axis: 'x', pos: 73 })
    expect(Object.keys(localStorage).some(name => name.includes(id) && localStorage.getItem(name)?.includes('73'))).toBe(true)
    expect(p().phase).toBe('error')
    expect(p().saved).not.toBeNull()
    expect(await p().openInstalled()).toBe(false)
  })

  it('继续编辑保护旧稿期间新增用户编辑则停止切换，保留最新内容', async () => {
    await savedAway()
    await s().switchDocument(doc(), `other-edit-${serial}`, undefined, { format: 'v4' })
    p().reopen()
    const blocked = hold(`/api/autosave/${s().documentId}`, 'PUT'), before = opens().length
    const pending = p().continueEditing()
    await blocked.entered
    s().commit(literal('while protecting'), draft => { draft.guides.push({ axis: 'y', pos: 87 }) })
    blocked.release()
    expect(await pending).toBe(false)
    expect(opens()).toHaveLength(before)
    expect(currentProjectId()).toBe('other-project')
    expect(s().doc.guides).toContainEqual({ axis: 'y', pos: 87 })
  })

  it('恢复目标读失败保saved证据不套旧包；明确重试才沿原恢复继续', async () => {
    const id = await savedAway(), saved = p().saved
    failReadProject = 'installed-project'
    expect(await p().continueEditing()).toBe(false)
    expect(p().phase).toBe('error')
    expect(p().saved).toBe(saved)
    expect(s().doc.objects).toEqual([])
    expect(await p().openInstalled()).toBe(false)
    failReadProject = null
    expect(await p().continueEditing()).toBe(true)
    expect(s().documentId).toBe(id)
    expect(s().doc.guides).toContainEqual({ axis: 'x', pos: 99 })
    expect(installs()).toHaveLength(1)
  })

  it.each(['cancel', 'projectABA', 'loadSeq'] as const)('继续编辑恢复真实GET等待时%s失效原token，不装迟到目标稿', async change => {
    const id = await savedAway(), saved = p().saved
    const blocked = hold(`/api/autosave/${id}`, 'GET')
    const pending = p().continueEditing()
    await blocked.entered
    if (change === 'cancel') p().cancel()
    else if (change === 'projectABA') { setCurrentProjectId('other-project'); setCurrentProjectId('installed-project') }
    else {
      const later = doc(true); later.project.name = 'newer explicit document'
      await s().switchDocument(later, s().documentId, undefined, { format: 'v4' })
    }
    blocked.release()
    expect(await pending).toBe(false)
    expect(s().doc.guides).not.toContainEqual({ axis: 'x', pos: 99 })
    if (change === 'loadSeq') expect(s().projectMeta.name).toBe('newer explicit document')
    expect(p().phase).not.toBe('saved')
    expect(p().saved).toBe(saved)
    expect(installs()).toHaveLength(1)
  })

  it('旧继续编辑恢复迟到不得关闭新的检视意图', async () => {
    const id = await savedAway(), blocked = hold(`/api/autosave/${id}`, 'GET')
    const pending = p().continueEditing()
    await blocked.entered
    p().cancel()
    packageDocument = doc(true); packageDocument.project.name = 'new inspection'
    expect(await p().inspect(file('new.zip'))).toBe(true)
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().phase).toBe('review')
    expect(p().inspection?.doc.project.name).toBe('new inspection')
    expect(s().doc.guides).not.toContainEqual({ axis: 'x', pos: 99 })
  })

  it.each(['cancel', 'projectABA', 'loadSeq'] as const)('继续编辑打开请求等待时%s保护后来文档且不认领目标', async change => {
    await savedAway()
    const blocked = hold('/api/projects/open'), pending = p().continueEditing()
    await blocked.entered
    if (change === 'cancel') p().cancel()
    else if (change === 'projectABA') { setCurrentProjectId('installed-project'); setCurrentProjectId('other-project') }
    else {
      const later = doc(true); later.project.name = 'later before adoption'
      await s().switchDocument(later, s().documentId, undefined, { format: 'v4' })
    }
    const id = s().documentId
    blocked.release()
    expect(await pending).toBe(false)
    expect(currentProjectId()).toBe('other-project')
    expect(s().documentId).toBe(id)
    if (change === 'loadSeq') expect(s().projectMeta.name).toBe('later before adoption')
  })

  it('检视和资源安装不改原文档；明确打开后非空正文只走原writer一次PUT并回读真实receipt', async () => {
    const original = s().documentId
    await published()
    expect(s().documentId).toBe(original)
    expect(puts()).toHaveLength(0)
    expect(opens()).toHaveLength(0)
    expect(p().installation?.document_saved).toBe(false)
    expect(await p().openInstalled()).toBe(true)
    expect(p().phase).toBe('saved')
    expect(puts()).toHaveLength(1)
    expect(opens()).toHaveLength(1)
    const saved = p().saved!
    expect(saved.address).toMatchObject({ projectId: 'installed-project', documentId: s().documentId, format: 'v4' })
    expect(saved.document).toEqual(slots.get(key('installed-project', s().documentId))!.doc)
    expect(saved.revision).toBe(slots.get(key('installed-project', s().documentId))!.revision)
    expect(s().projectFile).toBeNull()
    expect(requests.some(row => row.path.endsWith('/reserve') || row.path.includes('/probe') || row.path.endsWith('/run'))).toBe(false)
    expect(await p().openInstalled()).toBe(false)
  })

  it('空包明确persistEmpty生成一次真实槽位，不把普通empty wrote:true当完成', async () => {
    packageDocument = doc(true)
    await published()
    expect(puts()).toHaveLength(0)
    expect(await p().openInstalled()).toBe(true)
    expect(puts()).toHaveLength(1)
    expect(p().saved?.revision).toMatch(/^[0-9a-f]{32}$/)
    expect(p().saved?.document.canvases[0].objects).toEqual([])
  })

  it.each(['pending', 'needs_review'] as const)('%s配置的已发布503保留path，显式打开不重装/不自动Run', async config => {
    installState = 'installation_unconfirmed'; configuration = config
    await published()
    expect(p().installation).toMatchObject({ state: 'installation_unconfirmed', configuration: config, path: '/new/中文项目' })
    expect(await p().install({ destination: '/new/中文项目' })).toBe(false)
    expect(await p().openInstalled()).toBe(true)
    expect(installs()).toHaveLength(1)
    expect(requests.some(row => row.method === 'PATCH' || row.path.includes('preparation') || row.path.includes('/probe'))).toBe(false)
  })

  it('关闭已发布界面保留receipt，重新打开不需要File也不重复安装', async () => {
    await published()
    p().cancel()
    expect(p().phase).toBe('idle')
    expect(p().installation?.path).toBe('/new/中文项目')
    p().reopen()
    expect(p().phase).toBe('published')
    expect(await p().openInstalled()).toBe(true)
    p().cancel(); p().reopen()
    expect(p().phase).toBe('saved')
    expect(installs()).toHaveLength(1)
    expect(puts()).toHaveLength(1)
  })

  it.each(['cancel', 'projectABA'] as const)('检视等待时%s令响应失权，零资源/文档写入', async change => {
    const blocked = hold('/api/package/inspect')
    const pending = p().inspect(file())
    await blocked.entered
    if (change === 'cancel') p().cancel()
    else { setCurrentProjectId('other-project'); setCurrentProjectId('old-project') }
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().inspection).toBeNull()
    expect(installs()).toHaveLength(0)
    expect(opens()).toHaveLength(0)
  })

  it.each(['resources_installed', 'installation_unconfirmed'] as const)('关闭后迟到%s只保恢复receipt，不重开/不采用', async state => {
    installState = state
    expect(await p().inspect(file())).toBe(true)
    const blocked = hold('/api/package/install')
    const pending = p().install({ destination: '/new/中文项目' })
    await blocked.entered
    p().cancel()
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().phase).toBe('idle')
    expect(p().installation).toBeNull()
    expect(p().receipts).toHaveLength(1)
    expect(p().receipts[0].installation.path).toBe('/new/中文项目')
    expect(opens()).toHaveLength(0)
    p().reopen(p().receipts[0].attemptId)
    expect(p().phase).toBe('published')
  })

  it('换项目并开始新检视后，旧安装仅记history，不覆盖新inspection/phase', async () => {
    expect(await p().inspect(file('A.zip'))).toBe(true)
    const blocked = hold('/api/package/install')
    const pending = p().install({ destination: '/new/中文项目' })
    await blocked.entered
    setCurrentProjectId('other-project')
    packageDocument = doc(true); packageDocument.project.name = 'B'
    expect(await p().inspect(file('B.zip'))).toBe(true)
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().phase).toBe('review')
    expect(p().inspection?.doc.project.name).toBe('B')
    expect(p().installation).toBeNull()
    expect(p().receipts).toHaveLength(1)
    expect(opens()).toHaveLength(0)
  })

  it('资源请求在途重复点击零重复发布', async () => {
    expect(await p().inspect(file())).toBe(true)
    const blocked = hold('/api/package/install')
    const pending = p().install({ destination: '/new/中文项目' })
    await blocked.entered
    expect(await p().install({ destination: '/new/中文项目' })).toBe(false)
    blocked.release(); expect(await pending).toBe(true)
    expect(installs()).toHaveLength(1)
  })

  it.each(['conflict', 'network'] as const)('安装%s失败后更改destination可明确重试，未点击前零自动重发', async failure => {
    expect(await p().inspect(file())).toBe(true)
    installFailure = failure
    expect(await p().install({ destination: '/already/existing' })).toBe(false)
    expect(p().phase).toBe(failure === 'conflict' ? 'error' : 'unconfirmed')
    expect(p().inspection).not.toBeNull()
    expect(p().installation).toBeNull()
    expect(installs()).toHaveLength(1)
    installFailure = null
    expect(await p().install({ destination: '/new/中文项目' })).toBe(true)
    expect(installs()).toHaveLength(2)
    expect(installs().map(row => JSON.parse(String((row.body as FormData).get('options'))).destination)).toEqual(['/already/existing', '/new/中文项目'])
    expect(p().phase).toBe('published')
    expect(opens()).toHaveLength(0)
  })

  it.each(['projectABA', 'loadSeq'] as const)('失败后%s已变，改destination也不能沿旧检视重试', async change => {
    expect(await p().inspect(file())).toBe(true)
    installFailure = 'conflict'
    expect(await p().install({ destination: '/already/existing' })).toBe(false)
    if (change === 'projectABA') { setCurrentProjectId('other-project'); setCurrentProjectId('old-project') }
    else await s().switchDocument(doc(true), s().documentId, undefined, { format: 'v4' })
    installFailure = null
    expect(await p().install({ destination: '/new/中文项目' })).toBe(false)
    expect(installs()).toHaveLength(1)
  })

  it('旧稿写失败保留内存/undo/本机副本，根本不打开新项目', async () => {
    await oldDraft()
    s().commit(literal('latest'), draft => { draft.guides.push({ axis: 'x', pos: 77 }) })
    const originalId = s().documentId, history = s().past
    failWriteProject = 'old-project'
    await published()
    expect(await p().openInstalled()).toBe(false)
    expect(p().phase).toBe('error')
    expect(opens()).toHaveLength(0)
    expect(s().documentId).toBe(originalId)
    expect(s().doc.guides).toEqual([{ axis: 'x', pos: 77 }])
    expect(s().past).toBe(history)
    expect(Object.keys(localStorage).some(name => name.includes(originalId) && localStorage.getItem(name)?.includes('77'))).toBe(true)
  })

  it('旧稿flush等待期间新增编辑，保最新稿且停止项目切换', async () => {
    await oldDraft(); await published()
    const blocked = hold(`/api/autosave/${s().documentId}`, 'PUT')
    const pending = p().openInstalled()
    await blocked.entered
    s().commit(literal('edit during save'), draft => { draft.guides.push({ axis: 'x', pos: 88 }) })
    blocked.release()
    expect(await pending).toBe(false)
    expect(opens()).toHaveLength(0)
    expect(s().doc.guides).toContainEqual({ axis: 'x', pos: 88 })
    expect(currentProjectId()).toBe('old-project')
  })

  it('本机quota与旧磁盘写入同时失败也不丢尚未保护的最新内存稿', async () => {
    await oldDraft(); await published()
    s().commit(literal('latest unsaved'), draft => { draft.guides.push({ axis: 'y', pos: 91 }) })
    const originalId = s().documentId, history = s().past
    const write = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, name, value) {
      if (name.includes('autosave.')) throw new DOMException('quota', 'QuotaExceededError')
      write.call(this, name, value)
    })
    failWriteProject = 'old-project'
    expect(await p().openInstalled()).toBe(false)
    expect(opens()).toHaveLength(0)
    expect(s().documentId).toBe(originalId)
    expect(s().doc.guides).toContainEqual({ axis: 'y', pos: 91 })
    expect(s().past).toBe(history)
  })

  it('打开API迟到时同id新loadSeq禁止采用，保后来文档', async () => {
    await published()
    const blocked = hold('/api/projects/open')
    const pending = p().openInstalled()
    await blocked.entered
    const later = doc(true); later.project.name = 'later document'
    await s().switchDocument(later, s().documentId, undefined, { format: 'v4' })
    blocked.release()
    expect(await pending).toBe(false)
    expect(s().projectMeta.name).toBe('later document')
    expect(currentProjectId()).toBe('old-project')
    expect(puts()).toHaveLength(0)
  })

  it('新正文PUT失败不宣称采用已保存，已发布path仍可恢复，零第二安装', async () => {
    await published(); failWriteProject = 'installed-project'
    expect(await p().openInstalled()).toBe(false)
    expect(p().phase).toBe('error')
    expect(p().saved).toBeNull()
    expect(p().installation?.path).toBe('/new/中文项目')
    expect(installs()).toHaveLength(1)
    expect(puts()).toHaveLength(1)
    expect(s().saveState).toBe('save_error')
    expect(s().doc.objects[0]).toMatchObject({ text: 'package content' })
  })

  it('采用换代期间关闭，保原已发布receipt且不把包正文装到占位稿', async () => {
    await published()
    const blocked = hold('/api/panels')
    const pending = p().openInstalled()
    await blocked.entered
    p().cancel()
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().phase).toBe('idle')
    expect(p().receipts).toHaveLength(1)
    expect(puts()).toHaveLength(0)
  })

  it.each(['cancel', 'projectABA', 'loadSeq'] as const)('真实AS回读等待时%s不授迟到saved资格', async change => {
    await published()
    const blocked = hold('/api/autosave/*', 'GET')
    const pending = p().openInstalled()
    await blocked.entered
    if (change === 'cancel') p().cancel()
    else if (change === 'projectABA') { setCurrentProjectId('other-project'); setCurrentProjectId('installed-project') }
    else {
      const later = doc(true); later.project.name = 'later same id'
      await s().switchDocument(later, s().documentId, undefined, { format: 'v4' })
    }
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().saved).toBeNull()
    expect(p().receipts[0].saved).toBeNull()
    expect(p().phase).not.toBe('saved')
    if (change === 'loadSeq') expect(s().projectMeta.name).toBe('later same id')
  })

  it('打开网络失败保原稿及资源receipt，后续明确重试仅重试打开', async () => {
    await published(); failOpen = true
    const originalId = s().documentId
    expect(await p().openInstalled()).toBe(false)
    expect(s().documentId).toBe(originalId)
    expect(p().installation?.path).toBe('/new/中文项目')
    expect(await p().install({ destination: '/another/path' })).toBe(false)
    failOpen = false
    expect(await p().openInstalled()).toBe(true)
    expect(installs()).toHaveLength(1)
    expect(opens()).toHaveLength(2)
  })

  it('首次PUT已成功后取消确认回读，迟到保存事实只归原记录，继续保最新稿和undo', async () => {
    await published()
    const blocked = hold('/api/autosave/*', 'GET', () => puts().length > 0)
    const pending = p().openInstalled()
    await blocked.entered
    const id = s().documentId
    expect(puts()).toHaveLength(1)
    expect(slots.get(key('installed-project', id))?.doc).toBeDefined()
    p().cancel()
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().phase).toBe('idle')
    expect(p().saved).toBeNull()
    expect(p().receipts[0].saved?.address.documentId).toBe(id)
    s().commit(literal('actual later edit'), draft => { draft.guides.push({ axis: 'x', pos: 123 }) })
    expect((await saveNowWithResult()).wrote).toBe(true)
    const history = s().past, count = puts().length, opened = opens().length
    p().reopen()
    expect(p().phase).toBe('saved')
    expect(await p().continueEditing()).toBe(true)
    expect(s().documentId).toBe(id)
    expect(s().doc.guides).toContainEqual({ axis: 'x', pos: 123 })
    expect(s().past).toBe(history)
    expect(puts()).toHaveLength(count)
    expect(opens()).toHaveLength(opened)
    expect(installs()).toHaveLength(1)
  })

  it.each(['same', 'other'] as const)('首次PUT已成功但确认失败，重开%s项目沿已采用稿继续而不重套包', async destination => {
    await published()
    const blocked = hold('/api/autosave/*', 'GET', () => puts().length > 0)
    const pending = p().openInstalled()
    await blocked.entered
    const id = s().documentId
    expect(slots.get(key('installed-project', id))?.doc).toBeDefined()
    failReadProject = 'installed-project'
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().saved).toBeNull()
    expect(p().receipts[0].saved).toBeNull()
    failReadProject = null
    s().commit(literal('edit after unconfirmed adoption'), draft => { draft.guides.push({ axis: 'y', pos: 124 }) })
    expect((await saveNowWithResult()).wrote).toBe(true)
    const history = s().past
    if (destination === 'other') await useProjectStore.getState().open('/other')
    const count = puts().length
    p().cancel(); p().reopen()
    expect(p().phase).toBe('published')
    expect(await p().openInstalled()).toBe(true)
    expect(currentProjectId()).toBe('installed-project')
    expect(s().documentId).toBe(id)
    expect(s().doc.guides).toContainEqual({ axis: 'y', pos: 124 })
    if (destination === 'same') {
      expect(s().past).toBe(history)
      expect(puts()).toHaveLength(count)
      expect(opens()).toHaveLength(1)
    }
    expect(installs()).toHaveLength(1)
  })

  it('首次PUT后的回读晚于新检视，只记录原工作稿保存事实不认领新界面', async () => {
    await published()
    const blocked = hold('/api/autosave/*', 'GET', () => puts().length > 0)
    const pending = p().openInstalled()
    await blocked.entered
    const id = s().documentId
    p().cancel()
    packageDocument = doc(); packageDocument.project.name = 'new inspection'
    expect(await p().inspect(file('another.zip'))).toBe(true)
    const inspection = p().inspection
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().phase).toBe('review')
    expect(p().inspection).toBe(inspection)
    expect(p().installation).toBeNull()
    expect(p().saved).toBeNull()
    expect(p().receipts[0].saved?.address.documentId).toBe(id)
    expect(puts()).toHaveLength(1)
  })

  it('正文已装入但首次hydrate在途时取消并换稿，重开保后来明确工作稿', async () => {
    await published()
    const blocked = hold('/api/autosave/*', 'GET')
    const pending = p().openInstalled()
    await blocked.entered
    const adoptedId = s().documentId
    expect(s().doc.objects[0]).toMatchObject({ text: 'package content' })
    expect(puts()).toHaveLength(0)
    p().cancel()
    const later = doc(); later.canvases[0].guides.push({ axis: 'x', pos: 125 })
    const switching = s().switchDocument(later, 'explicit-later-working', undefined, { format: 'v4' })
    expect(s().documentId).toBe('explicit-later-working')
    blocked.release()
    expect(await pending).toBe(false)
    expect(await switching).toBe(true)
    expect((await saveNowWithResult()).wrote).toBe(true)
    expect(p().receipts[0].adopted?.documentId).toBe(adoptedId)
    expect(p().receipts[0].saved).toBeNull()
    const history = s().past, count = puts().length, opened = opens().length
    p().reopen()
    expect(await p().openInstalled()).toBe(true)
    expect(s().documentId).toBe('explicit-later-working')
    expect(s().doc.guides).toContainEqual({ axis: 'x', pos: 125 })
    expect(s().past).toBe(history)
    expect(puts()).toHaveLength(count)
    expect(opens()).toHaveLength(opened)
  })

  it.each(['projectABA', 'loadSeq'] as const)('首次PUT后%s使旧确认失权，不授后来稿保存资格', async change => {
    await published()
    const blocked = hold('/api/autosave/*', 'GET', () => puts().length > 0)
    const pending = p().openInstalled()
    await blocked.entered
    const id = s().documentId
    expect(slots.get(key('installed-project', id))?.doc).toBeDefined()
    if (change === 'projectABA') { setCurrentProjectId('other-project'); setCurrentProjectId('installed-project') }
    else {
      const later = doc(true); later.project.name = 'later document with same id'
      await s().switchDocument(later, id, undefined, { format: 'v4' })
    }
    blocked.release()
    expect(await pending).toBe(false)
    expect(p().saved).toBeNull()
    expect(p().receipts[0].saved).toBeNull()
    expect(p().phase).not.toBe('saved')
    if (change === 'loadSeq') expect(s().projectMeta.name).toBe('later document with same id')
  })
})
