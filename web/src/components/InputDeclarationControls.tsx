import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ApiError,
  backendErrorText,
  declareInputs,
  fetchDeclaredInputs,
  fetchInputRemap,
  fetchPreparationInputDeclarationContext,
  removeDeclaredInputs,
  resolveDeclaredInputProjectFile,
  type InputDeclarationScope,
  type InputDeclarationState,
  type InputRemapState,
  type PreparationInputDeclaration,
} from '@/lib/api'
import { isDesktop, pickAnyFile } from '@/lib/desktop'
import { captureProjectEpoch, type ProjectEpochGuard } from '@/lib/projectEpoch'
import { Button } from './ui/Button'
import { Checkbox } from './ui/Checkbox'
import { Details, Summary } from './ui/Details'
import { TextInput } from './ui/Input'
import { Notice } from './ui/Notice'

export interface InputDeclarationControlsProps {
  pj: string
  sessionId: string
  configRevision: number
  disabled?: boolean
  draftChanged?: boolean
  draftRef?: { current: InputDeclarationDraftCheckpoint | null }
  onSaved: () => Promise<void>
}

interface Loaded {
  pj: string
  sessionId: string
  configRevision: number
  context: PreparationInputDeclaration | undefined
  state: InputDeclarationState
  remap: InputRemapState
}

/** Card-local checkpoint only; restoring a draft does not restore write authority. */
export interface InputDeclarationDraftCheckpoint {
  pj: string
  sessionId: string
  configRevision: number
  loaded: Loaded
  files: string[]
  remaps: string[]
  path: string
  epoch: ProjectEpochGuard
}

const sameScope = (a: InputDeclarationScope, b: InputDeclarationScope) =>
  a.script === b.script && a.cwd_mode === b.cwd_mode && a.run_config === b.run_config

const rowOf = (loaded: Loaded) => {
  const scope = loaded.context?.scope
  return scope ? loaded.state.declarations.find((row) => sameScope(row.scope, scope)) : undefined
}

/** Keep local additions/removals while adopting unrelated remote edits and order. */
function mergeDraft(remote: string[], before: string[], draft: string[]): string[] {
  const removed = new Set(before.filter((item) => !draft.includes(item)))
  const merged = remote.filter((item) => !removed.has(item))
  for (const item of draft) {
    if (!before.includes(item) && !merged.includes(item)) merged.push(item)
  }
  return merged
}

const STATUS_KEYS = {
  saved: 'prep.inputs.saved',
  removed: 'prep.inputs.removed',
  reloaded: 'prep.inputs.reloaded',
  savedRecheckFailed: 'prep.inputs.savedRecheckFailed',
  removedRecheckFailed: 'prep.inputs.removedRecheckFailed',
} as const

const UNAVAILABLE_KEYS = {
  sensitive_run: 'prep.inputs.sensitiveRun',
  scope_unknown: 'prep.inputs.scopeUnknown',
  not_script: 'prep.inputs.notScript',
} as const

