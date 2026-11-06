import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal, formatMessage } from '@/i18n'
import { emptyProject, type ProjectDocument } from '@/types/document'
import { setCurrentProjectId } from '@/lib/session'
import { documentNamespaceKey } from '@/lib/documentFormat'
import { forgetProjectFilesInSession } from '@/lib/projectFile'
import { projectFileEditor, type ProjectFileAxis, type ProjectFileView, type ProjectFileTicket } from '@/lib/projectFileSession'
import { captureSaveContext } from './saveContext'
import { openLayoutDocument, runManualSave } from './actions'
import { applyDerivedUpdate, flushAutosave, saveNow, startAutosave, useDocumentStore, readAutosaveDoc,
  prepareProjectFileSave, finishProjectFileSave, overwriteDisk, reloadFromDisk } from './documentStore'
import { resetRestoreStateForTests } from './documentRestore'
import { useUiStore } from './uiStore'
import { onLayoutSaved } from '@/lib/layoutSaved'

const s = () => useDocumentStore.getState()
const serverAxis = { editor_id: 'old-browser', sequence: 5 }
let workingId = ''
let serial = 0
let stop = () => {}
let format: 'legacy' | 'v4' = 'legacy'
let body: ProjectDocument
let revision: string
let axis: ProjectFileAxis
let syncedAxis: ProjectFileAxis
let binding: ProjectFileView['binding']
let intent: number
let ticket: ProjectFileTicket | null
let failMetadata = false
let readUnavailable = false
let rejectEdit = false
let metadataKnown = true
let trackedRevision: string | null = null
let holdRecoveryWrite: Promise<void> | null = null
let releaseRecoveryWrite: (() => void) | undefined
let recoveryWriteStarted: (() => void) | undefined
let failRecoveryMetadata = false
let holdRead: Promise<void> | null = null
let releaseRead: (() => void) | undefined
let holdSlotRead: Promise<void> | null = null
let releaseSlotRead: (() => void) | undefined
let slotReadStarted: (() => void) | undefined
const requests: { url: URL; method: string; body: unknown }[] = []
const hex = (value: number) => value.toString(16).padStart(32, '0')
const view = (): ProjectFileView => ({
  version: 1, status: 'ok', binding: binding && { ...binding, dirty: JSON.stringify(axis) !== JSON.stringify(syncedAxis) },
  edit: { autosave_revision: trackedRevision ?? revision, axis: structuredClone(axis), known: metadataKnown }, intent_sequence: intent,
})
const response = (data: unknown, status = 200, rev?: string) => Response.json(data, {
  status, headers: { ...(format === 'v4' ? { 'X-Tavotto-Document-Format': 'v4' } : {}), ...(rev ? { 'X-Tavotto-Revision': rev } : {}) },
})
const confirm = (sequence: number, namedRevision: string) => {
  if (!ticket || ticket.sequence !== sequence) return { state: 'superseded' }
  binding = { sequence, name: ticket.name, file: ticket.file, revision: namedRevision, dirty: false }
  syncedAxis = structuredClone(ticket.axis)
  ticket = null
  return { state: 'bound', ...view() }
}

