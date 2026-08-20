import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useTelemetryStore } from '@/store/telemetryStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

/** 本对话框的文案在 dialogs:telemetry.* 下 */
const tt = (key: string, values?: Record<string, unknown>) =>
  translate(`telemetry.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 首启的一次性询问：只在同意态还是 `unset` 时出现一次。
 *
 * 为什么需要它——只放在设置里的开关几乎收不到数据（绝大多数人不会去翻设置），
 * 而「默认打开、想关自己找」与本项目的本地优先承诺是冲突的。折中就是这个框：
 * **问一次，说清楚发什么、不发什么，两个选项一样好点**。
 *
 * 三条纪律，别改：
 *   ① 它出现之前一个事件都没发过（后端在 unset 时连 install_id 都不生成）；
 *   ② 「暂不」写的是 `disabled`，不是留在 unset —— 留着等于每次启动再问一遍，
 *      那是骚扰，不是征求同意；
 *   ③ 两个按钮**视觉权重相同**（都是 outline），拒绝不比同意难点。深色主按钮
 *      留给「导出」那类真正的主动作。
 */
export function TelemetryConsentDialog() {
  useTranslation('dialogs')
  const askOpen = useTelemetryStore((s) => s.askOpen)
  const choose = useTelemetryStore((s) => s.choose)
  if (!askOpen) return null

  return (
    <Dialog
      open
      // 随手关掉 = 还没表态，下次启动再问。真正的「不」要按「暂不」。
      onOpenChange={() => {}}
      title={tt('title')}
      description={tt('intro')}
      size="md"
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button variant="outline" onClick={() => void choose('disabled', 'first_run')}>
            {tt('decline')}
          </Button>
          <Button variant="outline" onClick={() => void choose('enabled', 'first_run')}>
            {tt('allow')}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-2.5 text-xs leading-relaxed">
        <section>
          <h3 className="mb-1 font-medium text-ink-2">{tt('sendsTitle')}</h3>
          <ul className="flex list-inside list-disc flex-col gap-0.5 text-ink-3">
            <li>{tt('sendsVersion')}</li>
            <li>{tt('sendsPlatform')}</li>
            <li>{tt('sendsFeatures')}</li>
            <li>{tt('sendsOutcome')}</li>
          </ul>
        </section>
        <section>
          <h3 className="mb-1 font-medium text-ink-2">{tt('neverTitle')}</h3>
          <ul className="flex list-inside list-disc flex-col gap-0.5 text-ink-3">
            <li>{tt('neverFigures')}</li>
            <li>{tt('neverScripts')}</li>
            <li>{tt('neverPaths')}</li>
            <li>{tt('neverData')}</li>
            <li>{tt('neverPrompts')}</li>
          </ul>
        </section>
        <p className="text-ink-3">
          {tt('later')}{' '}
          <a
            href="https://github.com/Tavotto/Tavotto/blob/main/docs/privacy.md"
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:underline"
          >
            {tt('policy')}
          </a>
        </p>
      </div>
    </Dialog>
  )
}
