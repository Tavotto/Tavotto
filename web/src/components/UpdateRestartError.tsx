import { useTranslation } from 'react-i18next'
import { LogDetails } from './UpdateLogDetails'
import { Notice } from './ui/Notice'

/** Both installed-update surfaces retain the same restart recovery instruction. */
export function UpdateRestartError({ detail }: { detail: string | null }) {
  const { t } = useTranslation('errors')
  return (
    <div data-update-relaunch-error className="flex flex-col gap-1.5">
      <Notice tone="danger">{t('update.relaunchFailed')}</Notice>
      {detail && <LogDetails text={detail} />}
    </div>
  )
}
