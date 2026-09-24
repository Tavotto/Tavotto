import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { isDesktop } from '@/lib/desktop'
import { STANDARD_PASS_COUNT, usePerfProbeStore } from '@/perf/probeStore'
import { Button } from './ui/Button'

/**
 * 性能探针的浮动面板（ADR 0075）。只在录制期间出现，从「设置 → 诊断 →
 * 性能分析」打开。
 *
 * 它自己**不随每一帧更新**：片段数只在一次拖动结束时变，拖动途中这块面板
 * 不重渲染——否则量到的卡顿里有一份是探针自己的。
 */
export function PerfProbeHud() {
  const { t } = useTranslation('dialogs')
  const phase = usePerfProbeStore((s) => s.phase)
  const segments = usePerfProbeStore((s) => s.segments)
  const pass = usePerfProbeStore((s) => s.pass)
  const notice = usePerfProbeStore((s) => s.notice)
  const result = usePerfProbeStore((s) => s.result)
  const revealFailedPath = usePerfProbeStore((s) => s.revealFailedPath)
  const probe = usePerfProbeStore.getState

  // 选点：下一次按在面板以外的地方，就是要测的对象。按下照常交给画布（选中
  // 它），松手后稍等选区与属性页安顿下来，再在同一点上跑自动测试
  useEffect(() => {
    if (phase !== 'picking') return
    let at: [number, number] | null = null
    const down = (e: PointerEvent) => {
      if ((e.target as Element | null)?.closest?.('[data-perf-probe]')) return
      if (e.button !== 0 || !e.isTrusted) return
      at = [e.clientX, e.clientY]
    }
    const up = () => {
      if (!at) return
      const [x, y] = at
      at = null
      window.setTimeout(() => void probe().runAt(x, y), 500)
    }
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') probe().cancelPick()
    }
    document.addEventListener('pointerdown', down, true)
    document.addEventListener('pointerup', up, true)
    document.addEventListener('keydown', key, true)
    return () => {
      document.removeEventListener('pointerdown', down, true)
      document.removeEventListener('pointerup', up, true)
      document.removeEventListener('keydown', key, true)
    }
  }, [phase, probe])

  if (phase === 'off') return null

  return (
    <div
      data-perf-probe
      data-phase={phase}
      className="absolute right-3 top-3 z-30 flex w-72 flex-col gap-2 rounded-md bg-surface p-3 text-sm shadow-pop"
    >
      <p className="font-medium text-ink">{t('perfProbe.title')}</p>

      {phase === 'recording' && (
        <>
          <p className="text-ink-2">{t('perfProbe.recording')}</p>
          <p className="text-xs tabular-nums text-ink-3" role="status">
            {t('perfProbe.dragCount', { count: segments })}
          </p>
          {notice === 'not_draggable' && (
            <p className="text-xs text-danger" role="alert">
              {t('perfProbe.notDraggable')}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <Button variant="ghost" size="sm" onClick={() => probe().discard()} data-perf-action="discard">
              {t('perfProbe.discard')}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => probe().beginPick()} data-perf-action="runTest">
              {t('perfProbe.runTest')}
            </Button>
            <Button variant="primary" size="sm" onClick={() => void probe().finish()} data-perf-action="finish">
              {t('perfProbe.finish')}
            </Button>
          </div>
        </>
      )}

      {phase === 'picking' && (
        <>
          <p className="text-ink-2" role="status">
            {t('perfProbe.pickTarget')}
          </p>
          <div className="flex justify-end">
            <Button variant="ghost" size="sm" onClick={() => probe().cancelPick()} data-perf-action="back">
              {t('perfProbe.back')}
            </Button>
          </div>
        </>
      )}

      {phase === 'running' && (
        <>
          <p className="text-ink-2" role="status">
            {t('perfProbe.running', { pass, total: STANDARD_PASS_COUNT })}
          </p>
          <div className="flex justify-end">
            <Button variant="ghost" size="sm" onClick={() => probe().stopTest()} data-perf-action="stop">
              {t('perfProbe.stop')}
            </Button>
          </div>
        </>
      )}

      {phase === 'done' && (
        <>
          {result ? (
            <>
              <p className="text-ink-2" role="status">
                {t('perfProbe.saved')}
              </p>
              {/* 桌面：文件名就是「在文件管理器中显示」；浏览器：它已经在下载里了 */}
              {result.dir && isDesktop() ? (
                <button
                  type="button"
                  data-perf-action="reveal"
                  onClick={() => probe().reveal()}
                  className="min-w-0 truncate rounded-sm text-left font-mono text-xs text-ink-2 outline-none hover:underline focus-visible:focus-ring"
                >
                  {result.file}
                </button>
              ) : (
                <p className="truncate font-mono text-xs text-ink-2">{result.file}</p>
              )}
              {revealFailedPath && (
                <p className="break-all text-xs text-danger" role="alert">
                  {t('perfProbe.revealFailed', { path: revealFailedPath })}
                </p>
              )}
              {result.fps != null && result.jankPct != null && (
                <p className="text-xs tabular-nums text-ink-3">
                  {t('perfProbe.summary', { fps: result.fps, jank: result.jankPct })}
                </p>
              )}
            </>
          ) : notice === 'save_failed' ? (
            <p className="text-xs text-danger" role="alert">
              {t('perfProbe.saveFailed')}
            </p>
          ) : (
            <p className="text-ink-2" role="status">
              {t('perfProbe.noData')}
            </p>
          )}
          <div className="flex justify-end gap-1.5">
            <Button variant="secondary" size="sm" onClick={() => probe().close()} data-perf-action="close">
              {t('perfProbe.close')}
            </Button>
            {notice === 'save_failed' && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => void probe().retrySave()}
                data-perf-action="retry-save"
              >
                {t('perfProbe.retrySave')}
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
