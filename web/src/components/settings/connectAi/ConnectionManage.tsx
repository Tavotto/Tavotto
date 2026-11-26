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

/** 「退出登录」：退出这个 ChatGPT 账号并撤销授权（先确认）；远端撤销没确认时如实说一句（本机令牌照样清掉） */
export function SiwcSignOut({
  connection,
  onRevokeUnconfirmed,
}: {
  connection: AiConnection
  /** 远端撤销没确认：退出后这一行会换成未连接的样子、本组件随即卸载，提示由还活着的上层显示 */
  onRevokeUnconfirmed?: () => void
}) {
  useTranslation('dialogs')
  const [error, setError] = useState<string | null>(null)
  const signOut = async () => {
    const ok = await askConfirm({
      title: msg('settings.connectAi.siwc.signOutTitle', undefined, 'dialogs'),
      body: msg('settings.connectAi.siwc.disconnectBody', undefined, 'dialogs'),
      confirmLabel: msg('settings.connectAi.siwc.signOut', undefined, 'dialogs'),
      danger: true,
    })
    if (!ok) return
    try {
      const r = await disconnectAi(connection.id)
      if (r.revoked === false) onRevokeUnconfirmed?.()
      await useAiStore.getState().loadCaps(true)
    } catch (e) {
      setError(backendErrorText(e))
    }
  }
  return (
    <span className="flex shrink-0 flex-col items-end gap-1">
      {/* 次一级的灰字，负右边距让字的右缘与上下的箭头 / 下拉框对齐（agy 审稿） */}
      <Button variant="ghost" size="sm" className="-mr-2 text-ink-2" data-connect-disconnect onClick={() => void signOut()}>
        {cx('siwc.signOut')}
      </Button>
      {error && (
        <span role="alert" className="text-xs text-danger">
          {error}
        </span>
      )}
    </span>
  )
}
