/**
 * 失败页的三个明确出口（§十九）：
 *   ① 返回案例库（teardown + 回 idle）
 *   ② 试试主推案例（失败的用户正是最需要一条 30 秒成功路径的人）
 *   ③ 下载桌面版处理完整项目
 *
 * 会话来源是案例本身失败时（理论上不该发生，但 worker 崩溃/超时都可能），
 * ②仍然给——重试同一条路是合理出口。
 */
import { Button } from '@/components/ui/Button'
import { buttonClass } from '@/components/ui/buttonClass'
import { Download } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
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
      <Button variant="secondary" onClick={onBack}>
        {pg('failBackGallery')}
      </Button>
      <Button variant="secondary" onClick={() => onLaunch(FEATURED_EXAMPLE)}>
        {pg('failTryExample', { name: pg(FEATURED_EXAMPLE.titleKey) })}
      </Button>
      <a href={RELEASES_LATEST_URL} className={buttonClass({ variant: 'primary', size: 'md' })}>
        <Download size={ICON_SIZE.sm} aria-hidden />
        {pg('downloadDesktop')}
      </a>
    </div>
  )
}
