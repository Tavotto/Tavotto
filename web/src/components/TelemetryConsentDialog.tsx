import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { PRIVACY_DOC_URL } from '@/lib/brand'
import { useTelemetryStore } from '@/store/telemetryStore'
import { Button } from './ui/Button'
import { Card } from './ui/Card'
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
 *   ③ 两个按钮**视觉权重相同**（同一 secondary、同为 lg），拒绝不比同意难点。深色主按钮
 *      留给「导出」那类真正的主动作。
 */
export function TelemetryConsentDialog() {
  useTranslation('dialogs')
  const askOpen = useTelemetryStore((s) => s.askOpen)
  const choose = useTelemetryStore((s) => s.choose)

  return (
    <Dialog
      // 常驻挂载（Dialog 的常驻写法）：作答后有退场动画，不是瞬间消失
      open={askOpen}
      // 必须表态的询问：两颗同权按钮是仅有的出口。之前只给了空的 onOpenChange，
      // 右上角 × 照画、Esc 照收，点了却什么都不发生（2026-10-07 设计审计 §10.2 P0）。
      // blockDismiss 把 × 去掉、Esc / 点外面吞掉——没有「看起来能关其实关不掉」的控件。
      onOpenChange={() => {}}
      blockDismiss
      title={tt('title')}
      description={tt('intro')}
      size="lg"
      anchor="telemetry-consent"
      footer={{
        // 两颗同权（同一 variant、同一 lg 档）：拒绝不比同意难点。不占 primary 的近黑——
        // 这里没有「推荐的那个答案」
        secondary: (
          <Button data-telemetry-decline variant="secondary" size="lg" onClick={() => void choose('disabled', 'first_run')}>
            {tt('decline')}
          </Button>
        ),
        primary: (
          <Button data-telemetry-allow variant="secondary" size="lg" onClick={() => void choose('enabled', 'first_run')}>
            {tt('allow')}
          </Button>
        ),
      }}
    >
      <div className="flex flex-col gap-3">
        {/* 发什么 / 绝不发什么：两张并排的浅底卡，一眼对照（2026-10-07 设计审计 §10.2） */}
        <div className="grid grid-cols-2 gap-2">
          <Card appearance="subtle" data-telemetry-sends>
            <h3 className="type-section mb-1.5">{tt('sendsTitle')}</h3>
            <ul className="flex list-outside list-disc flex-col gap-1 pl-4 text-ink-2 marker:text-ink-3">
              <li>{tt('sendsVersion')}</li>
              <li>{tt('sendsPlatform')}</li>
              <li>{tt('sendsFeatures')}</li>
              <li>{tt('sendsOutcome')}</li>
            </ul>
          </Card>
          <Card appearance="subtle" data-telemetry-never>
            <h3 className="type-section mb-1.5">{tt('neverTitle')}</h3>
            <ul className="flex list-outside list-disc flex-col gap-1 pl-4 text-ink-2 marker:text-ink-3">
              <li>{tt('neverFigures')}</li>
              <li>{tt('neverScripts')}</li>
              <li>{tt('neverPaths')}</li>
              <li>{tt('neverData')}</li>
              <li>{tt('neverPrompts')}</li>
            </ul>
          </Card>
        </div>
        <p className="text-ink-2">
          {tt('later')}{' '}
          <a
            href={PRIVACY_DOC_URL}
            target="_blank"
            rel="noreferrer"
            className="text-ink underline underline-offset-2 hover:text-ink-2"
          >
            {tt('policy')}
          </a>
        </p>
      </div>
    </Dialog>
  )
}
