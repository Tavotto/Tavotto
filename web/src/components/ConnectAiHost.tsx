import { useEffect } from 'react'
import { useAiStore } from '@/store/aiStore'
import { useConnectAiStore } from '@/store/connectAiStore'
import { ConnectAiDialog } from './settings/connectAi/ConnectAiDialog'

/**
 * 「连接一个 AI」弹窗的唯一挂载点（App 里一处）。所有入口（修复、助手面板空态、模型选择器底部）
 * 都只是 `useConnectAiStore.openDialog()`。打开时若还没探测过 capabilities，先探一次（厂商目录来自它）。
 */
export function ConnectAiHost() {
  const open = useConnectAiStore((s) => s.open)
  const reason = useConnectAiStore((s) => s.reason)
  const onConnected = useConnectAiStore((s) => s.onConnected)
  const close = useConnectAiStore((s) => s.close)
  useEffect(() => {
    if (open && !useAiStore.getState().caps) void useAiStore.getState().loadCaps().catch(() => undefined)
  }, [open])
  if (!open) return null
  return <ConnectAiDialog open reason={reason} onClose={close} onConnected={() => onConnected?.()} />
}
