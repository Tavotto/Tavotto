import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { PRO_NAME } from '@/lib/brand'
import { licenseVisible, useLicenseStore } from '@/store/licenseStore'
import { useUiStore } from '@/store/uiStore'
import { Dialog } from '../ui/Dialog'
import { ProPage } from './ProPage'

/** Pro 页的外壳：中等宽的对话框，内容区自己滚；入口没开 / 没问过时不渲染（正式安装里没有任何痕迹） */
export function ProDialog() {
  useTranslation('dialogs')
  const open = useUiStore((s) => s.proOpen)
  const visible = useLicenseStore((s) => licenseVisible(s.status))
  if (!open || !visible) return null
  return (
    <Dialog
      open
      onOpenChange={(v) => useUiStore.getState().setProOpen(v)}
      title={translate('pro.title', { ns: 'dialogs', pro: PRO_NAME })}
      width={640}
    >
      <div className="py-1">
        <ProPage />
      </div>
    </Dialog>
  )
}
