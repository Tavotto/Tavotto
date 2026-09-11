import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

/**
 * 构建版本不一致时的提示。**弹窗而不是横幅**（2026-09-11 用户反馈）：横幅一直
 * 挂在画布上方，用户当它是界面的一部分而不是一条要处理的消息。
 *
 * 只提示不自动刷新——用户可能正在图内编辑或等 AI 跑完；「稍后」关掉之后这一次
 * 不再弹，下次页面重开还会再判一次。
 */
export function UpdateBanner() {
  const { t } = useTranslation(['workspace', 'common'])
  const [dismissed, setDismissed] = useState(false)
  return (
    <Dialog
      open={!dismissed}
      onOpenChange={(v) => !v && setDismissed(true)}
      title={t('workspace:update.title')}
      size="sm"
      footer={
        <>
          <Button variant="outline" size="md" onClick={() => setDismissed(true)}>
            {t('workspace:update.later')}
          </Button>
          <Button variant="primary" size="md" onClick={() => location.reload()}>
            {t('common:actions.refresh')}
          </Button>
        </>
      }
    >
      <p className="text-xs leading-relaxed text-ink-2">{t('workspace:update.banner')}</p>
    </Dialog>
  )
}