beforeEach(async () => {
  setCurrentProjectId(null)
  resetRestoreStateForTests()
  await s().switchDocument(emptyProject(), `reset-${++serial}`)
  projectFileEditor.clear(); forgetProjectFilesInSession(); localStorage.clear(); requests.length = 0
  workingId = `working-${serial}`; format = 'legacy'; revision = hex(10); axis = structuredClone(serverAxis)
  syncedAxis = structuredClone(serverAxis); intent = 3; ticket = null; failMetadata = false; readUnavailable = false; rejectEdit = false; holdRead = null
  holdSlotRead = null; slotReadStarted = undefined
  metadataKnown = true; trackedRevision = null; holdRecoveryWrite = null; recoveryWriteStarted = undefined; failRecoveryMetadata = false
  binding = { sequence: 3, name: 'A', file: 'tavottofile/A.json', revision: hex(3), dirty: false }
  body = emptyProject(); body.project.name = 'Different title'
  body.canvases[0].objects = [{ id: 't', type: 'text', text: 'original', sizePt: 9, bold: false, color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8 }]
  vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = new URL(String(input), 'http://test')
    const path = url.pathname.replace('/api/documents/v4/', '/api/')
    const method = init?.method ?? 'GET'
    const data = init?.body ? JSON.parse(String(init.body)) : null
    requests.push({ url, method, body: data })
    if (path === `/api/layout-session/project-file/${workingId}`) {
      const captured = structuredClone(view())
      if (holdRead) await holdRead
      return readUnavailable ? response({ code: 'session_unavailable', error: 'metadata unavailable' }, 503) : response(captured)
    }
    if (path === `/api/autosave/${workingId}`) {
      if (method === 'GET') {
        const captured = structuredClone(body), capturedRevision = revision
        if (holdSlotRead) { slotReadStarted?.(); await holdSlotRead }
        return response(captured, 200, capturedRevision)
      }
      if (method === 'PUT') {
        const edit = JSON.parse(url.searchParams.get('project_file_edit') ?? 'null')
        const proof = JSON.parse(url.searchParams.get('project_file_recovery') ?? 'null')
        if (proof) {
          recoveryWriteStarted?.()
          if (holdRecoveryWrite) await holdRecoveryWrite
          if (proof.revision !== revision || JSON.stringify(proof.expected) !== JSON.stringify(view())) return response({ code: 'stale_write', project_file: { state: 'unconfirmed' } }, 409)
          if (!edit?.axis || JSON.stringify(edit.axis) === JSON.stringify(axis) || JSON.stringify(edit.axis) === JSON.stringify(syncedAxis)) return response({ code: 'stale_write', project_file: { state: 'superseded' } }, 409)
        }
        if ((!proof && rejectEdit) || !edit || edit.base_revision !== revision) return response({ code: 'external_change', revision }, 409)
        body = data; revision = hex(Number.parseInt(revision, 16) + 1)
        if (proof && failRecoveryMetadata) return response({ ok: true, revision, metadata: 'failed', project_file: { state: 'unconfirmed' } })
        axis = edit.axis
        if (proof) { rejectEdit = false; metadataKnown = true; trackedRevision = null }
        return response({ ok: true, saved_at: 1, revision, project_file: { state: 'accepted', ...view() } })
      }
    }
    if (path.endsWith(`/project-file/${workingId}/reserve`)) {
      if (data.autosave_revision !== revision || data.expected_binding_sequence !== binding?.sequence) return response({ state: 'unconfirmed' })
      ticket = { sequence: ++intent, binding_sequence: binding?.sequence ?? 0, name: data.name,
        file: format === 'v4' ? `tavottofile/documents/v4/${data.name}.json` : `tavottofile/${data.name}.json`,
        autosave_revision: data.autosave_revision, axis: structuredClone(data.axis) }
      return response({ state: 'reserved', ticket })
    }
    if (path.endsWith(`/project-file/${workingId}/confirm`)) return response(confirm(data.intent_sequence, data.revision))
    if (path.includes(`/project-file/${workingId}/intent/`)) {
      const sequence = Number(path.split('/').at(-1))
      if (ticket?.sequence === sequence) ticket = null
      return response({ state: 'cancelled' })
    }
    if (path === '/api/layouts/A' && method === 'POST') {
      const sequence = Number(url.searchParams.get('binding_intent'))
      return response({ ok: true, revision: hex(99), name: 'A', file: binding!.file,
        project_file: failMetadata ? { state: 'unavailable' } : confirm(sequence, hex(99)) })
    }
    return response({}, 404)
  }))
  setCurrentProjectId('project-A')
  useUiStore.setState({ status: null, layoutOpen: false })
})
afterEach(() => { stop(); stop = () => {}; releaseRead?.(); releaseRead = undefined; releaseSlotRead?.(); releaseSlotRead = undefined; releaseRecoveryWrite?.(); releaseRecoveryWrite = undefined; setCurrentProjectId(null); vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function install() {
  if (format === 'v4') body.schema = 4
  const loaded = await readAutosaveDoc(workingId, format)
  await s().switchDocument(loaded.doc!, workingId, undefined, format === 'v4' ? { format } : undefined)
  stop = startAutosave()
  await saveNow()
  requests.length = 0
}
const edit = (pos = 9) => s().commit(literal('user edit'), draft => { draft.guides.push({ axis: 'x', pos }) })
async function enterUnknown(selected: 'legacy' | 'v4' = 'legacy', known = false) {
  format = selected
  await install(); edit(11)
  trackedRevision = revision; revision = hex(80); metadataKnown = known; rejectEdit = true
  body.project.name = 'external body'
  expect(await saveNow()).toBe('conflict')
  requests.length = 0
}

describe('明确用户裁决的编辑轴恢复', () => {
  it.each(['ok', 'latest', 'recovery'] as const)('404旧后端fallback重载等待期间新增编辑：备份=%s，保护失败不采用磁盘稿', async failure => {
    await enterUnknown()
    const originalDisk = structuredClone(body)
    const fetchBefore = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
      const path = new URL(String(input), 'http://test').pathname
      if (path.includes('/layout-session/project-file/') && (!init?.method || init.method === 'GET')) return response({}, 404)
      return fetchBefore(input as RequestInfo | URL, init)
    }))
    holdSlotRead = new Promise(resolve => { releaseSlotRead = resolve })
    const reached = new Promise<void>(resolve => { slotReadStarted = resolve })
    const reloading = reloadFromDisk()
    await reached
    edit(77) // 本机副本尚未被防抖刷新，必须由安装前保护实际抓到。
    const setItem = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key, value) {
      if ((failure === 'latest' && key === `tavotto.autosave.${workingId}`)
        || (failure === 'recovery' && key === `tavotto.recovery.${workingId}`)) throw new Error('quota during reload')
      return setItem.call(this, key, value)
    })
    releaseSlotRead!(); holdSlotRead = null
    expect(await reloading).toBe(failure === 'ok')
    expect(body).toEqual(originalDisk)
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(0)
    if (failure === 'ok') {
      expect(s().doc.guides).toEqual([])
      expect(s().docNotice?.kind).toBe('recovery')
      const backup = JSON.parse(localStorage.getItem(`tavotto.recovery.${workingId}`)!)
      expect(backup.canvases[0].guides).toEqual([{ axis: 'x', pos: 11 }, { axis: 'x', pos: 77 }])
    } else {
      expect(s().doc.guides).toEqual([{ axis: 'x', pos: 11 }, { axis: 'x', pos: 77 }])
      expect(s().saveState).toBe('conflict')
      if (failure === 'recovery') expect(JSON.parse(localStorage.getItem(`tavotto.autosave.${workingId}`)!).canvases[0].guides).toHaveLength(2)
    }
  })

  it.each(['legacy', 'v4'] as const)('%s unknown时明确覆盖一次采用新branch，保named原目标/基线/dirty，后续普通保存不带proof', async selected => {
    await enterUnknown(selected)
    const observed = structuredClone(view())
    const previousAxis = structuredClone(axis)
    expect(await overwriteDisk()).toBe('saved')
    const recovered = requests.find(request => request.method === 'PUT')!
    expect(JSON.parse(recovered.url.searchParams.get('project_file_recovery')!)).toEqual({ revision: hex(80), expected: observed })
    expect(observed.edit.autosave_revision).not.toBe(hex(80))
    const evidence = JSON.parse(recovered.url.searchParams.get('project_file_edit')!)
    expect(evidence.base_revision).toBe(hex(80))
    expect(evidence.axis.editor_id).not.toBe(previousAxis?.editor_id)
    expect(evidence.axis.sequence).toBe(1)
    expect(s().projectFile).toMatchObject({ name: 'A', revision: hex(3), dirty: true })
    edit(22); expect(await saveNow()).toBe('saved')
    const ordinary = requests.filter(request => request.method === 'PUT').at(-1)!
    expect(ordinary.url.searchParams.has('project_file_recovery')).toBe(false)
    expect(JSON.parse(ordinary.url.searchParams.get('project_file_edit')!).axis).toEqual({ ...evidence.axis, sequence: 2 })
  })

  it.each(['legacy', 'v4'] as const)('%s 重载选择磁盘正文并恢复轴，读取期间的新编辑完整保原恢复槽', async selected => {
    await enterUnknown(selected)
    holdSlotRead = new Promise(resolve => { releaseSlotRead = resolve })
    const reached = new Promise<void>(resolve => { slotReadStarted = resolve })
    const reloading = reloadFromDisk()
    await reached
    edit(77) // 尚在防抖窗口；安装前必须主动保护这笔未进本机副本的编辑。
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(0)
    releaseSlotRead!(); holdSlotRead = null
    expect(await reloading).toBe(true)
    expect(s().projectMeta.name).toBe('external body')
    const backup = localStorage.getItem(documentNamespaceKey(`tavotto.recovery.${workingId}`, selected))
    expect(backup).not.toBeNull()
    expect(JSON.parse(backup!).canvases[0].guides).toEqual([{ axis: 'x', pos: 11 }, { axis: 'x', pos: 77 }])
    expect(s().projectFile).toMatchObject({ name: 'A', revision: hex(3), dirty: true })
    edit(33); expect(await saveNow()).toBe('saved')
  })

  it('跨editor superseded但known仍true也必须新分支，不能复用旧编辑器身份', async () => {
    await enterUnknown('legacy', true)
    const before = structuredClone(axis)
    expect(await overwriteDisk()).toBe('saved')
    expect(axis?.editor_id).not.toBe(before?.editor_id)
    expect(s().projectFile?.dirty).toBe(true)
  })

  it.each(['latest', 'recovery'] as const)('重载的%s副本写入失败，保内存新编辑和原磁盘正文，不继续安装', async failure => {
    await enterUnknown()
    const original = structuredClone(body)
    const setItem = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key, value) {
      if (key.includes(failure === 'recovery' ? 'recovery.' : 'autosave.')) throw new Error('quota')
      return setItem.call(this, key, value)
    })
    expect(await reloadFromDisk()).toBe(false)
    expect(s().doc.guides).toEqual([{ axis: 'x', pos: 11 }])
    expect(s().saveState).toBe('conflict')
    expect(body).toEqual(original)
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(0)
  })

  it.each(['binding', 'body'] as const)('观察后%s变化，冻结proof被409拒绝；普通重试不刷新，重新明确裁决才新proof/branch', async changed => {
    await enterUnknown()
    holdSlotRead = new Promise(resolve => { releaseSlotRead = resolve })
    const reached = new Promise<void>(resolve => { slotReadStarted = resolve })
    const overwriting = overwriteDisk()
    await reached
    if (changed === 'binding') binding!.sequence = ++intent
    else revision = hex(90)
    releaseSlotRead!(); holdSlotRead = null
    expect(await overwriting).toBe('conflict')
    const first = requests.find(request => request.method === 'PUT')!
    expect(JSON.parse(first.url.searchParams.get('project_file_recovery')!).expected.binding.sequence).toBe(3)
    const rejectedAxis = JSON.parse(first.url.searchParams.get('project_file_edit')!).axis
    expect(await saveNow()).toBe('conflict')
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(1)
    expect(await overwriteDisk()).toBe('saved')
    const second = requests.filter(request => request.method === 'PUT').at(-1)!
    expect(JSON.parse(second.url.searchParams.get('project_file_recovery')!).expected.binding.sequence).toBe(changed === 'binding' ? 4 : 3)
    expect(JSON.parse(second.url.searchParams.get('project_file_edit')!).axis.editor_id).not.toBe(rejectedAxis.editor_id)
  })

  it('两次明确裁决读取乱序，仅较晚意图可提交且旧finally不撤掉新barrier', async () => {
    await enterUnknown()
    holdSlotRead = new Promise(resolve => { releaseSlotRead = resolve })
    let reads = 0
    slotReadStarted = () => { reads++ }
    const earlier = overwriteDisk()
    await vi.waitFor(() => expect(reads).toBe(1))
    const later = overwriteDisk()
    await vi.waitFor(() => expect(reads).toBe(2))
    releaseSlotRead!(); holdSlotRead = null
    await Promise.all([earlier, later])
    expect(s().saveState).toBe('saved')
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(1)
    expect(body.canvases[0].guides).toEqual([{ axis: 'x', pos: 11 }])
  })

  it('恢复PUT在途继续编辑只存本机，成功后同writer续存最新正文且不重复proof', async () => {
    await enterUnknown()
    holdRecoveryWrite = new Promise(resolve => { releaseRecoveryWrite = resolve })
    const reached = new Promise<void>(resolve => { recoveryWriteStarted = resolve })
    const overwriting = overwriteDisk()
    await reached
    edit(66); flushAutosave()
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(1)
    releaseRecoveryWrite!(); holdRecoveryWrite = null
    await overwriting; expect(await saveNow()).toBe('saved')
    const writes = requests.filter(request => request.method === 'PUT')
    expect(writes.filter(request => request.url.searchParams.has('project_file_recovery'))).toHaveLength(1)
    expect(body.canvases[0].guides).toEqual([{ axis: 'x', pos: 11 }, { axis: 'x', pos: 66 }])
  })

  it('恢复正文已写但metadata失败，保本机新编辑/元信息提醒，不自动重发正文或刷新proof', async () => {
    await enterUnknown(); failRecoveryMetadata = true
    holdRecoveryWrite = new Promise(resolve => { releaseRecoveryWrite = resolve })
    const reached = new Promise<void>(resolve => { recoveryWriteStarted = resolve })
    const overwriting = overwriteDisk()
    await reached
    edit(99); flushAutosave()
    releaseRecoveryWrite!(); holdRecoveryWrite = null
    await overwriting
    expect(body.canvases[0].guides).toEqual([{ axis: 'x', pos: 11 }])
    expect(s().doc.guides).toEqual([{ axis: 'x', pos: 11 }, { axis: 'x', pos: 99 }])
    expect(s().restoreMetaIssue).not.toBeNull()
    expect(s().saveState).not.toBe('save_error')
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(1)
    expect(JSON.parse(localStorage.getItem(`tavotto.autosave.${workingId}`)!).canvases[0].guides).toHaveLength(2)
  })

  it.each([false, true])('恢复PUT迟到且同id已新安装，失败=%s也不能改新loadSeq保存状态', async failure => {
    await enterUnknown()
    holdRecoveryWrite = new Promise(resolve => { releaseRecoveryWrite = resolve })
    const reached = new Promise<void>(resolve => { recoveryWriteStarted = resolve })
    const overwriting = overwriteDisk()
    await reached
    useDocumentStore.setState({ loadSeq: s().loadSeq + 1, saveState: 'dirty', lastPersisted: 123 })
    if (failure) binding!.sequence = ++intent
    releaseRecoveryWrite!(); holdRecoveryWrite = null
    await overwriting
    expect(s().saveState).toBe('dirty')
    expect(s().lastPersisted).toBe(123)
  })

  it.each(['reload', 'overwrite'] as const)('%s读取元信息失败保原冲突/本机稿，不退legacy发送', async choice => {
    await enterUnknown(); readUnavailable = true
    const original = structuredClone(body)
    expect(await (choice === 'reload' ? reloadFromDisk() : overwriteDisk())).toBe(choice === 'reload' ? false : 'conflict')
    expect(s().saveState).toBe('conflict')
    expect(body).toEqual(original)
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(0)
    expect(localStorage.getItem(`tavotto.autosave.${workingId}`)).not.toBeNull()
  })

  it.each([{ choice: 'reload', sameId: false }, { choice: 'overwrite', sameId: false },
    { choice: 'reload', sameId: true }, { choice: 'overwrite', sameId: true }] as const)('$choice读取迟到遇新安装同id=$sameId，无权写入或重载后来的文档', async ({ choice, sameId }) => {
    await enterUnknown()
    holdSlotRead = new Promise(resolve => { releaseSlotRead = resolve })
    const reached = new Promise<void>(resolve => { slotReadStarted = resolve })
    const resolving = choice === 'reload' ? reloadFromDisk() : overwriteDisk()
    await reached
    if (sameId) useDocumentStore.setState({ loadSeq: s().loadSeq + 1 })
    else await s().switchDocument(emptyProject(), 'different-document')
    releaseSlotRead!(); holdSlotRead = null
    await resolving
    expect(s().documentId).toBe(sameId ? workingId : 'different-document')
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(0)
  })
})

