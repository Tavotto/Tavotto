/**
 * 真实加载阶段（从 PlaygroundApp 抽出并带上会话来源）。
 *
 * 继续使用现有阶段协议 runtime → engine → packages → script → figures：
 * 确切阶段逐个点亮，**没有假造的百分比**。案例来源时标题写案例名，
 * 上传来源写文件名。已完成/进行中/未开始三态不只靠颜色（✓ / spinner /
 * 空圈三种形状）。aria-live 让当前阶段可被读出。
 *
 * 「取消」走 PlaygroundApp 的 cancelLoading：真正 dispose 在途 Worker
 * 回到案例库，不是把加载藏起来。
 */
import { Check, Loader2 } from 'lucide-react'
import type { PlaygroundPhase } from '../protocol'
import { pg } from '../pgText'

const PHASE_ORDER: (PlaygroundPhase | 'start')[] = [
  'start',
  'runtime',
  'engine',
  'packages',
  'script',
  'figures',
]

export function PlaygroundLoading({
  phase,
  filename,
  title,
  onCancel,
}: {
  phase: PlaygroundPhase | 'start'
  filename: string
  /** 人类可读的会话名：案例名（i18n 过的）或上传的文件名 */
  title: string
  onCancel: () => void
}) {
  const at = PHASE_ORDER.indexOf(phase)
  const steps: { key: string; values?: Record<string, unknown> }[] = [
    { key: 'phaseRuntime' },
    { key: 'phaseEngine' },
    { key: 'phasePackages' },
    { key: 'phaseScript', values: { filename } },
    { key: 'phaseFigures' },
  ]
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-5 p-6">
      <p className="text-[15px] font-medium text-ink">{pg('preparing', { name: title })}</p>
      {/* 真话进度：确切阶段逐个点亮，没有假造的百分比 */}
      <ol className="flex flex-col gap-2.5" aria-live="polite">
        {steps.map((s, i) => {
          const stepAt = i + 1 // PHASE_ORDER 里 'start' 占 0 位
          const state = at > stepAt ? 'done' : at === stepAt ? 'active' : 'todo'
          return (
            <li key={s.key} className="flex items-center gap-2.5 text-[13px]">
              {state === 'done' ? (
                <Check size={14} className="shrink-0 text-ink-3" aria-hidden />
              ) : state === 'active' ? (
                <Loader2 size={14} className="shrink-0 animate-spin text-sel" aria-hidden />
              ) : (
                <span className="h-3.5 w-3.5 shrink-0 rounded-full border border-border" aria-hidden />
              )}
              <span className={state === 'todo' ? 'text-ink-faint' : 'text-ink-2'}>
                {pg(s.key, s.values)}
              </span>
            </li>
          )
        })}
      </ol>
      <button
        onClick={onCancel}
        className="h-7 rounded-[6px] border border-border px-3 text-xs text-ink-2 transition-colors hover:border-ink-faint hover:text-ink"
      >
        {pg('cancelLoading')}
      </button>
    </div>
  )
}
