import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCw } from '@/components/ui/icons'
import { Button } from './ui/Button'
import { Banner } from './DocumentBanner'

/**
 * 页面里跑的前端与后端不是同一次构建（`useBuildVersion`）时的那一句话。
 *
 * **非模态**（2026-10-07 设计审计 §10.1）：此前它叫 UpdateBanner，实际是启动时自动弹出的对话框——一件
 * 「你可以刷新一下」的事挡住了整个界面。现在是工作面板顶上的一条内嵌说明条（与冲突 / 接入状态同一摞），
 * 只提示不自动刷新——用户可能正在图内编辑或等 AI 跑完；「稍后」收起之后这一次不再出现，页面重开还会再判一次。
 * 与「有新版本」（`UpdateNoticeDialog`，升级通道）是两件事：这里说的是**这一页**落后于本机的后端。
 */
export function BuildMismatchNotice() {
  const { t } = useTranslation(['workspace', 'common'])
  const [dismissed, setDismissed] = useState(false)
  if (dismissed) return null
  return (
    <Banner
      tone="info"
      icon={RefreshCw}
      data-build-mismatch=""
      title={t('workspace:update.banner')}
      action={
        <>
          <Button variant="secondary" size="sm" className="shrink-0" onClick={() => location.reload()}>
            {t('common:actions.refresh')}
          </Button>
          <Button size="sm" className="shrink-0 text-ink-3" onClick={() => setDismissed(true)}>
            {t('workspace:update.later')}
          </Button>
        </>
      }
    />
  )
}
