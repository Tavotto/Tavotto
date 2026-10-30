import { useEffect, useRef, useState } from 'react'
import { t } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { aiCancel, backendErrorText, discardAiEditDependencies, fetchAiSession, resumeAiEditDependencies, type ConvertDependencyCard } from '@/lib/api'
import { dependencyDecision } from '@/components/DependencyDecision'
import { useAiStore, type AiSession, type AiStatus } from '@/store/aiStore'
import { useEnvStore } from '@/store/envStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { Notice } from '../ui/Notice'

const ai = (key: string, values?: Record<string, unknown>) => t(key, { ns: 'ai', ...(values ?? {}) })
const envIdentity = () => {
  const env = useEnvStore.getState().env
  return [env?.python ?? '', env?.project?.source ?? '', env?.project?.python ?? ''].join('\u0000')
}

/** Authorize the existing dependency plan, then follow the new task to its final snapshot. */
export function EditDependencyNotice({ session, card }: { session: AiSession; card: ConvertDependencyCard }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [resumeId, setResumeId] = useState<string | null>(null)
  const [envBase, setEnvBase] = useState<string | null>(null)
  const alive = useRef(true)
  const env = useEnvStore((s) => s.env)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  async function start(action: 'install' | 'replan') {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const result = await resumeAiEditDependencies(session.id, session.project, action, action === 'install' ? card.impact_digest : undefined)
      if (!alive.current) return
      setResumeId(result.session)
      for (;;) {
        const detail = await fetchAiSession(result.session, session.project)
        if (!alive.current) return
        if (detail.final) {
          const status: AiStatus = detail.status === 'interrupted' ? 'failed' :
            (['done', 'failed', 'timeout', 'cancelled', 'reverted'].includes(detail.status) ? detail.status as AiStatus : 'failed')
          useAiStore.setState((s) => ({ sessions: s.sessions.map((entry) => entry.id === session.id && entry.project === session.project ? {
            ...entry, id: result.session, status, finalized: true, changed: !!detail.changed,
            diff: detail.diff ?? '', error: detail.error ?? undefined, task: detail.task, finishedAt: Date.now(),
          } : entry) }))
          setBusy(false)
          return
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
      }
    } catch (e) {
      if (alive.current) { setError(backendErrorText(e)); setBusy(false) }
    }
  }

  useEffect(() => {
    if (envBase !== null && envIdentity() !== envBase && !busy) {
      setEnvBase(null)
      void start('replan')
    }
    // env is the published environment snapshot; start is scoped to this card.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [env, envBase, busy])

  async function decline() {
    try {
      await discardAiEditDependencies(session.id, session.project)
      useAiStore.setState((s) => ({ sessions: s.sessions.map((entry) => entry.id === session.id ? { ...entry, task: undefined } : entry) }))
    } catch (e) { if (alive.current) setError(backendErrorText(e)) }
  }

  const installedLate = card.notice === 'dependency_installed_late'
  const flow = installedLate ? null : dependencyDecision(card, {
    install: () => void start('install'),
    decline: () => void decline(),
    chooseEnv: () => { setEnvBase(envIdentity()); useUiStore.getState().openEngineEnvPicker() },
  })
  const workspace = (key: string) => t(`prep.${key}`, { ns: 'workspace' })
  return (
    <Notice tone="warn" data-ai-edit-dependency action={busy ? (
      resumeId ? <Button size="sm" variant="ghost" onClick={() => void aiCancel(resumeId, session.project)}>{ai('dependency.stop')}</Button> : null
    ) : installedLate ? <><Button size="sm" onClick={() => void start('replan')}>{workspace('convert.deps.btn.continue')}</Button><Button size="sm" variant="ghost" onClick={() => void decline()}>{workspace('convert.deps.btn.decline')}</Button></> : <>{flow?.primary}{flow?.ghost}</>} detail={error || undefined}>
      {busy ? ai('dependency.installing') : installedLate ? workspace('convert.deps.installedLate') : `${ai('dependency.missing', { names: listJoin(card.packages) })} ${flow?.title}`}
    </Notice>
  )
}
