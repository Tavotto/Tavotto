import { useDiagSendStore } from '@/store/diagSendStore'
import { DiagnosticsSendDialog } from './settings/DiagnosticsSendDialog'

/**
 * 「发送问题反馈」对话框的唯一挂载点（App 里一处）。设置页的入口行与故障卡的入口都只是
 * `useDiagSendStore.setOpen(true)`——**打开 = 本机备包，不发送**；功能关着（`capability === null`）时什么都不渲染。
 */
export function DiagnosticsSendHost() {
  const capability = useDiagSendStore((s) => s.capability)
  const open = useDiagSendStore((s) => s.open)
  const setOpen = useDiagSendStore((s) => s.setOpen)
  if (!capability) return null
  return <DiagnosticsSendDialog open={open} onOpenChange={setOpen} capability={capability} />
}
