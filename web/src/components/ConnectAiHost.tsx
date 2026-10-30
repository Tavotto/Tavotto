import { useEffect } from 'react'
import { useAiStore } from '@/store/aiStore'
import { useConnectAiStore } from '@/store/connectAiStore'
import { CompatibleEndpoint } from './settings/connectAi/CompatibleEndpoint'
import { refreshCaps, useCapsWatch } from './settings/connectAi/useCapsWatch'
import { ConnectAiDialog } from './settings/connectAi/ConnectAiDialog'

/**
 * 「连接一个 AI」弹窗的唯一挂载点（App 里一处）。所有入口（修复、助手面板空态、模型选择器底部）
 * 都只是 `useConnectAiStore.openDialog()`。打开时若还没探测过 capabilities，先探一次（厂商目录来自它）。
 */
export function ConnectAiHost() {
  const open = useConnectAiStore((s) => s.open)
  const reason = useConnectAiStore((s) => s.reason)
  const onConnected = useConnectAiStore((s) => s.onConnected)
  const name = useConnectAiStore((s) => s.name)
  const close = useConnectAiStore((s) => s.close)
  const editing = useConnectAiStore((s) => s.editing)
  const closeEditor = useConnectAiStore((s) => s.closeEditor)
  const editingEndpoint = useAiStore((s) => (editing ? s.caps?.endpoints?.find((e) => e.id === editing) : undefined))
  useEffect(() => {
    // 打开时强刷一次：用户可能刚在终端里装好 / 登录好了；没探测过也走这一条
    if (open) void refreshCaps()
  }, [open])
  useCapsWatch(open)
  if (editingEndpoint) return <CompatibleEndpoint existing={editingEndpoint} onClose={closeEditor} onSaved={closeEditor} />
  if (!open) return null
  return <ConnectAiDialog open reason={reason} name={name ?? undefined} onClose={close} onConnected={() => onConnected?.()} />
}
