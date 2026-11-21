import { useTranslation } from 'react-i18next'
import { pickPackageFile } from '@/lib/packageImport'
import { openPackageWithRetry } from '@/store/packageActions'
import { useProjectStore } from '@/store/projectStore'
import { Button } from './ui/Button'
import { ICON_SIZE } from './ui/Icon'
import { Download } from './ui/icons'

export function ProjectPackageImportButton({ labelClassName }: { labelClassName?: string }) {
  const { t } = useTranslation('dialogs')
  const switching = useProjectStore((s) => s.switching)

  return (
    <Button
      size="md"
      className="text-ink-2"
      disabled={switching}
      data-install-project-package
      onClick={() => pickPackageFile((file) => void openPackageWithRetry(file, 0))}
    >
      <Download size={ICON_SIZE.md} aria-hidden />
      <span className={labelClassName}>{t('packageInstall.importEntry')}</span>
    </Button>
  )
}
