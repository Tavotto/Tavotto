import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { fetchAiHistoryLegacy, restoreAiHistoryLegacy } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '../ui/Button'

const ai = (key: string, values?: Record<string, unknown>) => translate(key, { ns: 'ai', ...(values ?? {}) })

type Phase = 'idle' | 'confirm' | 'busy' | 'failed'

/**
 * 任务历史底部一行：之前装在 Windows 应用包私有区时的任务，这个项目还有几条没带过来（#2282 补救 B）。
 * 没有可找回的（非 Windows、没有旧库、数不出来）整行不出现。确认是原地换内容，不弹全局确认框——
 * 那一层会把任务历史这个弹窗当成「点了外面」关掉（同 FixMemoryRow）。
 */
export function LegacyHistoryLine({ pj, onRestored }: { pj: string | null; onRestored: () => void }) {
  useTranslation('ai')
  const [missing, setMissing] = useState(0)
  const [phase, setPhase] = useState<Phase>('idle')

  useEffect(() => {
    let live = true
    fetchAiHistoryLegacy(pj)
      .then((r) => live && setMissing(Math.max(0, r.missing | 0)))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [pj])

  if (missing <= 0) return null

  const restore = async () => {
    setPhase('busy')
    try {
      const r = await restoreAiHistoryLegacy(pj)
      if (r.outcome === 'done') {
        setMissing(Math.max(0, r.missing | 0))
        setPhase('idle')
        onRestored()
        return
      }
      setPhase('failed')
    } catch {
      setPhase('failed')
    }
  }

  const text =
    phase === 'confirm'
      ? ai('history.legacyConfirm', { count: missing })
      : phase === 'busy'
        ? ai('history.legacyBusy')
        : phase === 'failed'
          ? ai('history.legacyFailed')
          : ai('history.legacyLine', { count: missing })

  return (
    <div
      data-ai-history-legacy={phase}
      className="flex min-h-9 shrink-0 items-center gap-1 border-t border-border py-1 pl-1.5"
    >
      {/* 和按钮同字号；确认那一句深一阶，让人停一下再点 */}
      <span
        className={cn('min-w-0 flex-1 truncate text-sm', phase === 'confirm' ? 'text-ink-2' : 'text-ink-3')}
        title={text}
      >
        {text}
      </span>
      {phase === 'busy' ? null : phase === 'confirm' ? (
        <>
          <Button size="sm" className="text-ink-3" data-confirm-cancel onClick={() => setPhase('idle')}>
            {ai('history.legacyCancel')}
          </Button>
          <Button size="sm" variant="secondary" data-confirm-ok onClick={() => void restore()}>
            {ai('history.legacyRestore')}
          </Button>
        </>
      ) : (
        <Button
          size="sm"
          data-ai-history-legacy-restore
          onClick={() => (phase === 'failed' ? void restore() : setPhase('confirm'))}
        >
          {ai(phase === 'failed' ? 'history.legacyRetry' : 'history.legacyRestore')}
        </Button>
      )}
    </div>
  )
}
