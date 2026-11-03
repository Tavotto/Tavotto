import { afterEach, beforeEach, expect, it } from 'vitest'
import { literal } from '@/i18n'
import type { PanelObject, ProjectDocument } from '@/types/document'
import { be, boot, newOrigin, resetAll, seedSaved, settle, shapeOf, type Req } from './recovery.harness'

const cleanups: (() => void)[] = []
const put = (r: Req) => r.method === 'PUT' && r.path === '/api/autosave/d_saved'
const panelOf = (pd: ProjectDocument) => pd.canvases[0].objects[0] as PanelObject
const slot = () => JSON.parse(be.slots.get('d_saved')!) as ProjectDocument
const localCopy = () => {
  const raw = localStorage.getItem('tavotto.autosave.d_saved')
  expect(raw, 'latest edits must survive before asynchronous snapshot freeze finishes').not.toBeNull()
  return shapeOf(JSON.parse(raw!) as ProjectDocument)
}
beforeEach(resetAll)
afterEach(async () => { cleanups.splice(0).forEach((f) => f()); be.gates.forEach((g) => g.release()); await settle() })

it('unload during slow failing snapshot save retains latest edits as explicit cold recovery, then saves them', async () => {
  seedSaved()
  const first = await boot()
  expect(await first.doc.restoreSession()).toBe(true)
  cleanups.push(first.doc.startAutosave())
  const old = shapeOf(slot())
  first.doc.useDocumentStore.getState().commit(literal('latest edit'), (d) => {
    const p = d.objects[0] as PanelObject
    p.overrides = [{ gid: 'axes_0.title', prop: 'fontsize', value: 13 }, { gid: 'axes_0.line0', prop: 'linewidth', value: 3 }]
    p.x = 20; p.y = 15; p.w = 80; p.h = 57.6
  })
  const expected = shapeOf(first.doc.useDocumentStore.getState().buildProject())
  be.fail(put, 500)
  const gate = be.hold(put)
  const leave = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(leave)
  await gate.reached
  expect(leave.defaultPrevented).toBe(true)
  expect(first.doc.useDocumentStore.getState().saveState).toBe('saving')
  expect(shapeOf(slot())).toEqual(old)
  expect(localCopy()).toEqual(expected)
  cleanups.splice(0).forEach((f) => f())
  gate.release()
  await settle()
  expect(first.doc.useDocumentStore.getState().saveState).toBe('save_error')
  const second = await boot()
  expect(await second.doc.restoreSession()).toBe(true)
  expect(shapeOf(second.doc.useDocumentStore.getState().buildProject())).toEqual(old)
  expect(second.doc.useDocumentStore.getState().docNotice).toMatchObject({ kind: 'recovery', docId: 'd_saved' })
  expect(shapeOf(JSON.parse(localStorage.getItem('tavotto.recovery.d_saved')!))).toEqual(expected)
  expect(second.doc.recoverLocalCopy()).toBe(true)
  expect(shapeOf(second.doc.useDocumentStore.getState().buildProject())).toEqual(expected)
  await second.doc.saveNow()
  expect(shapeOf(slot())).toEqual(expected)
  newOrigin()
  const third = await boot()
  expect(await third.doc.restoreSession()).toBe(true)
  expect(shapeOf(third.doc.useDocumentStore.getState().buildProject())).toEqual(expected)
})

it('explicit save does not finish before slow snapshot PUT and cold read contains exact latest overrides and geometry', async () => {
  seedSaved()
  const app = await boot()
  expect(await app.doc.restoreSession()).toBe(true)
  cleanups.push(app.doc.startAutosave())
  const gate = be.hold(put)
  app.doc.useDocumentStore.getState().commit(literal('latest geometry'), (d) => {
    const p = d.objects[0] as PanelObject
    p.x = 20; p.y = 15; p.w = 80; p.h = 57.6
    p.overrides = [{ gid: 'figure', prop: 'frame', value: 'figsize' }]
  })
  const expected = shapeOf(app.doc.useDocumentStore.getState().buildProject())
  let done = false
  const saved = app.doc.saveNowWithResult().then((v) => { done = true; return v })
  await gate.reached
  expect(done).toBe(false)
  expect(app.doc.useDocumentStore.getState().saveState).toBe('saving')
  expect(panelOf(slot()).overrides).not.toEqual([{ gid: 'figure', prop: 'frame', value: 'figsize' }])
  gate.release()
  expect(await saved).toMatchObject({ wrote: true, state: 'saved' })
  expect(shapeOf(slot())).toEqual(expected)
  cleanups.splice(0).forEach((f) => f())
  newOrigin()
  const reopened = await boot()
  expect(await reopened.doc.restoreSession()).toBe(true)
  expect(shapeOf(reopened.doc.useDocumentStore.getState().buildProject())).toEqual(expected)
})

