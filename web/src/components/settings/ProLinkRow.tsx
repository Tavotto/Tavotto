import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { formatDate } from '@/i18n/format'
import { PRO_NAME } from '@/lib/brand'
import { licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { FieldGroup, FormSection } from '../ui/FormSection'
import { SettingRow } from './SettingRow'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.license.${key}`, { ns: 'dialogs', pro: PRO_NAME, ...(values ?? {}) })

/**
 * 设置「关于与更新」里关于 Pro 的**一行链接**（2026-11-24：授权的全部内容搬到 Pro 页，这里只留入口）：
 * 名字 + 一句现状 + 「打开 Pro」。入口没开（`PRODUCTION_LIVE = False`）时整块不渲染。点它先收设置、再开 Pro 页。
 */
export function ProLinkRow() {
  useTranslation('dialogs')
  const status = useLicenseStore((s) => s.status)
  const load = useLicenseStore((s) => s.load)
  useEffect(() => {
    if (status === null) void load()
  }, [status, load])
  if (!licenseVisible(status)) return null
  const line =
    status.state === 'active' && status.exp
      ? st('until', { date: formatDate(status.exp * 1000) })
      : status.state === 'expired' && status.exp
        ? st('state.expired', { date: formatDate(status.exp * 1000) })
        : status.state === 'revoked'
          ? st('state.revoked')
          : status.state === 'lapsed'
            ? st('state.lapsed')
            : st('none')
  return (
    <FormSection data-settings-anchor="about.license" data-license-link>
      <FieldGroup>
        <SettingRow label={PRO_NAME} description={<span data-license-line>{line}</span>} control="auto">
          <Button
            variant="secondary"
            onClick={() => {
              const ui = useUiStore.getState()
              ui.setSettingsOpen(false)
              ui.setProOpen(true)
            }}
            data-license-open
          >
            {st('open', { pro: PRO_NAME })}
          </Button>
        </SettingRow>
      </FieldGroup>
    </FormSection>
  )
}
