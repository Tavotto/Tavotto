import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { backendErrorText } from '@/lib/api'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { chooseProjectPackage } from '@/lib/projectPackageEntry'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { useProjectPackageInstallStore } from '@/store/projectPackageInstallStore'
import { useUiStore } from '@/store/uiStore'
import { DirBrowser, NewProjectNameDialog, TailPath } from './DirBrowser'
import { Button } from './ui/Button'
import { Details, Summary } from './ui/Details'
import { Dialog } from './ui/Dialog'
import { TextInput } from './ui/Input'
import { Notice } from './ui/Notice'

/** Resource publication and the original document writer have separate receipts. */
export function ProjectPackageInstallDialog() {
  const { t } = useTranslation('dialogs')
  const flow = useProjectPackageInstallStore()
  const projectId = useSyncExternalStore(onCurrentProjectChange, currentProjectId, currentProjectId)
  const [destination, setDestination] = useState('')
  const [browsing, setBrowsing] = useState(false)
  const [createIn, setCreateIn] = useState<string | null>(null)
  const [browseError, setBrowseError] = useState<unknown>(null)
  const choosing = useRef(0)
  const busy = ['inspecting', 'installing', 'opening'].includes(flow.phase)
  const published = flow.installation

  useEffect(() => { choosing.current += 1; setDestination(''); setBrowseError(null) }, [flow.inspection])
  useEffect(() => {
    if (flow.phase === 'idle') { setBrowsing(false); setCreateIn(null) }
  }, [flow.phase])

  const chooseDestination = async () => {
    const mine = ++choosing.current
    const epoch = captureProjectEpoch()
    const stillChoosing = () => {
      const current = useProjectPackageInstallStore.getState()
      return mine === choosing.current && epoch.still()
        && ['review', 'error'].includes(current.phase)
        && current.inspection === inspection && !current.installation
    }
    setBrowseError(null)
    if (!isDesktop()) { setBrowsing(true); return }
    try {
      const parent = await pickDirectory(t('packageInstall.pickParent'))
      if (parent && stillChoosing()) setCreateIn(parent)
    } catch (error) { if (stillChoosing()) setBrowseError(error) }
  }
  const otherReceipts = flow.receipts.filter(row => row.installation.path !== published?.path)
  const unknown = flow.unconfirmedAttempt
  const received = unknown && flow.receipts.find(row => row.attemptId === unknown.attemptId)
  const otherAttempts = flow.attempts.filter(row => (row.state === 'installing' || row.state === 'completion_unknown')
    && row.attemptId !== unknown?.attemptId)
  const error = flow.error ?? browseError
  const inspection = flow.inspection
  const reviewConfiguration = () => {
    if (!flow.saved || currentProjectId() !== flow.saved.address.projectId) return
    flow.cancel()
    useUiStore.getState().setSettingsOpen(true, 'project')
  }

  return (
    <>
      <Dialog
        open={flow.phase !== 'idle'}
        covered={browsing || createIn !== null}
        onOpenChange={open => { if (!open) flow.cancel() }}
        onEscape={flow.cancel}
        title={t('packageInstall.title')}
        description={t('packageInstall.description')}
        size="lg"
        anchor="project-package-install"
        status={
          <div className="flex flex-col gap-2">
            {error != null && <Notice tone="danger">{backendErrorText(error)}</Notice>}
            {flow.phase === 'inspecting' && <Notice>{t('packageInstall.inspecting')}</Notice>}
            {flow.phase === 'installing' && <Notice>{t('packageInstall.installing')}</Notice>}
            {flow.phase === 'opening' && <Notice>{t('packageInstall.opening')}</Notice>}
            {flow.phase === 'saved' && flow.saved && <Notice tone="ok">{t('packageInstall.saved')}</Notice>}
            {unknown && <Notice tone={received ? 'ok' : 'warn'}>{t(received ? 'packageInstall.receivedResult' : 'packageInstall.completionUnknown')}</Notice>}
            {published && published.state !== 'resources_installed' && (
              <Notice tone="warn" detail={published.path}>{t('packageInstall.publicationUnconfirmed')}</Notice>
            )}
            {published && published.configuration !== 'ready' && (
              <Notice tone="warn" action={flow.saved && projectId === flow.saved.address.projectId
                ? <Button disabled={busy} data-project-package-review-configuration onClick={reviewConfiguration}>{t('packageInstall.reviewConfiguration')}</Button>
                : undefined}>{t('packageInstall.configurationReview')}</Notice>
            )}
          </div>
        }
        footer={{
          start: <Button size="lg" disabled={busy} data-project-package-choose-another onClick={() => chooseProjectPackage(true)}>{t('packageInstall.chooseAnother')}</Button>,
          secondary: <Button variant="secondary" size="lg" data-project-package-close onClick={flow.cancel}>{t('packageInstall.close')}</Button>,
          primary: flow.saved
            ? <Button variant="primary" size="lg" data-project-package-continue loading={flow.phase === 'opening'} disabled={busy} onClick={() => void flow.continueEditing()}>{t('packageInstall.continueEditing')}</Button>
            : published
              ? <Button variant="primary" size="lg" data-project-package-open loading={flow.phase === 'opening'} disabled={busy} onClick={() => void flow.openInstalled()}>{t('packageInstall.openInstalled')}</Button>
              : received
                ? <Button variant="primary" size="lg" data-project-package-view-result onClick={() => flow.reopen(received.attemptId)}>{t('packageInstall.viewResult')}</Button>
              : flow.phase === 'unconfirmed'
                ? null
              : <Button variant="primary" size="lg" data-project-package-install loading={flow.phase === 'installing'} disabled={busy || !inspection || !destination.trim()} onClick={() => void flow.install({ destination: destination.trim() })}>{t('packageInstall.install')}</Button>,
        }}
      >
        <div className="flex flex-col gap-4" aria-busy={busy || undefined}>
          {inspection && !published && (
            <>
              <p className="type-reading">{inspection.doc.project.name}</p>
              <p className="type-caption">{t('packageInstall.contents', { canvases: inspection.doc.canvases.length, resources: inspection.resources.length, snapshots: inspection.figure_snapshots })}</p>
              <label className="flex flex-col gap-2">
                <span>{t('packageInstall.destination')}</span>
                <span className="flex items-center gap-2">
                  <TextInput className="min-w-0 flex-1" data-project-package-destination value={destination} onChange={event => setDestination(event.target.value)} disabled={busy} aria-label={t('packageInstall.destination')} />
                  <Button variant="secondary" data-project-package-choose-destination disabled={busy} onClick={() => void chooseDestination()}>{t('packageInstall.chooseDestination')}</Button>
                </span>
              </label>
              <p className="type-caption">{t('packageInstall.newDirectory')}</p>
              {inspection.missing.length > 0 && (
                <Notice tone="warn" detail={inspection.missing.join('\n')}>{t('packageInstall.missing', { count: inspection.missing.length })}</Notice>
              )}
              {inspection.execution_coverage?.state === 'incomplete' && <Notice>{t('packageInstall.dependencies')}</Notice>}
            </>
          )}
          {published && (
            <>
              <p>{t('packageInstall.installedAt')}</p>
              <TailPath path={published.path} />
              {!flow.saved && <p className="type-caption">{t('packageInstall.documentPending')}</p>}
            </>
          )}
          {unknown && !published && (
            <div data-project-package-unconfirmed>
              <p>{t('packageInstall.attemptedAt')}</p>
              <TailPath path={unknown.destination} />
            </div>
          )}
          {(otherReceipts.length > 0 || otherAttempts.length > 0) && (
            <Details>
              <Summary>{t('packageInstall.previous')}</Summary>
              <ul className="mt-2 flex flex-col gap-2">
                {otherReceipts.map(row => (
                  <li key={row.attemptId}>
                    <Button className="max-w-full" data-project-package-reopen={row.attemptId} onClick={() => flow.reopen(row.attemptId)} disabled={busy}><TailPath path={row.installation.path} /></Button>
                  </li>
                ))}
                {otherAttempts.map(row => (
                  <li key={'attempt-' + row.attemptId}>
                    <Button className="max-w-full" data-project-package-attempt={row.attemptId} onClick={() => flow.reopen(row.attemptId)} disabled={busy}>
                      <TailPath path={row.destination} /> · {t('packageInstall.resultPending')}
                    </Button>
                  </li>
                ))}
              </ul>
            </Details>
          )}
        </div>
      </Dialog>
      {browsing && (
        <DirBrowser mode="create" title={t('packageInstall.chooseDestination')} onClose={() => setBrowsing(false)} onPick={path => { setBrowsing(false); setDestination(path) }} />
      )}
      {createIn && (
        <NewProjectNameDialog parent={createIn} onClose={() => setCreateIn(null)} onCreate={name => { setDestination(createIn + '/' + name); setCreateIn(null) }} />
      )}
    </>
  )
}
