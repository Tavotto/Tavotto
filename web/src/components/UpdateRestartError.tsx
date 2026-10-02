import { useTranslation } from 'react-i18next'
import { InlineWarning } from './settings/SettingRow'

/** Both installed-update surfaces retain the same restart recovery instruction. */
export function UpdateRestartError({ detail }: { detail: string | null }) {
  const { t } = useTranslation('errors')
  return (
    <div data-update-relaunch-error className="flex flex-col gap-1">
      <InlineWarning tone="danger">{t('update.relaunchFailed')}</InlineWarning>
      {detail && (
        <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs text-ink-3">
          {detail}
        </pre>
      )}
    </div>
  )
}
