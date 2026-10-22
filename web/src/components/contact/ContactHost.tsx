import { useContactStore } from '@/store/contactStore'
import { ContactDialog } from './ContactDialog'

/**
 * 「联系作者」对话框的唯一挂载点（App 的两个分支各一处，互斥）。入口按钮只是 `contactStore.setOpen(true)`；
 * 功能关着（`capability === null`）时什么都不渲染。
 */
export function ContactHost() {
  const enabled = useContactStore((s) => s.capability !== null)
  if (!enabled) return null
  return <ContactDialog />
}
