import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useRevertDiffStore } from '@/store/revertDiffStore'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { DiffView } from './DiffView'

/** 常驻宿主：画「回滚冲突 → 查看差异」的对话框（里面是现成的 `DiffView`），挂在任务历史弹层外面 */
export function RevertDiffHost() {
  const { t } = useTranslation('ai')
  const target = useRevertDiffStore((s) => s.target)
  const close = useRevertDiffStore((s) => s.close)
  return (
    <Dialog
      open={!!target}
      onOpenChange={(v) => !v && close()}
      title={t('diff.title')}
      size="xl"
      footer={
        <Button variant="secondary" size="lg" onClick={close}>
          {translate('actions.close')}
        </Button>
      }
    >
      {target && <DiffView diff={target.diff} script={target.script} />}
    </Dialog>
  )
}
