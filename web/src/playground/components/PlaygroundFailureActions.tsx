/**
 * 失败页的三个明确出口（§十九）：
 *   ① 返回案例库（teardown + 回 idle）
 *   ② 试试主推案例（失败的用户正是最需要一条 30 秒成功路径的人）
 *   ③ 下载桌面版处理完整项目
 *
 * 会话来源是案例本身失败时（理论上不该发生，但 worker 崩溃/超时都可能），
 * ②仍然给——重试同一条路是合理出口。
 */
import { Download } from 'lucide-react'
import { RELEASES_LATEST_URL } from '@/lib/brand'
import { FEATURED_EXAMPLE, type PlaygroundExample } from '../examples'
import { pg } from '../pgText'

export function PlaygroundFailureActions({
  onBack,
  onLaunch,
}: {
  onBack: () => void
  onLaunch: (example: PlaygroundExample) => void
}) {
  return (
    <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
      <button
        onClick={onBack}
        className="h-7 rounded-[6px] border border-border px-3 text-xs text-ink-2 transition-colors hover:border-ink-faint hover:text-ink"
      >
        {pg('failBackGallery')}
      </button>
      <button
        onClick={() => onLaunch(FEATURED_EXAMPLE)}
        className="h-7 rounded-[6px] border border-border px-3 text-xs text-ink-2 transition-colors hover:border-ink-faint hover:text-ink"
      >
        {pg('failTryExample', { name: pg(FEATURED_EXAMPLE.titleKey) })}
      </button>
      <a
        href={RELEASES_LATEST_URL}
        className="flex h-7 items-center gap-1.5 rounded-[6px] bg-ink px-3 text-xs text-white transition-opacity hover:opacity-90"
      >
        <Download size={12} aria-hidden />
        {pg('downloadDesktop')}
      </a>
    </div>
  )
}
