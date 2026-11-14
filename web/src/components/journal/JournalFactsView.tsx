import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { Select } from '@/components/ui/Select'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { readJournalFacts, type FactRecord, type FactsPage, type FactSource, type FigureKind, type SubmissionStage } from '@/lib/journalFactsView'
import { useDocumentStore } from '@/store/documentStore'

/** Volatile source viewer: does not change a document, profile, or preflight. */
export function JournalFactsView() {
  const ref = useDocumentStore((s) => s.doc.profile?.journalRef)
  const documentId = useDocumentStore((s) => s.documentId)
  const canvasId = useDocumentStore((s) => s.activeCanvasId)
  const projectId = useDocumentStore((s) => s.projectMeta.id)
  if (!ref) return null
  return <JournalFactsSession key={JSON.stringify([projectId, documentId, canvasId, ref.id])} journalId={ref.id} journalLabel={ref.label} />
}

function JournalFactsSession({ journalId, journalLabel }: { journalId: string; journalLabel: string }) {
  const { t } = useTranslation('dialogs')
  const objects = useDocumentStore((s) => s.doc.objects)
  const panels = useMemo(() => objects.filter((o) => o.type === 'panel'), [objects])
  const [open, setOpen] = useState(false)
  const [objectId, setObjectId] = useState<string | null>(null)
  const [kind, setKind] = useState<FigureKind>('unknown')
  const [stage, setStage] = useState<SubmissionStage>('unknown')
  const [page, setPage] = useState<FactsPage | null>(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pending = useRef<AbortController | null>(null)
  const sequence = useRef(0)
  const selectedPanel = panels.find((panel) => panel.id === objectId)
  const previousPanel = useRef(selectedPanel)
  useEffect(() => {
    if (previousPanel.current && previousPanel.current !== selectedPanel) {
      reset(); setObjectId(null); setKind('unknown'); setStage('unknown')
    }
    previousPanel.current = selectedPanel
  }, [selectedPanel])
  useEffect(() => {
    const unsubscribe = onCurrentProjectChange(() => {
      reset(); setOpen(false); setObjectId(null); setKind('unknown'); setStage('unknown')
    })
    return () => { unsubscribe(); sequence.current++; pending.current?.abort() }
  }, [])

  function reset() {
    sequence.current++
    pending.current?.abort()
    setPage(null); setOffset(0); setError(null); setLoading(false)
  }
  async function load(next: number, expected: number | null) {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    const seq = ++sequence.current
    const pj = currentProjectId()
    const doc = useDocumentStore.getState()
    const documentId = doc.documentId
    const canvasId = doc.activeCanvasId
    const selected = objectId === null ? null : panels.find((p) => p.id === objectId)
    if (objectId !== null && !selected) { reset(); return }
    setPage(null); setError(null); setLoading(true)
    const selection = selected ? { object_label: selected.id, figure_kind: kind, stage } : null
    const result = await readJournalFacts(journalId, next, expected, selection, controller.signal)
    const now = useDocumentStore.getState()
    if (controller.signal.aborted || seq !== sequence.current) return
    if (currentProjectId() !== pj || now.documentId !== documentId || now.activeCanvasId !== canvasId || now.doc.profile?.journalRef?.id !== journalId) {
      reset(); setError('context_changed'); return
    }
    if (selected && !now.doc.objects.some((o) => o.id === selected.id && o === selected)) {
      setLoading(false); setError('object_changed'); return
    }
    setLoading(false)
    if (!result.ok) { setError(result.code); return }
    setPage(result.page); setOffset(next)
  }

  const text = (key: string) => t(`journal.factView.${key}`)
  const syncLabel = (state: string) => text(({ updated: 'syncUpdated', unchanged: 'syncUnchanged', offline: 'syncOffline', disabled: 'syncDisabled', rejected: 'syncRejected' } as Record<string, string>)[state] ?? 'syncOther')
  const strengthLabel = (value: string) => {
    const key = ({ required: 'required', recommended: 'recommended', preferred: 'preferred', permitted: 'permitted', typical: 'typical', example: 'example', unspecified: 'unspecifiedStrength' } as Record<string, string>)[value]
    return key ? text(key) : `${text('cannotInterpret')}: ${value}`
  }
  const stageLabel = (value: string) => {
    const key = ({ any: 'anyStage', unknown: 'unknown', submission: 'submission', revision: 'revision', accepted: 'accepted', production: 'production' } as Record<string, string>)[value]
    return key ? text(key) : `${text('cannotInterpret')}: ${value}`
  }
  const originLabel = (value: string) => value === 'journal' ? text('originJournal') : value === 'publisher' ? text('originPublisher') : `${text('cannotInterpret')}: ${value}`
  const figureKindLabel = (value: unknown): string | null => {
    if (value === 'line_art') return text('lineArt')
    if (value === 'halftone') return text('halftone')
    if (value === 'combination') return text('combination')
    return null
  }
  const conditionLabel = (key: string, value: unknown): string | null => {
    if (key !== 'figure_kind') return null
    const single = figureKindLabel(value)
    if (single) return t('journal.factView.figureCondition', { value: single })
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const predicate = value as Record<string, unknown>
      if (Object.keys(predicate).length === 2 && predicate.relation === 'in' &&
          Array.isArray(predicate.value) && predicate.value.length > 0) {
        const labels = predicate.value.map(figureKindLabel)
        if (labels.every((label) => label !== null))
          return t('journal.factView.figureConditionOneOf', { value: labels.join(', ') })
      }
    }
    return null
  }
  const valueLabel = (fact: FactRecord): string | null => {
    if (!['raster_dpi', 'raster_resolution'].includes(fact.subject) || fact.unit !== 'dpi' ||
        typeof fact.value !== 'number' || !Number.isFinite(fact.value)) return null
    switch (fact.relation) {
      case 'gte': return t('journal.factView.dpiAtLeast', { value: fact.value })
      case 'gt': return t('journal.factView.dpiAbove', { value: fact.value })
      case 'lte': return t('journal.factView.dpiAtMost', { value: fact.value })
      case 'lt': return t('journal.factView.dpiBelow', { value: fact.value })
      case 'eq': return t('journal.factView.dpiExact', { value: fact.value })
      default: return null
    }
  }
  const errorLabel = (code: string) => code === 'session_auth_required' ? t('backend.session_auth_required', { ns: 'errors' }) : text(
    code === 'context_changed' ? 'errorContextChanged' : code === 'object_changed' ? 'errorObjectChanged' : code === 'catalog_changed' ? 'errorChanged' : code === 'unknown_journal' ? 'errorMissing' :
      code === 'result_too_large' ? 'errorLarge' : ['quota_hour', 'quota_day', 'ip_day', 'ip_tokens'].includes(code) ? 'errorQuota' :
        ['signature_invalid', 'index_invalid', 'key_unknown', 'rev_rollback', 'index_conflict', 'bundle_invalid', 'cache_invalid', 'invalid_json'].includes(code) ? 'errorIntegrity' :
          ['offline', 'disabled', 'network', 'server_error'].includes(code) ? 'errorOffline' : 'errorOther',
  )
  const source = (record: FactSource | undefined, anchor?: string) => {
    if (!record) return null
    let href: string | null = null
    try {
      const url = new URL(record.url)
      if (url.protocol === 'https:' && !url.username && !url.password && !url.hash) {
        // A legal source URL may end in an empty '#'; remove it before adding a locator.
        url.hash = ''
        href = url.href
        if (record.kind === 'pdf' && record.page) href += `#page=${record.page}`
        else if (anchor) href += anchor
      }
    } catch { /* invalid URL is not a link */ }
    return <div className="break-all text-xs text-ink-2">{text('source')}: {href ? <a href={href} target="_blank" rel="noreferrer">{record.url}</a> : record.url} · {t('journal.factView.retrieved', { date: record.retrieved })}{record.page ? ` · p. ${record.page}` : ''}</div>
  }
  return <div data-journal-facts-view className="mt-3 flex flex-col gap-2">
    <Button size="sm" variant="secondary" aria-expanded={open} onClick={() => { if (open) reset(); setOpen(!open) }}>{text(open ? 'close' : 'open')}</Button>
    {open && <div className="flex flex-col gap-2 text-sm">
      <p className="text-ink-2">{text('notice')}</p>
      <div className="grid gap-2">
        <Select title={selectedPanel ? `${selectedPanel.id} · ${selectedPanel.name || selectedPanel.fileId}` : undefined} prefix={text('object')} value={objectId === null ? 'none' : `panel:${objectId}`} onChange={(v) => { reset(); setKind('unknown'); setStage('unknown'); previousPanel.current = undefined; setObjectId(v === 'none' ? null : v.slice(6)) }} ariaLabel={text('object')}
          options={[{ value: 'none', label: text('noObject') }, ...panels.map((p) => ({ value: `panel:${p.id}`, label: `${p.id} · ${p.name || p.fileId}` }))]} />
        <Select prefix={text('kind')} disabled={objectId === null} value={kind} onChange={(v) => { reset(); setKind(v) }} ariaLabel={text('kind')} options={[
          { value: 'unknown', label: text('unknown') }, { value: 'line_art', label: text('lineArt') },
          { value: 'halftone', label: text('halftone') }, { value: 'combination', label: text('combination') },
        ]} />
        <Select prefix={text('stage')} disabled={objectId === null} value={stage} onChange={(v) => { reset(); setStage(v) }} ariaLabel={text('stage')} options={[
          { value: 'unknown', label: text('unknown') }, { value: 'submission', label: text('submission') },
          { value: 'revision', label: text('revision') }, { value: 'accepted', label: text('accepted') },
          { value: 'production', label: text('production') },
        ]} />
      </div>
      <Button size="sm" variant="secondary" disabled={loading} onClick={() => { reset(); void load(0, null) }}>{text(loading ? 'loading' : 'load')}</Button>
      {error && <p data-facts-error role="status" className="text-ink-2">{errorLabel(error)}</p>}
      {page && <div data-facts-page className="flex flex-col gap-3">
        <p>{t('journal.factView.identity', { selected: journalLabel, returned: page.journal.label })}</p>
        <p className="break-all text-xs text-ink-2">{text('journalId')}: {page.journal_id} · {text('publisherId')}: {page.journal.publisher_id}{page.journal.issn ? ` · ISSN: ${page.journal.issn}` : ''}{page.journal.eissn ? ` · eISSN: ${page.journal.eissn}` : ''}</p>
        <p className="text-xs text-ink-2">{syncLabel(page.sync_state)} · {t('journal.factView.page', { rev: page.catalog_rev, offset })}</p>
        {page.facts.length === 0 && page.coverage.length === 0 && <p>{text('noFacts')}</p>}
        {page.facts.map((fact) => {
          const comparison = page.inspection?.facts.find((item) => item.id === fact.id)?.structured_conditions.status
          return <div data-fact-id={fact.id} key={fact.id} className="flex min-w-0 flex-col gap-1 border-t border-line pt-2">
            {valueLabel(fact) ? <p>{text('value')}: {valueLabel(fact)}</p> :
              <p className="break-all">{text('cannotInterpret')}: {fact.subject} · {fact.relation} · {JSON.stringify(fact.value)} {fact.unit ?? ''}</p>}
            <p>{text('strength')}: {strengthLabel(fact.strength)} · {text('stageLabel')}: {stageLabel(fact.stage)} · {text('origin')}: {originLabel(fact.origin)}</p>
            <p className="text-ink-2">{text('sourceScopeUnknown')}</p>
            {fact.conditions && Object.entries(fact.conditions).map(([key, value]) => {
              const label = conditionLabel(key, value)
              return <p key={key} className="break-words">{label ?? `${text('cannotInterpret')}: ${JSON.stringify({ [key]: value })}`}</p>
            })}
            {fact.condition_text && <p>{text('conditionText')}: {fact.condition_text}</p>}
            {fact.derivation && <p>{text('derivation')}: {fact.derivation}</p>}
            <p>{text('quote')}: {fact.quote}</p>
            {source(page.sources[fact.source], fact.anchor)}
            {fact.context?.map((context, i) => <div key={i}><p>{text('context')}: {context.quote}</p>{source(page.sources[context.source])}</div>)}
            {comparison && <p>{text('comparison')}: {text(comparison === 'match' ? 'match' : comparison === 'mismatch' ? 'mismatch' : 'unresolved')}</p>}
            <p className="text-ink-2">{text('notVerified')}</p>
          </div>
        })}
        {page.coverage.map((coverage) => <div key={coverage.id}><p>{text('coverage')}: {coverage.scope} · {text('cannotInterpret')}: {coverage.subject}</p>{coverage.sources.map((id) => <div key={id}>{source(page.sources[id])}</div>)}</div>)}
        {page.next_offset !== null && <Button size="sm" variant="secondary" onClick={() => void load(page.next_offset!, page.catalog_rev)}>{text('next')}</Button>}
      </div>}
      {(page || error) && <Button size="sm" variant="secondary" onClick={() => { reset(); void load(0, null) }}>{text('restart')}</Button>}
    </div>}
  </div>
}
