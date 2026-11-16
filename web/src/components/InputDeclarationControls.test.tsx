import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (original) => ({
  ...(await original<typeof import('@/lib/api')>()),
  fetchPreparationInputDeclarationContext: vi.fn(),
  fetchDeclaredInputs: vi.fn(),
  fetchInputRemap: vi.fn(),
  resolveDeclaredInputProjectFile: vi.fn(),
  declareInputs: vi.fn(),
  removeDeclaredInputs: vi.fn(),
  addInputRemap: vi.fn(),
  probeScript: vi.fn(),
  actOnPreparationSession: vi.fn(),
}))
vi.mock('@/lib/desktop', async (original) => ({
  ...(await original<typeof import('@/lib/desktop')>()),
  isDesktop: vi.fn(),
  pickAnyFile: vi.fn(),
}))

import {
  ApiError, addInputRemap, declareInputs, fetchDeclaredInputs, fetchInputRemap,
  fetchPreparationInputDeclarationContext, removeDeclaredInputs, resolveDeclaredInputProjectFile,
  probeScript, actOnPreparationSession,
  type InputDeclarationScope, type InputDeclarationState, type InputRemapState,
  type PreparationInputDeclaration, type PreparationReport,
} from '@/lib/api'
import { isDesktop, pickAnyFile } from '@/lib/desktop'
import { setCurrentProjectId } from '@/lib/session'
import { i18n, t } from '@/i18n'
import { InputDeclarationControls, type InputDeclarationControlsProps, type InputDeclarationDraftCheckpoint } from './InputDeclarationControls'

const SCOPE: InputDeclarationScope = { script: 'plot.py', cwd_mode: 'project_root', run_config: null }
const REVISION = '1'.repeat(32)
const NEW_REVISION = '2'.repeat(32)
const SELECTION = 'sha256:' + 'a'.repeat(64)
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
const state = (files = ['keep.csv', 'remove.csv'], remaps = ['']): InputDeclarationState => ({
  version: 1, revision: REVISION, declarations: [{ scope: clone(SCOPE), files, remaps, selection_revision: SELECTION }],
})
const remapState = (): InputRemapState => ({ generation: 7, rules: [
  { kind: 'prefix', from: '', to: '/approved/tree', target_exists: true },
  { kind: 'file', from: 'old.csv', to: '/approved/value.csv', target_exists: true },
] })
const context = (over: Partial<PreparationInputDeclaration> = {}): PreparationInputDeclaration => ({
  version: 1, scope: clone(SCOPE), selection_revision: SELECTION,
  input_remap_generation: 7,
  unavailable_reason: null, captured_inputs: null, ...over,
})
/** The component reads only these fields of the preparation response. */
const report = (over: Partial<PreparationReport> = {}): PreparationReport => ({
  session_id: 'session-a', project_id: 'project-a', config_revision: 4,
  input_declaration: context(), ...over,
}) as PreparationReport

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

let host: HTMLDivElement
let root: Root
let props: InputDeclarationControlsProps
let saved: ReturnType<typeof vi.fn<() => Promise<void>>>

