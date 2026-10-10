import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useDiagSendStore } from '@/store/diagSendStore'
import { Button, type ButtonProps } from './ui/Button'

/**
 * 故障卡里的「发送问题反馈」入口（ADR 0118）。**开关与设置页同一个判据**：`diagSendStore.capability`
 * （引擎说「已开启」才非空）——关着时完全不渲染。点击只是打开对话框（本机备包），不发送；逐次确认在对话框里。
 *
 * 摆放规则（卡片一句话 + 一个主按钮）：卡片**没有**可执行的修复动作时它就是主按钮（`variant="primary"`）；
 * 已经有修复类主按钮时它住在卡片的折叠详情里（默认 `secondary` 小钮），不抢主按钮。
 */
export function SendReportButton({
  variant = 'secondary',
  size = 'sm',
}: {
  variant?: ButtonProps['variant']
  size?: ButtonProps['size']
}) {
  useTranslation('dialogs')
  const enabled = useDiagSendStore((s) => s.capability !== null)
  if (!enabled) return null
  return (
    <Button
      variant={variant}
      size={size}
      data-send-report
      onClick={() => useDiagSendStore.getState().setOpen(true)}
    >
      {translate('settings.diagnostics.send.cardButton', { ns: 'dialogs' })}
    </Button>
  )
}