it('a delayed stale PUT cannot report the queued latest edit saved; 409 keeps it recoverable across restart', async () => {
  seedSaved()
  const app = await boot()
  expect(await app.doc.restoreSession()).toBe(true)
  cleanups.push(app.doc.startAutosave())
  const old = shapeOf(slot())
  const actualFetch = globalThis.fetch
  // The existing backend harness controls I/O timing; supply the real conflict wire code here.
  globalThis.fetch = (async (...args: Parameters<typeof fetch>) => {
    const response = await actualFetch(...args)
    return response.status === 409
      ? new Response(JSON.stringify({ code: 'stale_write', theirs: 999 }), { status: 409 })
      : response
  }) as typeof fetch
  be.fail(put, 409)
  const gate = be.hold(put)
  app.doc.useDocumentStore.getState().commit(literal('first edit'), (d) => {
    const p = d.objects[0] as PanelObject
    p.x = 21; p.y = 17; p.w = 73; p.h = 58
  })
  app.doc.flushAutosave()
  await gate.reached
  app.doc.useDocumentStore.getState().commit(literal('undo geometry and edit title'), (d) => {
    const p = d.objects[0] as PanelObject
    p.x = 20; p.y = 15; p.w = 80; p.h = 57.6
    p.overrides = [{ gid: 'axes_0.title', prop: 'fontsize', value: 13 }]
  })
  const expected = shapeOf(app.doc.useDocumentStore.getState().buildProject())
  let done = false
  const saved = app.doc.saveNowWithResult().then((v) => { done = true; return v })
  expect(done).toBe(false)
  expect(localCopy()).toEqual(expected)
  gate.release()
  expect(await saved).toMatchObject({ wrote: false, state: 'conflict' })
  expect(be.count(put)).toBe(1)
  expect(shapeOf(slot())).toEqual(old)
  expect(localCopy()).toEqual(expected)
  cleanups.splice(0).forEach((f) => f())
  const reopened = await boot()
  expect(await reopened.doc.restoreSession()).toBe(true)
  expect(reopened.doc.useDocumentStore.getState().docNotice).toMatchObject({ kind: 'recovery', docId: 'd_saved' })
  expect(reopened.doc.recoverLocalCopy()).toBe(true)
  expect(shapeOf(reopened.doc.useDocumentStore.getState().buildProject())).toEqual(expected)
  expect(await reopened.doc.saveNowWithResult()).toMatchObject({ wrote: true, state: 'saved' })
  expect(shapeOf(slot())).toEqual(expected)
  newOrigin()
  const cold = await boot()
  expect(await cold.doc.restoreSession()).toBe(true)
  expect(shapeOf(cold.doc.useDocumentStore.getState().buildProject())).toEqual(expected)
})

it('an old same-content 200 is not completion while an intermediate PUT and the final undo save remain queued', async () => {
  seedSaved()
  const app = await boot()
  expect(await app.doc.restoreSession()).toBe(true)
  cleanups.push(app.doc.startAutosave())
  const expected = shapeOf(app.doc.useDocumentStore.getState().buildProject())
  const first = be.hold(put)
  app.doc.flushAutosave()
  await first.reached
  const firstSeq = be.requests.findLast(put)!.seq

  app.doc.useDocumentStore.getState().commit(literal('adopt another frame'), (d) => {
    const p = d.objects[0] as PanelObject
    p.x = 21; p.y = 17; p.w = 73; p.h = 58
  })
  app.doc.flushAutosave() // Intermediate geometry is queued behind the old, identical-to-final snapshot.
  app.doc.useDocumentStore.getState().undo()
  expect(shapeOf(app.doc.useDocumentStore.getState().buildProject())).toEqual(expected)
  const intermediate = be.hold((r) => put(r) && r.seq > firstSeq)
  first.release()
  await intermediate.reached
  // A response/body-only waiter could pass here even though another version is already being written.
  expect(shapeOf(slot())).toEqual(expected)
  expect(app.doc.useDocumentStore.getState().saveState).toBe('saving')
  const intermediateSeq = be.requests.findLast(put)!.seq
  const final = be.hold((r) => put(r) && r.seq > intermediateSeq)
  let done = false
  const saved = app.doc.saveNowWithResult().then((v) => { done = true; return v })
  intermediate.release()
  await final.reached
  expect(shapeOf(slot())).not.toEqual(expected)
  expect(done).toBe(false)
  expect(app.doc.useDocumentStore.getState().saveState).toBe('saving')
  final.release()
  expect(await saved).toMatchObject({ wrote: true, state: 'saved' })
  expect(shapeOf(slot())).toEqual(expected)
  cleanups.splice(0).forEach((f) => f())
  newOrigin()
  const cold = await boot()
  expect(await cold.doc.restoreSession()).toBe(true)
  expect(shapeOf(cold.doc.useDocumentStore.getState().buildProject())).toEqual(expected)
})