describe('后端工作副本关联的前端消费链（有状态 HTTP 边界）', () => {
  it.each(['legacy', 'v4'] as const)('%s 冷读继承既定目标，标题不同的 CtrlS仍写A并携原绑定revision/票据/精确AS正文', async selected => {
    format = selected
    if (format === 'v4') binding!.file = 'tavottofile/documents/v4/A.json'
    await install()
    expect(s().projectFile).toMatchObject({ name: 'A', sequence: 3, dirty: false })
    edit()
    await runManualSave()
    const named = requests.filter(request => request.method === 'POST' && request.url.pathname.endsWith('/layouts/A'))
    expect(named).toHaveLength(1)
    expect(named[0].url.searchParams.get('base_revision')).toBe(hex(3))
    expect(named[0].url.searchParams.get('working_doc_id')).toBe(workingId)
    expect(named[0].url.searchParams.get('binding_intent')).toBe('4')
    expect(named[0].body).toEqual(body)
    const writes = requests.filter(request => request.method === 'PUT' && request.url.pathname.endsWith(`/autosave/${workingId}`))
    expect(writes.length).toBeGreaterThan(0)
    const evidence = JSON.parse(writes[0].url.searchParams.get('project_file_edit')!)
    expect(evidence.axis).toMatchObject({ sequence: 1 })
    expect(evidence.axis.editor_id).not.toBe(serverAxis.editor_id)
    expect(evidence.base_axis).toEqual(serverAxis)
    expect(s().projectFile).toMatchObject({ name: 'A', sequence: 4, revision: hex(99), dirty: false })
  })

  it('仅用户编辑推进轴；派生同步保同轴，清本机缓存再装仍从后端得A+dirty', async () => {
    await install()
    applyDerivedUpdate({ doc: { ...s().doc, page: { ...s().doc.page, bg: '#fafafa' } } })
    await saveNow()
    expect(body.canvases[0].page.bg).toBe('#fafafa')
    expect(axis).toEqual(serverAxis)
    expect(s().projectFile?.dirty).toBe(false)
    edit(); await saveNow()
    expect(s().projectFile?.dirty).toBe(true)
    projectFileEditor.clear(); forgetProjectFilesInSession(); localStorage.clear()
    await s().switchDocument(body, workingId)
    await saveNow()
    expect(s().projectFile).toMatchObject({ name: 'A', dirty: true, revision: hex(3) })
  })

  it('读回AS后、取得票据前继续编辑：命名正文和保存moment仍是已核revision那份，新编辑保dirty', async () => {
    await install(); edit(9)
    holdSlotRead = new Promise(resolve => { releaseSlotRead = resolve })
    const reached = new Promise<void>(resolve => { slotReadStarted = resolve })
    let savedGuides: unknown
    const off = onLayoutSaved((via, { moment }) => { if (via === 'project_file') savedGuides = moment.identity.doc.guides })
    try {
      const saving = runManualSave()
      await reached
      edit(88)
      releaseSlotRead!(); holdSlotRead = null
      await saving
      const named = requests.find(request => request.method === 'POST' && request.url.pathname.endsWith('/layouts/A'))!
      expect((named.body as ProjectDocument).canvases[0].guides).toEqual([{ axis: 'x', pos: 9 }])
      expect(savedGuides).toEqual([{ axis: 'x', pos: 9 }])
      expect(s().doc.guides).toEqual([{ axis: 'x', pos: 9 }, { axis: 'x', pos: 88 }])
      expect(s().projectFile?.dirty).toBe(true)
    } finally { off() }
  })

  it('hydrate等待期间只保本机副本，结束后重新捕获最新正文/继承轴，不排旧body后洗祖先', async () => {
    holdRead = new Promise(resolve => { releaseRead = resolve })
    const loaded = await readAutosaveDoc(workingId)
    const opening = s().switchDocument(loaded.doc!, workingId)
    await vi.waitFor(() => expect(s().documentId).toBe(workingId))
    stop = startAutosave()
    edit(17)
    flushAutosave()
    expect(localStorage.getItem(`tavotto.autosave.${workingId}`)).not.toBeNull()
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(0)
    releaseRead!(); holdRead = null
    await opening; await saveNow()
    const writes = requests.filter(request => request.method === 'PUT' && request.url.pathname.endsWith(`/autosave/${workingId}`))
    expect(writes.length).toBeGreaterThan(0)
    expect((writes[0].body as ProjectDocument).canvases[0].guides).toEqual([{ axis: 'x', pos: 17 }])
    expect(JSON.parse(writes[0].url.searchParams.get('project_file_edit')!).base_axis).toEqual(serverAxis)
  })

  it('另存A→B→A的较早回执不因文件名相同而改绑', async () => {
    await install()
    const scope = captureSaveContext()
    const early = await prepareProjectFileSave('A', scope)
    const middle = await prepareProjectFileSave('B', scope)
    const later = await prepareProjectFileSave('A', scope)
    expect(middle!.ticket.name).toBe('B')
    expect(early!.ticket.sequence).toBeLessThan(later!.ticket.sequence)
    expect(finishProjectFileSave(early!, { ...view(), state: 'bound', intent_sequence: early!.ticket.sequence,
      binding: { ...binding!, sequence: early!.ticket.sequence, revision: hex(99) } }, hex(99))).toBe(false)
    expect(finishProjectFileSave(middle!, { ...view(), state: 'bound', intent_sequence: middle!.ticket.sequence,
      binding: { ...binding!, name: 'B', file: 'tavottofile/B.json', sequence: middle!.ticket.sequence, revision: hex(99) } }, hex(99))).toBe(false)
    const confirmed = confirm(later!.ticket.sequence, hex(100))
    expect(finishProjectFileSave(later!, confirmed as ReturnType<typeof view> & { state: 'bound' }, hex(100))).toBe(true)
    expect(s().projectFile?.sequence).toBe(later!.ticket.sequence)
  })

  it.each([false, true])('命名正文已写但metadata失败；确认重试不重写旧正文，有新编辑=%s时重新捕获上下文续存', async changed => {
    await install(); edit(); failMetadata = true
    await runManualSave()
    expect(formatMessage(useUiStore.getState().status)).toContain('已写入')
    expect(ticket).not.toBeNull()
    const posts = () => requests.filter(request => request.method === 'POST' && request.url.pathname.endsWith('/layouts/A'))
    expect(posts()).toHaveLength(1)
    if (changed) edit(22)
    failMetadata = false
    await runManualSave()
    expect(posts()).toHaveLength(changed ? 2 : 1)
    expect(s().projectFile?.dirty).toBe(false)
    expect(s().projectFile?.revision).toBe(hex(99))
  })

  it('503保留已有绑定/本机副本，不降legacy写；明确重试后可恢复', async () => {
    await install()
    readUnavailable = true
    // 同一地址重新读取时，旧本机目标是已知事实，但读不到后端不等于无绑定。
    projectFileEditor.beginInstall({ projectId: 'project-A', documentId: workingId, format })
    edit()
    expect(await saveNow()).toBe('save_error')
    expect(s().projectFile).toMatchObject({ name: 'A', revision: hex(3), dirty: true })
    expect(localStorage.getItem(`tavotto.autosave.${workingId}`)).not.toBeNull()
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(0)
    readUnavailable = false
    expect(await saveNow()).toBe('saved')
    expect(s().projectFile?.name).toBe('A')
  })

  it('后端判轴谱系不明时保原正文/关联基线，不删query自动重试', async () => {
    await install()
    const original = structuredClone(body)
    rejectEdit = true
    edit()
    expect(await saveNow()).toBe('conflict')
    expect(body).toEqual(original)
    expect(s().projectFile).toMatchObject({ name: 'A', revision: hex(3), dirty: true })
    expect(requests.filter(request => request.method === 'PUT').every(request => request.url.searchParams.has('project_file_edit'))).toBe(true)
    expect(localStorage.getItem(`tavotto.autosave.${workingId}`)).not.toBeNull()
  })

  it('A的hydrate迟到且已安装B，不关联到后来的文档，旧打开不再收尾B', async () => {
    const loaded = await readAutosaveDoc(workingId)
    holdRead = new Promise(resolve => { releaseRead = resolve })
    const oldId = workingId
    const opening = s().switchDocument(loaded.doc!, oldId)
    await vi.waitFor(() => expect(s().documentId).toBe(oldId))
    workingId = `${oldId}-B`; holdRead = null
    body = emptyProject(); body.canvases[0].objects = loaded.doc!.canvases[0].objects
    revision = hex(30); intent = 1
    binding = { sequence: 1, name: 'B', file: 'tavottofile/B.json', revision: hex(2), dirty: false }
    expect(await s().switchDocument(body, workingId)).toBe(true)
    releaseRead!()
    expect(await opening).toBe(false)
    expect(s().documentId).toBe(workingId)
    expect(s().projectFile).toMatchObject({ name: 'B', sequence: 1 })
  })

  it.each(['autosave', 'axis', 'unknown'] as const)('明确打开的初始化%s不满足全新条件，不伪造null预留或确认clean关联', async existing => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
      const path = new URL(String(input), 'http://test').pathname
      calls.push(path)
      if (/\/layout-session\/project-file\/[^/]+$/.test(path)) return response({
        version: 1, status: 'ok', binding: null, intent_sequence: 0,
        edit: { known: existing !== 'unknown', autosave_revision: existing === 'autosave' ? hex(40) : null,
          axis: existing === 'axis' ? serverAxis : null },
      })
      if (init?.method === 'PUT') return response({ code: 'external_change', revision: hex(40) }, 409)
      return response({}, 404)
    }))
    expect(await openLayoutDocument(body, { projectId: 'project-A', name: 'Chosen', file: 'tavottofile/Chosen.json', revision: hex(7), dirty: false })).toBe(true)
    expect(calls.some(path => path.endsWith('/reserve') || path.endsWith('/confirm'))).toBe(false)
    expect(s().projectFile).toMatchObject({ name: 'Chosen', revision: hex(7) })
    expect(s().projectFile?.sequence).toBeUndefined()
    expect(s().restoreMetaIssue).not.toBeNull()
  })

  it.each([{ late: false, editDuringRead: false, failConfirm: false }, { late: true, editDuringRead: false, failConfirm: false },
    { late: false, editDuringRead: true, failConfirm: false }, { late: false, editDuringRead: false, failConfirm: true }])(
    '显式命名打开先预留初始票据再AS；确认迟到=$late，hydrate中编辑=$editDuringRead，确认503=$failConfirm', async ({ late, editDuringRead, failConfirm }) => {
    const slots = new Map<string, { document: ProjectDocument; axis: ProjectFileAxis; binding: ProjectFileView['binding'] }>()
    const intents = new Map<string, number>()
    const installAxes = new Map<string, ProjectFileAxis>()
    const seen: { path: string; method: string; data: Record<string, unknown> | null }[] = []
    let releaseConfirm: (() => void) | undefined
    const confirmGate = new Promise<void>(resolve => { releaseConfirm = resolve })
    let confirmStarted: (() => void) | undefined
    const reachedConfirm = new Promise<void>(resolve => { confirmStarted = resolve })
    let confirmAttempts = 0
    vi.stubGlobal('fetch', vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = new URL(String(input), 'http://test'), path = url.pathname
      const method = init?.method ?? 'GET', data = init?.body ? JSON.parse(String(init.body)) : null
      seen.push({ path, method, data })
      const meta = path.match(/^\/api\/layout-session\/project-file\/([^/]+)(.*)$/)
      if (meta) {
        const id = meta[1], suffix = meta[2], slot = slots.get(id)
        const record = (): ProjectFileView => ({
          version: 1, status: 'ok', binding: slot?.binding ?? null, intent_sequence: intents.get(id) ?? 0,
          edit: { known: true, axis: slot?.axis ?? null, autosave_revision: slot ? hex(50) : null },
        })
        if (!suffix) {
          if (editDuringRead && !slot && !s().doc.guides.length) { stop = startAutosave(); edit(44) }
          return response(record())
        }
        if (suffix === '/reserve') {
          intents.set(id, 1); installAxes.set(id, structuredClone(data.axis))
          return response({ state: 'reserved', ticket: {
          sequence: 1, binding_sequence: 0, name: data.name, file: `tavottofile/${data.name}.json`, autosave_revision: data.autosave_revision, axis: data.axis,
          } })
        }
        if (suffix === '/confirm') {
          confirmStarted!()
          if (failConfirm && ++confirmAttempts === 1) return response({ code: 'session_unavailable' }, 503)
          if (late) await confirmGate
          slot!.binding = { sequence: 1, name: data.name, file: `tavottofile/${data.name}.json`, revision: data.revision,
            dirty: JSON.stringify(slot!.axis) !== JSON.stringify(installAxes.get(id)) }
          return response({ state: 'bound', ...record() })
        }
        return response({ state: 'superseded' })
      }
      const auto = path.match(/^\/api\/autosave\/([^/]+)$/)
      if (auto) {
        const id = auto[1], slot = slots.get(id)
        if (method === 'GET') return slot ? response(slot.document, 200, hex(50)) : response({}, 404)
        if (method === 'PUT') {
          const edit = JSON.parse(url.searchParams.get('project_file_edit')!)
          slots.set(id, { document: data, axis: edit.axis, binding: slot?.binding ?? null })
          return response({ ok: true, saved_at: 1, revision: hex(50), project_file: {
            state: 'accepted', version: 1, status: 'ok', binding: slot?.binding ?? null, intent_sequence: intents.get(id) ?? 0,
            edit: { known: true, axis: edit.axis, autosave_revision: hex(50) },
          } })
        }
      }
      return response({}, 404)
    }))
    const opening = openLayoutDocument(body, { projectId: 'project-A', name: 'Chosen', file: 'tavottofile/Chosen.json', revision: hex(7), dirty: false })
    await reachedConfirm
    const installed = s().documentId
    expect(installed).not.toBe(body.project.id)
    expect(seen.find(item => item.path.endsWith('/reserve'))!.data).toMatchObject({ name: 'Chosen', autosave_revision: null, axis: null })
    expect(seen.findIndex(item => item.path.endsWith('/reserve'))).toBeLessThan(seen.findIndex(item => item.method === 'PUT'))
    expect(seen.find(item => item.path.endsWith('/confirm'))!.data).toMatchObject({ name: 'Chosen', revision: hex(7), intent_sequence: 1 })
    if (late) await s().switchDocument(emptyProject(), 'later-document')
    releaseConfirm!()
    expect(await opening).toBe(true)
    if (late) {
      expect(s().documentId).toBe('later-document')
      expect(s().projectFile).toBeNull()
    } else {
      expect(s().documentId).toBe(installed)
      if (failConfirm) {
        expect(s().projectFile).toMatchObject({ name: 'Chosen', revision: hex(7) })
        expect(s().projectFile?.sequence).toBeUndefined()
        expect(s().restoreMetaIssue).not.toBeNull()
        await runManualSave()
        expect(confirmAttempts).toBe(2)
        expect(seen.some(item => item.method === 'POST' && /\/layouts\//.test(item.path))).toBe(false)
      }
      expect(s().projectFile).toMatchObject({ name: 'Chosen', sequence: 1, revision: hex(7), dirty: editDuringRead })
    }
  })
})
