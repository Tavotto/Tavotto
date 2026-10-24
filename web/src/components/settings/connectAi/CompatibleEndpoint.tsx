import { saveAiEndpoint } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { EndpointDialog } from '../EndpointDialog'

/**
 * 「其他兼容接口…」：直接走现有 `EndpointDialog` 的手动模式（没有预设可选 = 直接是地址 / 密钥 / 型号表单）。
 * 保存走旧的 endpoints 接口（约定里它保留到前端切换完成），存好重读 capabilities 再通知调用方。
 */
export function CompatibleEndpoint({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  return (
    <EndpointDialog
      agent="codex"
      agentLabel=""
      wireApi
      existing={null}
      presets={[]}
      onClose={onClose}
      onSave={(rec) => {
        void (async () => {
          try {
            await saveAiEndpoint(rec)
            await useAiStore.getState().loadCaps(true)
          } catch {
            return
          }
          onSaved()
        })()
      }}
    />
  )
}
