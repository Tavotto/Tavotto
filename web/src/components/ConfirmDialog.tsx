import { useTranslation } from 'react-i18next'
import { useFormatMessage } from '@/i18n/react'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

/**
 * 全局确认框。挂在 App 上，由 `askConfirm()` 驱动——这样「需要用户点头」的
 * 流程不必各自维护弹窗状态，也不用原生 confirm（样式不一致、还会卡住主线程）。
 * 必须做出选择，所以 blockDismiss：点外面和 Esc 都不算回答。
 */
export function ConfirmDialog() {
  const { t } = useTranslation()
  const fmt = useFormatMessage()
  const req = useUiStore((s) => s.confirm)
  const answer = (ok: boolean) => {
    useUiStore.getState().setConfirm(null)
    req?.resolve(ok)
  }

  return (
    <Dialog
      open={!!req}
      onOpenChange={() => answer(false)}
      title={fmt(req?.title)}
      size="sm"
      blockDismiss
      footer={
        <>
          <Button variant="outline" size="md" onClick={() => answer(false)}>
            {req?.cancelLabel ? fmt(req.cancelLabel) : t('actions.cancel')}
          </Button>
          <Button
            variant={req?.danger ? 'danger' : 'primary'}
            size="md"
            onClick={() => answer(true)}
          >
            {req?.confirmLabel ? fmt(req.confirmLabel) : t('actions.continue')}
          </Button>
        </>
      }
    >
      <p className="text-xs leading-relaxed text-ink-2">{fmt(req?.body)}</p>
    </Dialog>
  )
}
