import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { backendErrorText, disconnectAi, type AiConnection, type AiVendor, type AiVendorMethod } from '@/lib/api'
import { msg } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { askConfirm } from '@/store/uiStore'
import { Button } from '../../ui/Button'
import { Select } from '../../ui/Select'
import { ag, cx } from './copy'
import { VendorKeyForm } from './VendorKeyForm'

/**
 * 已连接的 Key 类连接的管理区（设置页里点开那一行）：默认型号、换 Key、断开。
 * 默认型号只是本机的记忆选择（`aiStore.models[连接 id]`，与模型选择器同一份）；断开先确认——Key 删了找不回。
 */
export function ConnectionManage({
  vendor,
  connection,
  method,
}: {
  vendor: AiVendor
  connection: AiConnection
  /** 多方式厂商里这条连接对应的方式（决定换 Key 时的目录行 / 获取链接） */
  method?: AiVendorMethod
}) {
  useTranslation('dialogs')
  const model = useAiStore((s) => s.models[connection.id]) ?? connection.default_model ?? ''
  const [changing, setChanging] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const disconnect = async () => {
    const ok = await askConfirm({
      title: msg('settings.connectAi.disconnectTitle', { name: connection.label }, 'dialogs'),
      body: msg('settings.connectAi.disconnectBody', undefined, 'dialogs'),
      confirmLabel: msg('settings.connectAi.disconnect', undefined, 'dialogs'),
      danger: true,
    })
    if (!ok) return
    try {
      await disconnectAi(connection.id)
      await useAiStore.getState().loadCaps(true)
    } catch (e) {
      setError(backendErrorText(e))
    }
  }

  return (
    <div data-connect-manage={connection.id} className="flex flex-col gap-2">
      {connection.models.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="shrink-0 text-xs text-ink-2">{cx('defaultModel')}</span>
          <Select
            className="min-w-0 flex-1"
            ariaLabel={cx('defaultModel')}
            value={model}
            onChange={(m) => useAiStore.getState().setModel(connection.id, m)}
            options={connection.models.map((m) => ({
              value: m,
              label: connection.model_details?.find((d) => d.id === m)?.display_name || m,
            }))}
          />
        </div>
      )}
      {changing ? (
        <VendorKeyForm vendor={vendor} method={method} connectionId={connection.id} submitLabel={cx('saveKey')} onConnected={() => setChanging(false)} />
      ) : (
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" data-connect-change-key onClick={() => setChanging(true)}>
            {cx('changeKey')}
          </Button>
          <Button variant="danger-tinted" size="sm" data-connect-disconnect onClick={() => void disconnect()}>
            {cx('disconnect')}
          </Button>
        </div>
      )}
      {connection.insecure_lan && (
        <p data-connect-insecure className="type-caption">
          {ag('endpoint.insecureLan')}
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  )
}
