import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useReviewStore } from '@/store/reviewStore'
import { Button } from '../ui/Button'
import { ICON_SIZE } from '../ui/Icon'
import { Upload } from '../ui/icons'
import { Tip } from '../ui/Tooltip'

/**
 * 顶栏「分享审阅」（ADR 0187 §八）：导出按钮左边的次级胶囊。**开关只看引擎的 `capability.enabled`**
 * （`TAVOTTO_NO_REVIEW_SHARE=1` 或端点不合规 = 完全不渲染）；窄于 900 只留图标。有未读意见时右上角一个小圆点。
 * 点击只是打开弹窗，不联网。
 */
export function ReviewButton() {
  useTranslation('dialogs')
  const enabled = useReviewStore((s) => s.enabled)
  const hasTarget = useReviewStore((s) => s.target !== null)
  const unread = useReviewStore((s) => s.unread.total)
  if (!enabled) return null
  const label = translate('review.button', { ns: 'dialogs' })
  return (
    <Tip label={label}>
      <Button
        variant={hasTarget ? 'secondary' : 'ghost'}
        size="md"
        disabled={!hasTarget}
        data-review-button
        data-review-unread={unread > 0 ? unread : undefined}
        aria-label={unread > 0 ? translate('review.buttonUnread', { ns: 'dialogs' }) : label}
        onClick={() => useReviewStore.getState().openDialog()}
        className="relative shrink-0"
      >
        <Upload size={ICON_SIZE.sm} aria-hidden />
        <span className="max-[899px]:sr-only">{label}</span>
        {unread > 0 && <span aria-hidden data-review-dot className="absolute right-1 top-1 size-1.5 rounded-full bg-accent" />}
      </Button>
    </Tip>
  )
}
