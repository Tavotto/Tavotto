import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import ts from 'typescript'
import type { PanelObject } from '@/types/document'
import { autosavePanelEvidence, type AutosaveTarget } from '../e2e/autosaveEvidence'
import { literal } from '@/i18n'
import { be, boot, resetAll, SAVED_PANEL, settle } from './store/recovery.harness'

const target: AutosaveTarget = {
  panelId: 'visible-panel', fileId: 'runtime:show_only.py#show_only',
  requiredOverrides: [{ gid: 'axes_0.title', prop: 'fontsize', value: 13 }, { prop: 'linewidth', value: 3 }],
}
const panel = (): PanelObject => ({
  ...SAVED_PANEL, id: target.panelId, fileId: target.fileId, fileKind: 'runtime', script: 'show_only.py',
  source: { script: 'show_only.py', entry: '', stem: 'show_only', captureSource: 'pyplot', fingerprint: 'run', sizeMm: [80, 60] },
  overrides: [{ gid: 'axes_0.title', prop: 'fontsize', value: 13 }, { gid: 'axes_0.lines0', prop: 'linewidth', value: 3 }],
})
const body = () => ({ canvases: [{ objects: [panel()] }] })
const response = (doc: unknown = body(), opts: { url?: string; method?: string; status?: number } = {}) => ({
  url: () => opts.url ?? 'http://127.0.0.1/api/autosave/actual-doc?pj=p_a',
  ok: () => (opts.status ?? 200) >= 200 && (opts.status ?? 200) < 300,
  request: () => ({ method: () => opts.method ?? 'PUT', postDataJSON: () => doc }),
})
const cleanups: (() => void)[] = []
beforeEach(resetAll)
afterEach(async () => { cleanups.splice(0).forEach((stop) => stop()); await settle() })

describe('E2E autosave evidence uses the current object and actual PUT route', () => {
  it.each([null, 'stale-other-doc'])('returns the real route regardless of the recovery index (%s)', (oldIndex) => {
    if (oldIndex !== null) localStorage.setItem('tavotto.currentDoc', oldIndex)
    expect(autosavePanelEvidence(response(), target)).toEqual({ docId: 'actual-doc', panel: panel() })
  })

  it.each([
    { method: 'GET' }, { method: 'POST' }, { status: 409 }, { status: 500 },
    { url: 'http://127.0.0.1/api/autosave/actual-doc/summary' },
    { url: 'http://127.0.0.1/unrelated?next=/api/autosave/actual-doc' },
    { url: 'http://127.0.0.1/api/autosave/' },
    { url: 'http://127.0.0.1/api/autosave/wrong%2Fdoc' },
  ])('rejects an unrelated or unsuccessful request: %j', (opts) => {
    expect(autosavePanelEvidence(response(body(), opts), target)).toBeNull()
  })

  it('rejects another document with the same runtime source but a different panel id', () => {
    const old = body()
    old.canvases[0].objects[0].id = 'other-doc-panel'
    expect(autosavePanelEvidence(response(old, { url: 'http://127.0.0.1/api/autosave/other-doc' }), target)).toBeNull()
  })

  it('rejects another source, an unfinished edit, stale values, and duplicate object IDs', () => {
    const otherSource = body()
    otherSource.canvases[0].objects[0].fileId = 'runtime:other.py#figure'
    expect(autosavePanelEvidence(response(otherSource), target)).toBeNull()
    const incomplete = body()
    incomplete.canvases[0].objects[0].overrides.pop()
    expect(autosavePanelEvidence(response(incomplete), target)).toBeNull()
    const stale = body()
    stale.canvases[0].objects[0].overrides[0].value = 12
    expect(autosavePanelEvidence(response(stale), target)).toBeNull()
    const duplicate = body()
    duplicate.canvases.push({ objects: [panel()] })
    expect(autosavePanelEvidence(response(duplicate), target)).toBeNull()
  })

  it.each([null, 'stale-other-doc'])('new-document save works before the recovery index is written (index=%s)', async (oldIndex) => {
    const app = await boot()
    await app.doc.restoreSession()
    if (oldIndex !== null) localStorage.setItem('tavotto.currentDoc', oldIndex)
    const docId = app.doc.useDocumentStore.getState().documentId
    expect(docId).not.toBe(oldIndex)
    cleanups.push(app.doc.startAutosave())
    app.doc.useDocumentStore.getState().commit(literal('edit first unsaved document'), (doc) => {
      doc.objects.push(panel())
    })
    // This is the real production reader: a live edited document can have no recovery index yet.
    expect(app.doc.readCurrentDocId()).toBe(oldIndex)
    expect(app.doc.useDocumentStore.getState().doc.objects[0].id).toBe(target.panelId)
    const observed: ReturnType<typeof autosavePanelEvidence>[] = []
    const actualFetch = globalThis.fetch
    globalThis.fetch = (async (...args: Parameters<typeof fetch>) => {
      const result = await actualFetch(...args)
      const evidence = autosavePanelEvidence(response(
        typeof args[1]?.body === 'string' ? JSON.parse(args[1].body) : null,
        { url: new URL(String(args[0]), 'http://127.0.0.1').href, method: args[1]?.method ?? 'GET', status: result.status },
      ), target)
      if (evidence) observed.push(evidence)
      return result
    }) as typeof fetch
    expect(await app.doc.saveNowWithResult()).toMatchObject({ wrote: true, state: 'saved' })
    expect(observed).toHaveLength(1)
    expect(observed[0]?.docId).toBe(docId)
    const written = JSON.parse(be.slots.get(observed[0]!.docId)!)
    expect(written.canvases[0].objects[0]).toEqual(observed[0]!.panel)
    expect(app.doc.readCurrentDocId()).toBe(docId)
    expect(be.slots.has('stale-other-doc')).toBe(false)
  })
})

// Inspect the actual E2E callback, not a copied predicate: the live identity must not regress
// to a recovery-index read even if the response matcher itself remains correct.
it('the real save/reopen E2E uses this matcher and never reads a storage index as live identity', () => {
  const files = import.meta.glob('/e2e/asset-library.spec.ts', { eager: true, query: '?raw', import: 'default' }) as Record<string, string>
  const source = ts.createSourceFile('asset-library.spec.ts', Object.values(files)[0], ts.ScriptTarget.Latest, true)
  let body: ts.Node | undefined
  const findTest = (node: ts.Node) => {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'test' &&
        ts.isStringLiteral(node.arguments[0]) && node.arguments[0].text.startsWith('完整链：保存')) body = node.arguments[1]
    ts.forEachChild(node, findTest)
  }
  findTest(source)
  expect(body).toBeDefined()
  let matcherCalls = 0
  const indexReads: string[] = []
  const inspect = (node: ts.Node) => {
    if (ts.isCallExpression(node)) {
      if (ts.isIdentifier(node.expression) && node.expression.text === 'autosavePanelEvidence') matcherCalls += 1
      if (ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === 'getItem') {
        indexReads.push(node.getText(source))
      }
    }
    ts.forEachChild(node, inspect)
  }
  inspect(body!)
  expect(indexReads).toEqual([])
  expect(matcherCalls).toBe(2) // Response admission and the exact document ID used for the disk read.
})