function one<T extends HTMLElement>(selector: string, within: ParentNode = host): T {
  const matches = within.querySelectorAll<T>(selector)
  expect(matches.length, selector).toBe(1)
  return matches[0]
}
function identified(attribute: string, value: string): HTMLElement {
  const found = [...host.querySelectorAll<HTMLElement>(`[${attribute}]`)].filter((node) => node.getAttribute(attribute) === value)
  expect(found).toHaveLength(1)
  return found[0]
}
const files = () => [...host.querySelectorAll('[data-input-declaration-file]')].map((node) => node.getAttribute('data-input-declaration-file'))
const button = (name: string) => one<HTMLButtonElement>(`[data-input-declaration-${name}]`)
async function show(over: Partial<InputDeclarationControlsProps> = {}) {
  props = { ...props, ...over }
  await act(async () => { root.render(<InputDeclarationControls {...props} />) })
}
async function click(name: string) {
  await act(async () => { button(name).click() })
}
async function open() {
  await act(async () => {
    const details = one<HTMLDetailsElement>('[data-input-declaration]')
    details.open = true
    details.dispatchEvent(new Event('toggle'))
  })
}
async function typePath(value: string) {
  await act(async () => {
    const input = one<HTMLInputElement>('[data-input-declaration-path]')
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function removeFile(value: string) {
  await act(async () => { one<HTMLButtonElement>('[data-input-declaration-remove-file]', identified('data-input-declaration-file', value)).click() })
}
async function toggleRemap(from: string) {
  await act(async () => { one<HTMLInputElement>('input', identified('data-input-declaration-remap', from)).click() })
}

beforeEach(async () => {
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  await i18n.changeLanguage('zh-CN')
  setCurrentProjectId('project-a')
  vi.mocked(isDesktop).mockReturnValue(false)
  vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report())
  vi.mocked(fetchDeclaredInputs).mockImplementation(async () => state())
  vi.mocked(fetchInputRemap).mockImplementation(async () => remapState())
  vi.mocked(resolveDeclaredInputProjectFile).mockImplementation(async (path) => path)
  vi.mocked(declareInputs).mockImplementation(async (body) => ({ version: 1, revision: NEW_REVISION, declarations: [
    { scope: { script: body.script, cwd_mode: body.cwd_mode, run_config: body.run_config }, files: [...body.files], remaps: [...(body.remaps ?? [])] },
  ] }))
  vi.mocked(removeDeclaredInputs).mockResolvedValue({ version: 1, revision: NEW_REVISION, declarations: [] })
  saved = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
  props = { pj: 'project-a', sessionId: 'session-a', configRevision: 4, onSaved: saved }
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => { root.unmount() })
  host.remove()
  setCurrentProjectId(null)
  vi.unstubAllGlobals()
})

describe('InputDeclarationControls', () => {
  it('is folded and reads three pinned endpoints only after opening', async () => {
    await show()
    expect(fetchDeclaredInputs).not.toHaveBeenCalled()
    await open()
    expect(fetchPreparationInputDeclarationContext).toHaveBeenCalledWith('session-a', 'project-a', expect.any(AbortSignal))
    expect(fetchDeclaredInputs).toHaveBeenCalledWith('project-a', expect.any(AbortSignal))
    expect(fetchInputRemap).toHaveBeenCalledWith('project-a', expect.any(AbortSignal))
    expect(files()).toEqual(['keep.csv', 'remove.csv'])
    expect(declareInputs).not.toHaveBeenCalled()
    expect(removeDeclaredInputs).not.toHaveBeenCalled()
  })

  it('uses exact backend scope, including rc ID and cwd, rather than the first declaration', async () => {
    const scope: InputDeclarationScope = { script: 'nested/figure.py', cwd_mode: 'project', run_config: 'rc_0123456789ab' }
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ input_declaration: context({ scope }) }))
    vi.mocked(fetchDeclaredInputs).mockResolvedValue({ ...state(), declarations: [
      ...state().declarations, { scope, files: ['correct.csv'], remaps: [] },
    ] })
    await show(); await open(); await click('save')
    expect(declareInputs).toHaveBeenCalledWith({ ...scope, files: ['correct.csv'], remaps: [], expected_revision: REVISION, expected_remap_generation: 7 }, 'project-a', expect.any(AbortSignal))
  })

  it('adds only the server normalized ID, preserves raw path tokens, and cancels without writing', async () => {
    vi.mocked(resolveDeclaredInputProjectFile).mockResolvedValue('data/中文, value.csv')
    await show(); await open()
    await typePath('  exact input, "中文".csv')
    await click('add')
    expect(resolveDeclaredInputProjectFile).toHaveBeenCalledWith('  exact input, "中文".csv', 'project-a', expect.any(AbortSignal))
    expect(files()).toEqual(['keep.csv', 'remove.csv', 'data/中文, value.csv'])
    await removeFile('remove.csv'); await toggleRemap('old.csv'); await click('cancel')
    expect(declareInputs).not.toHaveBeenCalled()
    expect(removeDeclaredInputs).not.toHaveBeenCalled()
    expect(addInputRemap).not.toHaveBeenCalled()
    await open()
    expect(files()).toEqual(['keep.csv', 'remove.csv'])
  })

  it('submits one CAS save with exact external requests, including empty prefix, and never runs', async () => {
    await show(); await open(); await toggleRemap('old.csv'); await click('save')
    expect(declareInputs).toHaveBeenCalledTimes(1)
    expect(declareInputs).toHaveBeenCalledWith({ ...SCOPE, files: ['keep.csv', 'remove.csv'], remaps: ['', 'old.csv'], expected_revision: REVISION, expected_remap_generation: 7 }, 'project-a', expect.any(AbortSignal))
    expect(saved).toHaveBeenCalledTimes(1)
    expect(probeScript).not.toHaveBeenCalled()
    expect(actOnPreparationSession).not.toHaveBeenCalled()
    expect(addInputRemap).not.toHaveBeenCalled()
    expect(host.querySelector('[data-input-declaration-captured]')).toBeNull()
  })

  it('distinguishes explicitly saving an empty declaration from removing its scope', async () => {
    await show(); await open(); await removeFile('keep.csv'); await removeFile('remove.csv'); await toggleRemap('')
    await click('save')
    expect(vi.mocked(declareInputs).mock.calls[0][0]).toEqual({ ...SCOPE, files: [], remaps: [], expected_revision: REVISION, expected_remap_generation: 7 })
    expect(removeDeclaredInputs).not.toHaveBeenCalled()
    await click('remove')
    expect(removeDeclaredInputs).toHaveBeenCalledWith({ ...SCOPE, expected_revision: NEW_REVISION }, 'project-a', expect.any(AbortSignal))
    expect(saved).toHaveBeenCalledTimes(2)
  })

  it('retains the form after 503 and does not automatically retry', async () => {
    vi.mocked(declareInputs).mockRejectedValue(new ApiError('temporarily unavailable', 503, { code: 'input_declaration_unavailable' }))
    await show(); await open(); await removeFile('remove.csv'); await click('save')
    await act(async () => {})
    expect(files()).toEqual(['keep.csv'])
    expect(button('save').disabled).toBe(false)
    expect(declareInputs).toHaveBeenCalledTimes(1)
    expect(saved).not.toHaveBeenCalled()
    expect(host.querySelector('[data-input-declaration-error]')).not.toBeNull()
  })

  it('reloads a conflict with three-way draft merge and the new remap generation', async () => {
    vi.mocked(declareInputs).mockRejectedValueOnce(new ApiError('conflict', 409, { code: 'input_declaration_conflict' }))
    await show(); await open()
    await removeFile('remove.csv'); await typePath('local.csv'); await click('add'); await toggleRemap('old.csv')
    await click('save')
    expect(button('save').disabled).toBe(true)
    vi.mocked(fetchDeclaredInputs).mockResolvedValue({ ...state(['keep.csv', 'remove.csv', 'remote.csv']), revision: NEW_REVISION })
    vi.mocked(fetchInputRemap).mockResolvedValue({ ...remapState(), generation: 8 })
    await click('reload')
    expect(files()).toEqual(['keep.csv', 'remote.csv', 'local.csv'])
    await click('save')
    expect(vi.mocked(declareInputs).mock.calls[1][0]).toEqual({ ...SCOPE, files: ['keep.csv', 'remote.csv', 'local.csv'], remaps: ['', 'old.csv'], expected_revision: NEW_REVISION, expected_remap_generation: 8 })
    expect(saved).toHaveBeenCalledTimes(1)
  })

  it('does not treat external-target drift as a successful save', async () => {
    vi.mocked(declareInputs).mockRejectedValue(new ApiError('remap changed', 409, { code: 'input_declaration_conflict' }))
    await show(); await open(); await toggleRemap('old.csv'); await click('save')
    expect(vi.mocked(declareInputs).mock.calls[0][0].expected_remap_generation).toBe(7)
    expect(one<HTMLInputElement>('input', identified('data-input-declaration-remap', 'old.csv')).checked).toBe(true)
    expect(saved).not.toHaveBeenCalled()
    expect(button('save').disabled).toBe(true)
  })

  it.each(['save', 'remove'] as const)('reports %s committed separately from recheck failure', async (action) => {
    saved.mockRejectedValue(new Error('recheck disconnected'))
    await show(); await open(); await click(action)
    expect(host.querySelector('[data-input-declaration-error]')).toBeNull()
    expect(one('[data-input-declaration-status]').textContent).toContain(t(action === 'save' ? 'prep.inputs.savedRecheckFailed' : 'prep.inputs.removedRecheckFailed', { ns: 'workspace' }))
    expect(declareInputs).toHaveBeenCalledTimes(action === 'save' ? 1 : 0)
    expect(removeDeclaredInputs).toHaveBeenCalledTimes(action === 'remove' ? 1 : 0)
  })

  it.each(['sensitive_run', 'scope_unknown', 'not_script'] as const)('shows %s without guessing a scope or permitting writes', async (reason) => {
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ input_declaration: context({ scope: null, unavailable_reason: reason }) }))
    await show(); await open(); await click('save')
    expect(host.querySelector('[data-input-declaration-unavailable]')).not.toBeNull()
    expect(button('save').disabled).toBe(true)
    expect(declareInputs).not.toHaveBeenCalled()
  })

  it('treats missing opt-in projection as unknown, not an empty parameter-free scope', async () => {
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ input_declaration: undefined }))
    await show(); await open()
    expect(button('save').disabled).toBe(true)
    expect(host.querySelector('[data-input-declaration-unavailable]')).not.toBeNull()
  })

  it.each([{ project_id: 'project-b' }, { session_id: 'session-b' }, { config_revision: 5 }])('rejects a context response from a different owner: %j', async (over) => {
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report(over))
    await show(); await open()
    expect(button('save').disabled).toBe(true)
    expect(host.querySelector('[data-input-declaration-changed]')).not.toBeNull()
    expect(files()).toEqual([])
  })

  it('accepts a pinned default-project response while the app has no explicit current ID', async () => {
    setCurrentProjectId(null)
    await show(); await open(); await click('save')
    expect(saved).toHaveBeenCalledTimes(1)
  })

  it('shows capture evidence only from the server and hides it for a modified selection', async () => {
    const revision = 'sha256:' + 'c'.repeat(64)
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ input_declaration: context({ captured_inputs: { version: 1, revision } }) }))
    await show(); await open()
    expect(one('[data-input-declaration-captured]').textContent).toContain(revision)
    await removeFile('remove.csv')
    expect(host.querySelector('[data-input-declaration-captured]')).toBeNull()
    await click('save')
    expect(host.querySelector('[data-input-declaration-captured]')).toBeNull()
  })

  it.each(['different', 'missing'] as const)('does not pair old captured evidence with a %s current row token', async (kind) => {
    const current = state()
    if (kind === 'different') current.declarations[0].selection_revision = 'sha256:' + 'b'.repeat(64)
    else delete current.declarations[0].selection_revision
    vi.mocked(fetchDeclaredInputs).mockResolvedValue(current)
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ input_declaration: context({ captured_inputs: { version: 1, revision: 'sha256:' + 'c'.repeat(64) } }) }))
    await show(); await open()
    expect(files()).toEqual(['keep.csv', 'remove.csv'])
    expect(host.querySelector('[data-input-declaration-captured]')).toBeNull()
  })

  it('does not display old capture beside a changed remap target with the same declaration row', async () => {
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ input_declaration: context({ captured_inputs: { version: 1, revision: 'sha256:' + 'c'.repeat(64) } }) }))
    vi.mocked(fetchInputRemap).mockResolvedValue({ generation: 8, rules: [
      { kind: 'prefix', from: '', to: '/approved/new-target', target_exists: true },
    ] })
    await show(); await open()
    expect(files()).toEqual(['keep.csv', 'remove.csv'])
    expect(identified('data-input-declaration-remap', '').textContent).toContain('/approved/new-target')
    expect(host.querySelector('[data-input-declaration-captured]')).toBeNull()
  })

  it.each(['null', 'missing'] as const)('does not grant capture evidence with %s remap generation even without selected remaps', async (kind) => {
    const projection = context({ input_remap_generation: null, captured_inputs: { version: 1, revision: 'sha256:' + 'c'.repeat(64) } })
    if (kind === 'missing') Reflect.deleteProperty(projection, 'input_remap_generation')
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ input_declaration: projection }))
    vi.mocked(fetchDeclaredInputs).mockResolvedValue(state(['keep.csv'], []))
    vi.mocked(fetchInputRemap).mockResolvedValue({ generation: 7, rules: [] })
    await show(); await open()
    expect(files()).toEqual(['keep.csv'])
    expect(host.querySelector('[data-input-declaration-captured]')).toBeNull()
  })

  it('accepts matching known remap generation when the selection contains no remaps', async () => {
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ input_declaration: context({ captured_inputs: { version: 1, revision: 'sha256:' + 'c'.repeat(64) } }) }))
    vi.mocked(fetchDeclaredInputs).mockResolvedValue(state(['keep.csv'], []))
    vi.mocked(fetchInputRemap).mockResolvedValue({ generation: 7, rules: [] })
    await show(); await open()
    expect(host.querySelector('[data-input-declaration-captured]')).not.toBeNull()
  })

  it('retains the actual draft across parent report-null unmount and blocks it until explicit reload', async () => {
    const draftRef: { current: InputDeclarationDraftCheckpoint | null } = { current: null }
    await show({ draftRef }); await open(); await removeFile('remove.csv'); await toggleRemap('old.csv'); await typePath('not-yet-added.csv')
    await act(async () => root.render(null))
    const scope = { ...SCOPE, run_config: 'rc_0123456789ab' }
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ config_revision: 5, input_declaration: context({ scope }) }))
    vi.mocked(fetchDeclaredInputs).mockResolvedValue({ ...state(), declarations: [{ scope, files: ['new-scope.csv'], remaps: [] }] })
    await show({ configRevision: 5 })
    expect(one<HTMLDetailsElement>('[data-input-declaration]').open).toBe(true)
    expect(files()).toEqual(['keep.csv'])
    expect(one<HTMLInputElement>('[data-input-declaration-path]').value).toBe('not-yet-added.csv')
    expect(one<HTMLInputElement>('input', identified('data-input-declaration-remap', 'old.csv')).checked).toBe(true)
    expect(button('save').disabled).toBe(true)
    expect(draftRef.current?.configRevision).toBe(4)
    await act(async () => root.render(null))
    await show()
    expect(files()).toEqual(['keep.csv'])
    expect(button('save').disabled).toBe(true)
    await click('reload')
    expect(files()).toEqual(['new-scope.csv'])
    await click('save')
    expect(vi.mocked(declareInputs).mock.calls[0][0].run_config).toBe(scope.run_config)
  })

  it('automatically refreshes a retained same-owner draft and merges local changes without a changed warning', async () => {
    const draftRef: { current: InputDeclarationDraftCheckpoint | null } = { current: null }
    await show({ draftRef }); await open(); await removeFile('remove.csv')
    await act(async () => root.render(null))
    vi.mocked(fetchDeclaredInputs).mockResolvedValue(state(['keep.csv', 'remove.csv', 'remote.csv']))
    const reads = vi.mocked(fetchDeclaredInputs).mock.calls.length
    await show()
    expect(fetchDeclaredInputs).toHaveBeenCalledTimes(reads + 1)
    expect(files()).toEqual(['keep.csv', 'remote.csv'])
    expect(host.querySelector('[data-input-declaration-changed]')).toBeNull()
    expect(button('save').disabled).toBe(false)
  })

  it.each(['cancel', 'fold'] as const)('does not resurrect a checkpoint after explicit %s', async (action) => {
    const draftRef: { current: InputDeclarationDraftCheckpoint | null } = { current: null }
    await show({ draftRef }); await open(); await removeFile('remove.csv'); await typePath('cancelled.csv')
    expect(draftRef.current?.files).toEqual(['keep.csv'])
    if (action === 'cancel') await click('cancel')
    else await act(async () => {
      const details = one<HTMLDetailsElement>('[data-input-declaration]')
      details.open = false
      details.dispatchEvent(new Event('toggle'))
    })
    expect(draftRef.current).toBeNull()
    await act(async () => root.render(null))
    await show()
    expect(one<HTMLDetailsElement>('[data-input-declaration]').open).toBe(false)
    await open()
    expect(files()).toEqual(['keep.csv', 'remove.csv'])
    expect(one<HTMLInputElement>('[data-input-declaration-path]').value).toBe('')
    expect(declareInputs).not.toHaveBeenCalled()
    expect(removeDeclaredInputs).not.toHaveBeenCalled()
  })

  it('rejects a retained draft after the app project epoch changes A to B to A', async () => {
    const draftRef: { current: InputDeclarationDraftCheckpoint | null } = { current: null }
    await show({ draftRef }); await open(); await removeFile('remove.csv')
    await act(async () => root.render(null))
    setCurrentProjectId('project-b'); setCurrentProjectId('project-a')
    await show()
    expect(one<HTMLDetailsElement>('[data-input-declaration]').open).toBe(false)
    await open()
    expect(files()).toEqual(['keep.csv', 'remove.csv'])
    await removeFile('remove.csv')
    await act(async () => root.render(null))
    await show()
    expect(files()).toEqual(['keep.csv'])
    expect(button('save').disabled).toBe(false)
  })

  it('keeps a failed save draft after unmount without adopting a late success', async () => {
    const draftRef: { current: InputDeclarationDraftCheckpoint | null } = { current: null }
    vi.mocked(declareInputs).mockRejectedValueOnce(new ApiError('unavailable', 503, {}))
    await show({ draftRef }); await open(); await removeFile('remove.csv'); await typePath('still-pending.csv'); await click('save')
    expect(host.querySelector('[data-input-declaration-error]')).not.toBeNull()
    const late = deferred<InputDeclarationState>()
    vi.mocked(declareInputs).mockReturnValueOnce(late.promise)
    await click('save')
    await act(async () => root.render(null))
    await show()
    await act(async () => late.resolve(state(['late-success.csv'])))
    expect(files()).toEqual(['keep.csv'])
    expect(one<HTMLInputElement>('[data-input-declaration-path]').value).toBe('still-pending.csv')
    expect(button('save').disabled).toBe(false)
    expect(saved).not.toHaveBeenCalled()
    expect(one('[data-input-declaration-status]').textContent).toBe(t('prep.inputs.reloaded', { ns: 'workspace' }))
  })

  it('does not turn a cancelled native picker into a request or error', async () => {
    vi.mocked(isDesktop).mockReturnValue(true)
    vi.mocked(pickAnyFile).mockResolvedValue(null)
    await show(); await open(); await click('pick')
    expect(resolveDeclaredInputProjectFile).not.toHaveBeenCalled()
    expect(declareInputs).not.toHaveBeenCalled()
    expect(host.querySelector('[data-input-declaration-error]')).toBeNull()
  })

  it('normalizes a native-picked file on the server and keeps picker failures local', async () => {
    vi.mocked(isDesktop).mockReturnValue(true)
    vi.mocked(pickAnyFile).mockResolvedValue('E:\\project\\中文.csv')
    vi.mocked(resolveDeclaredInputProjectFile).mockResolvedValue('中文.csv')
    await show(); await open(); await click('pick')
    expect(files()).toContain('中文.csv')
    expect(resolveDeclaredInputProjectFile).toHaveBeenCalledWith('E:\\project\\中文.csv', 'project-a', expect.any(AbortSignal))
    vi.mocked(resolveDeclaredInputProjectFile).mockRejectedValue(new ApiError('outside project', 400, { code: 'input_declaration_invalid' }))
    await click('pick')
    expect(files()).toEqual(['keep.csv', 'remove.csv', '中文.csv'])
    expect(host.querySelector('[data-input-declaration-error]')).not.toBeNull()
  })

  it('discards an old load after cancel/reopen instead of overriding the new form', async () => {
    const first = deferred<PreparationReport>()
    vi.mocked(fetchPreparationInputDeclarationContext).mockReturnValueOnce(first.promise)
    await show(); await open(); await click('cancel')
    vi.mocked(fetchDeclaredInputs).mockResolvedValue(state(['new.csv'], []))
    await open()
    await act(async () => first.resolve(report()))
    expect(files()).toEqual(['new.csv'])
    expect(saved).not.toHaveBeenCalled()
  })

  it('drops a pending file resolver on same-session config changes and preserves local draft', async () => {
    const pending = deferred<string>()
    vi.mocked(resolveDeclaredInputProjectFile).mockReturnValueOnce(pending.promise)
    await show(); await open(); await removeFile('remove.csv'); await typePath('late.csv'); await click('add')
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ config_revision: 5 }))
    await show({ configRevision: 5 })
    await act(async () => pending.resolve('late.csv'))
    expect(files()).toEqual(['keep.csv'])
    await click('save')
    expect(vi.mocked(declareInputs).mock.calls[0][0].files).toEqual(['keep.csv'])
  })

  it('holds the old draft without save authority when the backend scope changes until explicit reload', async () => {
    await show(); await open(); await removeFile('remove.csv')
    const scope = { ...SCOPE, run_config: 'rc_0123456789ab' }
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ config_revision: 5, input_declaration: context({ scope }) }))
    vi.mocked(fetchDeclaredInputs).mockResolvedValue({ ...state(), declarations: [{ scope, files: ['new-scope.csv'], remaps: [] }] })
    await show({ configRevision: 5 })
    expect(files()).toEqual(['keep.csv'])
    expect(button('save').disabled).toBe(true)
    await click('reload')
    expect(files()).toEqual(['new-scope.csv'])
    await click('save')
    expect(vi.mocked(declareInputs).mock.calls[0][0].run_config).toBe(scope.run_config)
  })

  it.each(['disabled', 'draftChanged'] as const)('rejects a save response after %s invalidates the request', async (flag) => {
    const pending = deferred<InputDeclarationState>()
    vi.mocked(declareInputs).mockReturnValue(pending.promise)
    await show(); await open(); await click('save'); await show({ [flag]: true })
    await act(async () => pending.resolve({ ...state(), revision: NEW_REVISION }))
    expect(saved).not.toHaveBeenCalled()
    expect(host.querySelector('[data-input-declaration-status]')).toBeNull()
    expect(button('save').disabled).toBe(true)
  })

  it('does not resolve a pending picker after session replacement', async () => {
    const pending = deferred<string | null>()
    vi.mocked(isDesktop).mockReturnValue(true)
    vi.mocked(pickAnyFile).mockReturnValue(pending.promise)
    await show(); await open(); await click('pick')
    vi.mocked(fetchPreparationInputDeclarationContext).mockResolvedValue(report({ session_id: 'session-b' }))
    await show({ sessionId: 'session-b' })
    await act(async () => pending.resolve('/old-project/file.csv'))
    expect(resolveDeclaredInputProjectFile).not.toHaveBeenCalled()
  })

  it('rejects a late save after an app project A to B to A round trip', async () => {
    const pending = deferred<InputDeclarationState>()
    vi.mocked(declareInputs).mockReturnValue(pending.promise)
    await show(); await open(); await click('save')
    setCurrentProjectId('project-b'); setCurrentProjectId('project-a')
    await act(async () => pending.resolve({ ...state(), revision: NEW_REVISION }))
    expect(saved).not.toHaveBeenCalled()
    expect(host.querySelector('[data-input-declaration-status]')).toBeNull()
  })

  it('keeps a selected vanished remap removable without silently dropping it', async () => {
    vi.mocked(fetchDeclaredInputs).mockResolvedValue(state([], ['gone.csv']))
    vi.mocked(fetchInputRemap).mockResolvedValue({ generation: 8, rules: [] })
    await show(); await open()
    expect(button('save').disabled).toBe(true)
    const row = identified('data-input-declaration-missing-remap', 'gone.csv')
    await act(async () => one<HTMLButtonElement>('button', row).click())
    await click('save')
    expect(vi.mocked(declareInputs).mock.calls[0][0].remaps).toEqual([])
  })
})