/** Selection is a local draft. Only an explicit save/remove mutates settings. */
export function InputDeclarationControls({
  pj, sessionId, configRevision, disabled = false, draftChanged = false, draftRef, onSaved,
}: InputDeclarationControlsProps) {
  const { t } = useTranslation('workspace')
  const [restored] = useState(() => {
    const saved = draftRef?.current
    return saved?.pj === pj && saved.epoch.still() ? saved : null
  })
  const restoredOwnerChanged = !!restored && (restored.sessionId !== sessionId || restored.configRevision !== configRevision)
  const checkpointEpoch = useRef(restored?.epoch ?? captureProjectEpoch())
  const [open, setOpen] = useState(!!restored)
  const [loaded, setLoaded] = useState<Loaded | null>(restored?.loaded ?? null)
  const [files, setFiles] = useState<string[]>(restored?.files ?? [])
  const [remaps, setRemaps] = useState<string[]>(restored?.remaps ?? [])
  const [path, setPath] = useState(restored?.path ?? '')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [changed, setChanged] = useState(restoredOwnerChanged)
  const [stale, setStale] = useState(!!restored)
  const [status, setStatus] = useState<keyof typeof STATUS_KEYS | null>(null)
  const [heldUnavailable, setHeldUnavailable] = useState<keyof typeof UNAVAILABLE_KEYS | null>(null)
  const [reload, setReload] = useState(0)
  const mounted = useRef(true)
  const opened = useRef(open)
  opened.current = open
  const owner = useRef({ pj, sessionId, configRevision, disabled, draftChanged, generation: 0 })
  const previous = owner.current
  if (previous.pj !== pj || previous.sessionId !== sessionId || previous.configRevision !== configRevision ||
    previous.disabled !== disabled || previous.draftChanged !== draftChanged) {
    owner.current = { pj, sessionId, configRevision, disabled, draftChanged, generation: previous.generation + 1 }
  }
  const sequence = useRef(0)
  const active = useRef<{ controller: AbortController; timer?: number } | null>(null)
  const preserveOnReload = useRef(false)
  const freshOpening = useRef(!restored)
  const requireExplicitReload = useRef(restoredOwnerChanged)
  const draft = useRef({ loaded, files, remaps })
  draft.current = { loaded, files, remaps }

  const invalidate = useCallback(() => {
    sequence.current += 1
    active.current?.controller.abort()
    window.clearTimeout(active.current?.timer)
    active.current = null
  }, [])

  const begin = useCallback(() => {
    if (active.current || owner.current.disabled || owner.current.draftChanged || !opened.current) return null
    const guard = captureProjectEpoch(() => owner.current.generation)
    const seq = ++sequence.current
    const request = { controller: new AbortController(), timer: undefined as number | undefined }
    active.current = request
    const live = () => mounted.current && opened.current && sequence.current === seq && guard.still() &&
      !owner.current.disabled && !owner.current.draftChanged
    return {
      signal: request.controller.signal,
      live,
      network: () => { request.timer = window.setTimeout(() => request.controller.abort(), 15_000) },
      finish: () => {
        window.clearTimeout(request.timer)
        if (active.current === request) active.current = null
        if (live()) setBusy(null)
      },
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; invalidate() }
  }, [invalidate])

  useEffect(() => {
    if (!draftRef) return
    draftRef.current = open && loaded && checkpointEpoch.current.still()
      ? { pj: loaded.pj, sessionId: loaded.sessionId, configRevision: loaded.configRevision, loaded, files: [...files], remaps: [...remaps], path, epoch: checkpointEpoch.current }
      : null
  }, [draftRef, open, loaded, files, remaps, path])

  useEffect(() => {
    invalidate()
    setBusy(null)
    if (!open || disabled || draftChanged) return
    if (requireExplicitReload.current && !preserveOnReload.current) {
      setChanged(true)
      setStale(true)
      return
    }
    requireExplicitReload.current = false
    const old = freshOpening.current ? null : draft.current
    const adoptScope = preserveOnReload.current || freshOpening.current
    preserveOnReload.current = false
    freshOpening.current = false
    const operation = begin()
    if (!operation) return
    setBusy('load')
    setError(null)
    setChanged(false)
    setHeldUnavailable(null)
    setStatus(null)
    setStale(true)
    operation.network()
    void Promise.all([
      fetchPreparationInputDeclarationContext(sessionId, pj, operation.signal),
      fetchDeclaredInputs(pj, operation.signal),
      fetchInputRemap(pj, operation.signal),
    ]).then(([report, state, remap]) => {
      if (!operation.live()) return
      if (report.project_id !== pj || report.session_id !== sessionId || report.config_revision !== configRevision || state.version !== 1) {
        setChanged(true)
        return
      }
      const next: Loaded = { pj, sessionId, configRevision, context: report.input_declaration, state, remap }
      const row = rowOf(next)
      const baseline = old?.loaded ? rowOf(old.loaded) : undefined
      const merge = old?.loaded?.context?.scope && next.context?.scope &&
        old.loaded.pj === pj && sameScope(old.loaded.context.scope, next.context.scope)
      if (old?.loaded && !merge && !adoptScope) {
        setChanged(true)
        if (!next.context?.scope || next.context.unavailable_reason) {
          setHeldUnavailable(next.context?.unavailable_reason ?? 'scope_unknown')
        }
        return
      }
      setFiles(merge && old ? mergeDraft(row?.files ?? [], baseline?.files ?? [], old.files) : [...(row?.files ?? [])])
      setRemaps(merge && old ? mergeDraft(row?.remaps ?? [], baseline?.remaps ?? [], old.remaps) : [...(row?.remaps ?? [])])
      checkpointEpoch.current = captureProjectEpoch()
      setLoaded(next)
      setStale(false)
      if (merge) setStatus('reloaded')
    }).catch((err: unknown) => {
      if (operation.live()) setError(err)
    }).finally(operation.finish)
    return invalidate
  // Read only on opening or an explicit owner/reload change, not on draft edits.
  }, [open, pj, sessionId, configRevision, disabled, draftChanged, reload, begin, invalidate])

  const scope = loaded?.context?.scope
  const blocked = disabled || draftChanged || !!busy
  const editable = !!scope && !loaded?.context?.unavailable_reason && !stale && !changed && !blocked

  const cancel = () => {
    invalidate()
    if (draftRef) draftRef.current = null
    opened.current = false
    setOpen(false)
    setBusy(null)
    setLoaded(null)
    setFiles([])
    setRemaps([])
    setPath('')
    freshOpening.current = true
    requireExplicitReload.current = false
  }

  const addFile = async (native: boolean) => {
    if (!editable) return
    const operation = begin()
    if (!operation) return
    setBusy(native ? 'pick' : 'resolve')
    setError(null)
    setStatus(null)
    try {
      const selected = native ? await pickAnyFile(t('prep.inputs.chooseFile')) : path
      if (!operation.live() || !selected) return
      operation.network()
      const id = await resolveDeclaredInputProjectFile(selected, pj, operation.signal)
      if (!operation.live()) return
      setFiles((current) => current.includes(id) ? current : [...current, id])
      setPath('')
    } catch (err) {
      if (operation.live()) setError(err)
    } finally {
      operation.finish()
    }
  }

  const submit = async (remove: boolean) => {
    if (!editable || !loaded || !scope) return
    const operation = begin()
    if (!operation) return
    setBusy(remove ? 'remove' : 'save')
    setError(null)
    setStatus(null)
    operation.network()
    try {
      const common = { ...scope, expected_revision: loaded.state.revision }
      const state = remove
        ? await removeDeclaredInputs(common, pj, operation.signal)
        : await declareInputs({ ...common, files: [...files], remaps: [...remaps], expected_remap_generation: loaded.remap.generation }, pj, operation.signal)
      if (!operation.live()) return
      const next = { ...loaded, state, context: loaded.context ? { ...loaded.context, captured_inputs: null } : undefined }
      const row = rowOf(next)
      setLoaded(next)
      setFiles([...(row?.files ?? [])])
      setRemaps([...(row?.remaps ?? [])])
      setStatus(remove ? 'removed' : 'saved')
      try {
        await onSaved()
      } catch {
        if (operation.live()) setStatus(remove ? 'removedRecheckFailed' : 'savedRecheckFailed')
      }
    } catch (err) {
      if (!operation.live()) return
      setError(err)
      if (err instanceof ApiError && err.status === 409) setStale(true)
    } finally {
      operation.finish()
    }
  }

  const missing = remaps.filter((from) => !loaded?.remap.rules.some((rule) => rule.from === from))
  const unavailable = heldUnavailable || (loaded && (!scope || loaded.context?.unavailable_reason))
  const unavailableKey = UNAVAILABLE_KEYS[heldUnavailable ?? loaded?.context?.unavailable_reason ?? 'scope_unknown']
  const baseline = loaded ? rowOf(loaded) : undefined
  const capturedSelectionMatches = typeof baseline?.selection_revision === 'string' &&
    baseline.selection_revision.length > 0 && baseline.selection_revision === loaded?.context?.selection_revision
  const capturedRemapMatches = typeof loaded?.context?.input_remap_generation === 'number' &&
    loaded.context.input_remap_generation === loaded.remap.generation
  const draftMatches = files.length === (baseline?.files.length ?? 0) &&
    files.every((file, index) => file === baseline?.files[index]) &&
    remaps.length === (baseline?.remaps.length ?? 0) &&
    remaps.every((from, index) => from === baseline?.remaps[index])
  return (
    <Details open={open} data-input-declaration onToggle={(event) => {
      const next = event.currentTarget.open
      if (!next) { cancel(); return }
      opened.current = next
      setOpen(next)
    }}>
      <Summary className="type-meta" data-input-declaration-toggle>{t('prep.inputs.title')}</Summary>
      {open && <div className="mt-2 flex min-w-0 flex-col gap-2">
        <p className="type-caption">{t('prep.inputs.hint')}</p>
        {(disabled || draftChanged || changed) && <Notice data-input-declaration-changed>{t('prep.inputs.changed')}</Notice>}
        {busy === 'load' && <p className="type-caption" data-input-declaration-loading>{t('prep.inputs.loading')}</p>}
        {unavailable && <Notice data-input-declaration-unavailable>{t(unavailableKey)}</Notice>}
        {error != null && <Notice tone="danger" data-input-declaration-error>{backendErrorText(error)}</Notice>}
        {loaded && scope && !unavailable && <>
          <div data-input-declaration-files className="flex flex-col gap-1">
            <span className="type-meta">{t('prep.inputs.projectFiles')}</span>
            {!files.length && <span className="type-caption">{t('prep.inputs.noFiles')}</span>}
            {files.map((file) => <div key={file} className="flex min-w-0 items-center gap-2" data-input-declaration-file={file}>
              <span className="min-w-0 flex-1 break-all font-mono text-xs text-ink-2">{file}</span>
              <Button size="sm" variant="ghost" disabled={blocked} data-input-declaration-remove-file onClick={() => setFiles((current) => current.filter((item) => item !== file))}>{t('prep.inputs.removeFile')}</Button>
            </div>)}
            <div className="flex min-w-0 items-center gap-1">
              <TextInput data-input-declaration-path value={path} disabled={!editable} placeholder={t('prep.inputs.pathPlaceholder')} aria-label={t('prep.inputs.pathPlaceholder')} onChange={(event) => setPath(event.target.value)} />
              <Button size="sm" disabled={!editable || !path} data-input-declaration-add onClick={() => addFile(false)}>{t('prep.inputs.addFile')}</Button>
            </div>
            {isDesktop() && <Button size="sm" disabled={!editable} data-input-declaration-pick onClick={() => addFile(true)}>{t('prep.inputs.chooseFile')}</Button>}
          </div>
          <div data-input-declaration-remaps className="flex flex-col gap-1">
            <span className="type-meta">{t('prep.inputs.approvedRemaps')}</span>
            {!loaded.remap.rules.length && !missing.length && <span className="type-caption">{t('prep.inputs.noRemaps')}</span>}
            {loaded.remap.rules.map((rule) => <label key={JSON.stringify([rule.kind, rule.from])} className="flex min-w-0 items-start gap-2" data-input-declaration-remap={rule.from}>
              <Checkbox checked={remaps.includes(rule.from)} disabled={blocked || (rule.target_exists === false && !remaps.includes(rule.from))} onChange={(event) => {
                const checked = event.target.checked
                setRemaps((current) => checked ? [...current, rule.from] : current.filter((item) => item !== rule.from))
              }} />
              <span className="min-w-0 break-all font-mono text-xs text-ink-2">{rule.from || t('prep.inputs.anyRelative')}{' → '}{rule.to}{rule.target_exists === false && <span className="ml-1 font-sans">{t('prep.inputs.missingRemap')}</span>}</span>
            </label>)}
            {missing.map((from) => <div key={from} className="flex items-center gap-2" data-input-declaration-missing-remap={from}>
              <span className="min-w-0 flex-1 break-all text-xs text-ink-2">{from || t('prep.inputs.anyRelative')} · {t('prep.inputs.missingRemap')}</span>
              <Button size="sm" disabled={blocked} onClick={() => setRemaps((current) => current.filter((item) => item !== from))}>{t('prep.inputs.removeFile')}</Button>
            </div>)}
          </div>
          {draftMatches && capturedSelectionMatches && capturedRemapMatches && !stale && !changed && loaded.context?.captured_inputs && <p className="break-all font-mono text-xs text-ink-3" data-input-declaration-captured>{t('prep.inputs.captured', { revision: loaded.context.captured_inputs.revision })}</p>}
        </>}
        {status && <Notice data-input-declaration-status>{t(STATUS_KEYS[status])}</Notice>}
        <div className="flex flex-wrap items-center gap-1">
          <Button size="sm" variant="secondary" disabled={!editable || !!missing.length} data-input-declaration-save onClick={() => submit(false)}>{t('prep.inputs.save')}</Button>
          {loaded && rowOf(loaded) && <Button size="sm" disabled={!editable} data-input-declaration-remove onClick={() => submit(true)}>{t('prep.inputs.removeDeclaration')}</Button>}
          <Button size="sm" disabled={blocked} data-input-declaration-reload onClick={() => { preserveOnReload.current = true; setReload((value) => value + 1) }}>{t('prep.inputs.reload')}</Button>
          <Button size="sm" disabled={!!busy && busy !== 'load' && busy !== 'pick' && busy !== 'resolve'} data-input-declaration-cancel onClick={cancel}>{t('prep.inputs.cancel')}</Button>
        </div>
      </div>}
    </Details>
  )
}
