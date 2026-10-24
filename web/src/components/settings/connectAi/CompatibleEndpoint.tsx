import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { backendErrorText, saveAiEndpoint } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { EndpointDialog } from '../EndpointDialog'
import { cx } from './copy'

/**
 * 「其他兼容接口…」：直接走现有 `EndpointDialog` 的手动模式（没有预设可选 = 直接是地址 / 密钥 / 型号表单）。
 * 保存走旧的 endpoints 接口（约定里它保留到前端切换完成），存好重读 capabilities 再通知调用方。
 *
 * 两种失败分开说：**保存失败**——表单里写一句原因，用户改了再存；**保存成功但刷新失败**——记住「已经存了」，
 * 换成「已保存，没能刷新」+「重试刷新」，重试只重读、绝不再发一次（无 id 的）新建。
 */
export function CompatibleEndpoint({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  useTranslation('dialogs')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const busy = useRef(false)

  const refresh = async (): Promise<boolean> => {
    try {
      await useAiStore.getState().loadCaps(true)
      return true
    } catch {
      return false
    }
  }

  const save = async (rec: Parameters<typeof saveAiEndpoint>[0]) => {
    if (busy.current || saved) return // 已经存过了：任何路径都不再发新建
    busy.current = true
    setSaveError(null)
    try {
      await saveAiEndpoint(rec)
    } catch (e) {
      setSaveError(backendErrorText(e))
      busy.current = false
      return
    }
    setSaved(true)
    busy.current = false
    if (await refresh()) onSaved()
  }

  if (saved) {
    return (
      <Dialog
        open
        onOpenChange={(v) => !v && onClose()}
        title={cx('other')}
        size="sm"
        footer={{
          secondary: (
            <Button variant="secondary" size="lg" onClick={onClose}>
              {cx('later')}
            </Button>
          ),
          primary: (
            <Button
              variant="primary"
              size="lg"
              data-endpoint-retry-refresh
              loading={refreshing}
              onClick={async () => {
                setRefreshing(true)
                const ok = await refresh()
                setRefreshing(false)
                if (ok) onSaved()
              }}
            >
              {cx('retryRefresh')}
            </Button>
          ),
        }}
      >
        <p data-endpoint-saved-notice className="text-base text-ink-2">
          {cx('savedRefreshFailed')}
        </p>
      </Dialog>
    )
  }

  return (
    <EndpointDialog
      agent="codex"
      agentLabel=""
      wireApi
      existing={null}
      presets={[]}
      saveError={saveError}
      onClose={onClose}
      onSave={(rec) => void save(rec)}
    />
  )
}
