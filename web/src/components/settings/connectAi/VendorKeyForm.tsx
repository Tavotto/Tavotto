import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { connectAi, type AiConnection, type AiVendor, type AiVendorMethod } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { Button } from '../../ui/Button'
import { TextInput } from '../../ui/Input'
import { ag, cx } from './copy'
import { vendorName } from './vendors'

/**
 * 填 Key 并连接（连接弹窗与设置页**共用这一份**）：一个输入框 +「去 X 获取 ↗」+「连接」。
 * 提交 = `POST /api/ai/connections`，成功后重读 capabilities，再把新连接交给 `onConnected`。
 * 保存了但后端试取型号失败（`state: 'error'`）不算连好：留在这里说一句，连接已存下，改 Key 再试即可。
 * `submitLabel` 让「换 Key」复用同一张表单。
 */
export function VendorKeyForm({
  vendor,
  method,
  submitLabel,
  connectionId,
  onConnected,
}: {
  vendor: AiVendor
  /** 厂商有多种方式时，当前选的这一种（决定目录行 / 获取链接 / 提示） */
  method?: AiVendorMethod
  /** 给了 = 换 Key（带已有连接 id，不先删后建） */
  connectionId?: string
  submitLabel?: string
  onConnected: (c: AiConnection) => void
}) {
  useTranslation('dialogs')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (!key.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const res = await connectAi({ ...(connectionId ? { id: connectionId } : {}), vendor: method?.catalog_id ?? vendor.id, api_key: key.trim() })
      if (!res.ok || !res.connection) {
        setError(cx('failed'))
        return
      }
      await useAiStore.getState().loadCaps(true)
      if (res.connection.state !== 'ready') {
        setError(cx('failed'))
        return
      }
      setKey('')
      onConnected(res.connection)
    } catch {
      setError(cx('failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      data-connect-key-form={vendor.id}
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <div className="flex items-center gap-2">
        <TextInput
          type="password"
          autoComplete="off"
          spellCheck={false}
          data-connect-key-input
          aria-label={cx('keyLabel')}
          placeholder={cx('keyPlaceholder')}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          className="min-w-0 flex-1"
        />
        <Button type="submit" variant="primary" size="sm" data-connect-key-submit loading={busy} disabled={!key.trim()}>
          {submitLabel ?? cx('connect')}
        </Button>
      </div>
      {(method?.key_url ?? vendor.key_url) && (
        <a
          data-connect-key-link
          href={method?.key_url ?? vendor.key_url}
          target="_blank"
          rel="noreferrer"
          className="focus-visible:focus-ring w-fit rounded-sm text-xs text-ink-2 underline-offset-2 outline-none hover:text-ink hover:underline"
        >
          {cx('getKey', { name: vendorName(vendor) })}
        </a>
      )}
      {(method?.key_hint_key ?? vendor.key_hint_key) && (
        <p data-connect-key-hint className="type-caption">
          {ag((method?.key_hint_key ?? vendor.key_hint_key)!)}
        </p>
      )}
      {error && (
        <p role="alert" data-connect-error className="text-xs text-danger">
          {error}
        </p>
      )}
    </form>
  )
}
