import { create } from 'zustand'
import {
  ApiError,
  inspectProjectPackage,
  installProjectPackage,
  openProjectApi,
  type ProjectPackageInspection,
  type ProjectPackageInstallation,
  type ProjectPackageInstallOptions,
} from '@/lib/api'
import { newId } from '@/lib/id'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { openDocumentAs } from '@/store/actions'
import {
  getRestoreGate,
  isDocumentPlaceholder,
  isRestoreTokenCurrent,
  projectFileSnapshot,
  saveNowWithResult,
  settleRestore,
  useDocumentStore,
  waitForWorkingDocumentSave,
  type WorkingDocumentSaveReceipt,
  type WorkingDocumentSaveScope,
} from '@/store/documentStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { useProjectStore } from '@/store/projectStore'

export type ProjectPackageInstallPhase = 'idle' | 'inspecting' | 'review' | 'installing'
  | 'unconfirmed' | 'published' | 'opening' | 'saved' | 'error'

export interface ProjectPackageInstallAttempt {
  attemptId: number
  destination: string
  state: 'installing' | 'completion_unknown' | 'confirmed' | 'rejected'
}

export interface ProjectPackageInstallationReceipt {
  attemptId: number
  installation: ProjectPackageInstallation
  /** Actual document installation is separate from a confirmed disk receipt. */
  adopted?: WorkingDocumentSaveScope
  saved: WorkingDocumentSaveReceipt | null
}

export interface ProjectPackageInstallState {
  phase: ProjectPackageInstallPhase
  inspection: ProjectPackageInspection | null
  /** An installation receipt proves resource publication, never document persistence. */
  installation: ProjectPackageInstallation | null
  /** Null means persistence is unconfirmed; a late/failed read does not prove zero writes. */
  saved: WorkingDocumentSaveReceipt | null
  /** Publication can finish after dismissal; retain its path without reopening the dialog. */
  receipts: ProjectPackageInstallationReceipt[]
  /** Request facts survive lost responses; they do not prove resource publication. */
  attempts: ProjectPackageInstallAttempt[]
  unconfirmedAttempt: ProjectPackageInstallAttempt | null
  error: unknown | null
  inspect: (file: File) => Promise<boolean>
  install: (options: ProjectPackageInstallOptions) => Promise<boolean>
  openInstalled: () => Promise<boolean>
  continueEditing: () => Promise<boolean>
  cancel: () => void
  reopen: (attemptId?: number) => void
  reset: () => void
}

const documentScope = () => {
  const state = useDocumentStore.getState()
  return { documentId: state.documentId, format: state.documentFormat, loadSeq: state.loadSeq }
}
const scopeCurrent = (scope: ReturnType<typeof documentScope>) => {
  const state = useDocumentStore.getState()
  return state.documentId === scope.documentId && state.documentFormat === scope.format && state.loadSeq === scope.loadSeq
}
const unconfirmed = () => new ApiError('Document adoption could not be confirmed.', 503, { code: 'session_unavailable' })
const stale = () => new ApiError('The document changed during installation.', 409, { code: 'stale_write' })

