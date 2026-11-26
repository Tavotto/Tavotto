import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  backendErrorText, commitFigureStyle, previewFigureStyle,
  restoreScriptBackup, type FigureStylePreview,
} from '@/lib/api'
import { reloadEditedProjectLayout } from '@/store/actions'
import { activeProjectFile, getWindowHold, saveNowWithResult, useDocumentStore } from '@/store/documentStore'
import { useEnvStore } from '@/store/envStore'
import { captureSaveContext, stillCurrent, type SaveContext } from '@/store/saveContext'
import { Button } from './ui/Button'
import { Checkbox } from './ui/Checkbox'
import { Dialog } from './ui/Dialog'
import { FormRow } from './FormRow'
import { Notice } from './ui/Notice'

export function ScriptStyleAction({
  visible, available, canvas, panel,
}: { visible: boolean; available: boolean; canvas: string | null; panel: string | null }) {
  const { t } = useTranslation(['dialogs', 'errors', 'common'])
  const file = useDocumentStore(s => s.projectFile)
  const [preview, setPreview] = useState<FigureStylePreview | null>(null)
  const [origin, setOrigin] = useState<SaveContext | null>(null)
  const [finished, setFinished] = useState<{ id: string; sha: string } | null>(null)
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const canStart = available && canvas !== null && panel !== null && !!file?.revision && !file.dirty

  async function prepare() {
    const binding = activeProjectFile()
    if (!binding?.revision || binding.dirty || !canvas || !panel || getWindowHold()) return
    const ctx = captureSaveContext()
    setOrigin(ctx)
    setPreview(null)
    setFinished(null)
    setChecked(false)
    setBusy(true)
    setError(null)
    try {
      const saved = await saveNowWithResult()
      if (!stillCurrent(ctx)) {
        setOrigin(null)
        return
      }
      if (!saved.wrote || activeProjectFile()?.dirty) {
        setError(t('export.sourceStyle.saveFirst'))
        return
      }
      const entry = await previewFigureStyle({
        layout: binding.name, revision: binding.revision, canvas_id: canvas, panel_id: panel,
        working_doc_id: ctx.documentId,
      }, ctx.pj)
      if (stillCurrent(ctx)) setPreview(entry)
      else setOrigin(null)
    } catch (cause) {
      setError(backendErrorText(cause))
    } finally {
      setBusy(false)
    }
  }

  async function commit() {
    const binding = activeProjectFile()
    if (!origin || !preview || !checked || !binding || binding.dirty ||
        !stillCurrent(origin) || getWindowHold()) return
    setBusy(true)
    setError(null)
    try {
      const reply = await commitFigureStyle(preview.token, origin.pj)
      useEnvStore.getState().bumpScriptBackups()
      setFinished({ id: reply.backup_id, sha: reply.script_revision })
      if (!(await reloadEditedProjectLayout(origin, binding.name, reply.revision, reply.working_revision))) {
        setError(t('export.sourceStyle.reopen'))
      }
    } catch (cause) {
      setError(backendErrorText(cause))
    } finally {
      setBusy(false)
    }
  }

  async function restore() {
    const binding = activeProjectFile()
    if (!origin || !finished || !binding || binding.dirty || getWindowHold()) return
    const ctx = captureSaveContext()
    if (ctx.pj !== origin.pj || ctx.file !== origin.file) return
    setBusy(true)
    setError(null)
    try {
      const reply = await restoreScriptBackup(finished.id, 'full', finished.sha, ctx.pj, ctx.documentId)
      useEnvStore.getState().bumpScriptBackups()
      setFinished(null)
      setPreview(null)
      const layout = reply.layout
      if (layout && await reloadEditedProjectLayout(ctx, layout.name, layout.revision, layout.working_revision)) {
        setOrigin(null)
      } else setError(t('export.sourceStyle.reopen'))
    } catch (cause) {
      setError(backendErrorText(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {visible && (
        <FormRow>
          <Button
            data-r-source-style
            disabled={!canStart || busy}
            title={canStart ? undefined : t('export.sourceStyle.saveFirst')}
            onClick={prepare}
          >
            {t('export.sourceStyle.open')}
          </Button>
        </FormRow>
      )}
      <Dialog
        open={origin !== null}
        onOpenChange={open => { if (!open) setOrigin(null) }}
        title={t('export.sourceStyle.title')}
        size="lg"
        busy={busy}
        footer={{
          secondary: <Button size="lg" onClick={() => setOrigin(null)}>{t('common:actions.close')}</Button>,
          primary: finished
            ? <Button size="lg" variant="secondary" onClick={restore}>{t('export.sourceStyle.restore')}</Button>
            : <Button size="lg" variant="danger" disabled={!preview || !checked || !!file?.dirty} onClick={commit}>
                {t('export.sourceStyle.commit')}
              </Button>,
        }}
        status={error && <Notice tone="danger">{error}</Notice>}
      >
        {finished ? <Notice tone="ok">{t('export.sourceStyle.done')}</Notice> : preview ? (
          <div className="flex flex-col gap-3">
            <Notice tone="warn" title={t('errors:engine.rewriteWarning')}>
              <span className="break-all font-mono text-sm">{preview.script_abs}</span>
            </Notice>
            <p>{t('export.sourceStyle.count', { written: preview.written, remaining: preview.skipped.length })}</p>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-surface-2 p-3 font-mono text-xs"
              data-r-source-diff>{preview.diff.join('\n')}</pre>
            <p>{t('errors:engine.rewriteBackups')}</p>
            <ul className="break-all font-mono text-sm">
              {preview.backups.map(path => <li key={path}>{path}</li>)}
            </ul>
            {preview.git && (
              <p>{preview.git.tracked
                ? preview.git.dirty ? t('errors:engine.rewriteGitDirty') : t('errors:engine.rewriteGitClean')
                : t('errors:engine.rewriteGitUntracked')}</p>
            )}
            {preview.checksums.length > 0 &&
              <Notice tone="warn">{t('errors:engine.rewriteChecksums', { files: preview.checksums.join(', ') })}</Notice>}
            <label className="flex items-start gap-2">
              <Checkbox checked={checked} onChange={event => setChecked(event.target.checked)} />
              {t('errors:engine.rewriteConfirm')}
            </label>
          </div>
        ) : busy ? <p>{t('export.sourceStyle.verifying')}</p> : null}
      </Dialog>
    </>
  )
}
