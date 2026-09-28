import { useTranslation } from 'react-i18next'
import { PRODUCT_NAME } from '@/lib/brand'
import { useUiStore } from '@/store/uiStore'
import { EngineEnvironmentCard } from './EngineEnvironmentCard'
import { Dialog } from './ui/Dialog'

/**
 * 「渲染环境」对话框：把 `EngineEnvironmentCard`（状态、「使用其他 Python 环境…」、
 * 运行目录）原样装进一个就地弹出的窗口。
 *
 * 为什么不深链设置页：卡片在设置里住在「诊断」页，环境正常时还折叠在技术详情里——
 * 脚本跑失败时点「选择渲染环境」被带进设置、落在一页看不到任何环境内容的地方
 * （此前还指向早已搬走的「关于与隐私」），用户只会更懵。卡片只有一份实现，这里与
 * 设置页、图内元素面板都是它的消费者，不另写第二份环境界面。
 *
 * 开关只有 `uiStore.engineEnvOpen`。
 */
export function EngineEnvironmentDialog() {
  const { t } = useTranslation('dialogs')
  const open = useUiStore((s) => s.engineEnvOpen)
  const setOpen = useUiStore((s) => s.setEngineEnvOpen)
  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={t('engineEnv.title')}
      description={t('engineEnv.description', { product: PRODUCT_NAME })}
      size="lg"
      anchor="engine-environment"
    >
      {/* Radix 只在打开时挂正文：卡片挂上时若还没有环境状态会自己取一次 */}
      <EngineEnvironmentCard />
    </Dialog>
  )
}