/** Coordinates explicit installation with the existing project switch and document writer. */
export const useProjectPackageInstallStore = create<ProjectPackageInstallState>((set, get) => {
  let generation = 0
  let selectedFile: File | null = null
  let selectedOwner: (() => boolean) | null = null
  let selectedProject: string | null = null
  let selectedAttempt = 0
  let requestSequence = 0
  let cancelRestore: (() => void) | null = null

  const stopRestore = () => {
    const cancel = cancelRestore
    cancelRestore = null
    cancel?.()
  }

  const clear = () => {
    stopRestore()
    generation += 1
    selectedFile = null
    selectedOwner = null
    selectedProject = null
    set({ phase: 'idle', inspection: null, installation: null, saved: null, error: null, receipts: [], attempts: [], unconfirmedAttempt: null })
  }
  const retire = (mine: number) => {
    if (mine !== generation) return
    selectedOwner = null
    set({ phase: 'idle', error: null })
  }

  return {
    phase: 'idle', inspection: null, installation: null, saved: null, error: null, receipts: [], attempts: [], unconfirmedAttempt: null,
    cancel: () => {
      stopRestore()
      generation += 1
      selectedOwner = null
      set({ phase: 'idle', error: null })
    },
    reopen: attemptId => {
      const { receipts, attempts } = get()
      const recoverable = (id: number) => receipts.some(row => row.attemptId === id)
        || attempts.some(row => row.attemptId === id && ['installing', 'completion_unknown'].includes(row.state))
      const id = attemptId ?? (recoverable(selectedAttempt) ? selectedAttempt
        : attempts.findLast(row => recoverable(row.attemptId))?.attemptId ?? receipts.at(-1)?.attemptId)
      const receipt = receipts.find(row => row.attemptId === id)
      const attempt = attempts.find(row => row.attemptId === id)
      if (!receipt && (!attempt || !['installing', 'completion_unknown'].includes(attempt.state))) return
      stopRestore()
      const mine = ++generation
      selectedAttempt = id!
      if (!receipt) {
        // A request path is evidence to review, not permission to reuse an old
        // File/inspection under the project or document currently on screen.
        selectedFile = null
        selectedProject = null
        selectedOwner = null
        set({ phase: 'unconfirmed', inspection: null, installation: null, saved: null, error: null, unconfirmedAttempt: attempt! })
        return
      }
      const guard = captureProjectEpoch()
      const scope = documentScope()
      selectedOwner = () => mine === generation && guard.still() && scopeCurrent(scope)
      set({ phase: receipt.saved ? 'saved' : 'published', installation: receipt.installation, saved: receipt.saved, error: null, unconfirmedAttempt: null })
    },
    reset: clear,

    inspect: async file => {
      // A request in flight may publish resources; cancelling retires only its UI owner.
      stopRestore()
      const mine = ++generation
      const guard = captureProjectEpoch()
      const scope = documentScope()
      const still = () => mine === generation && guard.still() && scopeCurrent(scope)
      selectedFile = file
      selectedAttempt = 0
      selectedProject = guard.pj
      selectedOwner = still
      set({ phase: 'inspecting', inspection: null, installation: null, saved: null, error: null, unconfirmedAttempt: null })
      try {
        const inspection = await inspectProjectPackage(file, guard.pj)
        if (!still()) { retire(mine); return false }
        set({ inspection: structuredClone(inspection), phase: 'review' })
        return true
      } catch (error) {
        if (still()) set({ phase: 'error', error })
        else retire(mine)
        return false
      }
    },

    install: async options => {
      const file = selectedFile
      const still = selectedOwner
      if (!file || !still?.() || !get().inspection || !['review', 'error', 'unconfirmed'].includes(get().phase) || get().installation) return false
      const frozenOptions = structuredClone(options)
      const attemptId = ++requestSequence
      selectedAttempt = attemptId
      const attempt: ProjectPackageInstallAttempt = { attemptId, destination: frozenOptions.destination, state: 'installing' }
      const mine = generation
      set(state => ({ phase: 'installing', error: null, unconfirmedAttempt: null, attempts: [...state.attempts, attempt] }))
      try {
        const installation = await installProjectPackage(file, frozenOptions, selectedProject)
        const frozen = structuredClone(installation)
        // Publication has no cancel endpoint. Preserve every completed attempt's path,
        // even if a newer file, project, or dismissed dialog owns the current UI.
        const confirmed = { ...attempt, state: 'confirmed' as const }
        set(state => ({ receipts: [...state.receipts, { attemptId, installation: frozen, saved: null }],
          attempts: state.attempts.map(row => row.attemptId === attemptId ? confirmed : row),
          ...(state.unconfirmedAttempt?.attemptId === attemptId ? { unconfirmedAttempt: confirmed } : {}) }))
        if (!still()) { retire(mine); return false }
        // Includes an acknowledged published directory from a partial 503 response.
        // The same destination is not installed again to repair later phases.
        set({ phase: 'published', installation: frozen })
        return true
      } catch (error) {
        // A wrapper can reject a malformed success receipt with a local ApiError
        // carrying `receipt`; that is not an acknowledged HTTP rejection.
        const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500 && !('receipt' in error.body)
        const completed: ProjectPackageInstallAttempt = { ...attempt, state: rejected ? 'rejected' : 'completion_unknown' }
        set(state => ({ attempts: state.attempts.map(row => row.attemptId === attemptId ? completed : row),
          ...(state.unconfirmedAttempt?.attemptId === attemptId ? { unconfirmedAttempt: completed } : {}) }))
        if (still()) set({ phase: rejected ? 'error' : 'unconfirmed', error, unconfirmedAttempt: rejected ? null : completed })
        else retire(mine)
        return false
      }
    },

    openInstalled: async () => {
      const installation = get().installation
      if (!installation || get().saved || !['published', 'error'].includes(get().phase) || !selectedOwner?.()) return false
      // A lost confirmation is not proof that the package was never adopted.
      if (get().receipts.find(row => row.attemptId === selectedAttempt)?.adopted) return get().continueEditing()
      const mine = generation
      const attemptId = selectedAttempt
      const oldGuard = captureProjectEpoch()
      const oldScope = documentScope()
      let still = () => mine === generation && oldGuard.still() && scopeCurrent(oldScope)
      const document = structuredClone(installation.doc)
      const preparation: { saved: WorkingDocumentSaveReceipt | null; error: unknown } = { saved: null, error: null }
      set({ phase: 'opening', error: null })
      try {
        await useProjectStore.getState().switchTransaction(async adopt => {
          if (!still()) throw stale()
          finishActiveGesture()
          const edited = projectFileSnapshot()
          // Protect the latest old draft before projectStore replaces its in-memory owner.
          // A quota error, unresolved conflict, or a new edit during this await stops adoption.
          if (!isDocumentPlaceholder()) {
            const flushed = await saveNowWithResult()
            if (!still()) throw stale()
            if (!flushed.wrote || flushed.state === 'save_error' || flushed.state === 'conflict') throw unconfirmed()
          }
          if (!still() || edited()) throw stale()
          const opened = await openProjectApi(installation.path, false)
          if (!still() || edited()) throw stale()
          if (!opened.open || !opened.id) throw unconfirmed()
          await adopt(opened, {
            prepareDocument: async () => {
              // Adoption deliberately changes project epoch. Establish a new owner only
              // in the original prepareDocument slot, never by trusting the old guard.
              const newGuard = captureProjectEpoch()
              const placeholder = documentScope()
              still = () => mine === generation && newGuard.still() && currentProjectId() === opened.id
              if (!still()) return false
              const workingId = newId('d')
              const adoption: { scope?: WorkingDocumentSaveScope } = {}
              // switchDocument installs synchronously, then awaits hydration. Capture
              // that actual scope even if its caller is dismissed during the await.
              const stopObserving = useDocumentStore.subscribe(() => {
                const scope = { pj: opened.id!, ...documentScope() }
                if (adoption.scope || !newGuard.still() || currentProjectId() !== opened.id
                  || scope.documentId !== workingId || scope.format !== 'v4') return
                adoption.scope = scope
                set(state => ({ receipts: state.receipts.map(row => row.attemptId === attemptId ? { ...row, adopted: scope } : row) }))
              })
              try {
                const installed = await openDocumentAs(document, workingId, {
                  format: 'v4', stillCurrent: still,
                  beforeInstall: async () => still() && scopeCurrent(placeholder),
                })
                if (!installed || !still()) return false
                const scope = adoption.scope
                if (!scope || !scopeCurrent(scope)) return false
                selectedOwner = () => still() && scopeCurrent(scope)
                preparation.saved = await waitForWorkingDocumentSave(scope, { persistEmpty: true })
                // The original writer's verified receipt is a fact about this scope,
                // not permission for a retired dialog to claim the current UI.
                if (newGuard.still() && currentProjectId() === opened.id && scopeCurrent(scope)) {
                  const saved = preparation.saved
                  set(state => ({ receipts: state.receipts.map(row => row.attemptId === attemptId && row.adopted === scope ? { ...row, saved } : row) }))
                }
                return still() && scopeCurrent(scope)
              } catch (error) {
                preparation.error = error
                return false
              } finally {
                stopObserving()
              }
            },
          })
          if (!still()) throw stale()
          const gate = getRestoreGate()
          const saved = preparation.saved
          if (!saved || !scopeCurrent({ documentId: saved.address.documentId, format: saved.address.format, loadSeq: saved.loadSeq })
            || gate.state !== 'ready' || gate.reason !== 'prepared' || gate.pj !== opened.id || gate.docId !== saved.address.documentId) {
            throw preparation.error ?? unconfirmed()
          }
        })
        const saved = preparation.saved
        if (!still() || !saved) { retire(mine); return false }
        set(state => ({ phase: 'saved', saved, receipts: state.receipts.map(row => row.attemptId === attemptId ? { ...row, saved } : row) }))
        return true
      } catch (error) {
        if (still()) set({ phase: 'error', error })
        else retire(mine)
        return false
      }
    },

    continueEditing: async () => {
      const { installation, saved, phase } = get()
      const adopted = get().receipts.find(row => row.attemptId === selectedAttempt)?.adopted
      if (!installation || (!saved && !adopted) || !['published', 'saved', 'error'].includes(phase) || !selectedOwner?.()) return false
      const target = saved?.address.projectId ?? adopted?.pj
      if (!target) return false
      const mine = generation
      const readyHere = () => {
        const gate = getRestoreGate(), project = useProjectStore.getState()
        return currentProjectId() === target && project.phase === 'open' && project.project?.id === target
          && gate.state === 'ready' && gate.pj === target
      }
      // Already editing this project: retain its current document and undo history.
      if (readyHere()) { retire(mine); return true }

      const oldGuard = captureProjectEpoch(), oldScope = documentScope()
      let still = () => mine === generation && oldGuard.still() && scopeCurrent(oldScope)
      let stopWatching = () => {}
      let invalidate: (() => void) | null = null
      set({ phase: 'opening', error: null })
      try {
        await useProjectStore.getState().switchTransaction(async adopt => {
          if (!still()) throw stale()
          finishActiveGesture()
          const edited = projectFileSnapshot()
          if (!isDocumentPlaceholder()) {
            const flushed = await saveNowWithResult()
            if (!still()) throw stale()
            if (!flushed.wrote || flushed.state === 'save_error' || flushed.state === 'conflict') throw unconfirmed()
          }
          if (!still() || edited()) throw stale()
          const opened = await openProjectApi(installation.path, false)
          if (!still() || edited()) throw stale()
          if (!opened.open || opened.id !== target) throw unconfirmed()

          // The original adoption synchronously claims a new restore token before its
          // first await. Do not pass prepareDocument: normal restore owns the latest
          // working document, which can be newer than this installation receipt.
          const restoring = adopt(opened)
          const gate = getRestoreGate(), guard = captureProjectEpoch()
          if (gate.state !== 'restoring' || gate.pj !== target) {
            await restoring
            throw unconfirmed()
          }
          const token = { gen: gate.gen, pj: gate.pj }
          still = () => mine === generation && guard.still() && currentProjectId() === target
          invalidate = () => {
            if (isRestoreTokenCurrent(token)) settleRestore(token, { state: 'needs_user', issue: {
              pj: target, gen: token.gen, kind: 'prepare_failed', ref: null, alternatives: [],
            } })
          }
          cancelRestore = invalidate
          stopWatching = onCurrentProjectChange(() => { if (!guard.still()) invalidate?.() })
          if (!still()) invalidate()
          await restoring
          if (!still()) throw stale()
          const completed = getRestoreGate()
          if (completed.gen !== token.gen || completed.docId !== useDocumentStore.getState().documentId || !readyHere()) throw unconfirmed()
        })
        if (!still()) { retire(mine); return false }
        retire(mine)
        return true
      } catch (error) {
        if (still()) {
          const scope = documentScope()
          selectedOwner = () => still() && scopeCurrent(scope)
          set({ phase: 'error', error })
        }
        else retire(mine)
        return false
      } finally {
        stopWatching()
        if (cancelRestore === invalidate) cancelRestore = null
      }
    },
  }
})
