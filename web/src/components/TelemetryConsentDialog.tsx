import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { PRIVACY_DOC_URL } from '@/lib/brand'
import { useTelemetryStore } from '@/store/telemetryStore'
import { ConsentArt, type ConsentArtId } from './telemetry/ConsentArt'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'

/** 本对话框的文案在 dialogs:telemetry.* 下 */
const tt = (key: string, values?: Record<string, unknown>) =>
  translate(`telemetry.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 三套卡片：**只换插画和第一句话**（`lead`），其余事实句、清单、按钮全部共用同一组 i18n 键。
 * 加第四套时别在这里夹带别的文案——测试钉着「三套事实键相同」。
 */
export const CONSENT_VARIANTS: Record<ConsentArtId, { lead: string }> = {
  A: { lead: 'leadA' },
  B: { lead: 'leadB' },
  C: { lead: 'leadC' },
}
const VARIANT_IDS = Object.keys(CONSENT_VARIANTS) as ConsentArtId[]

/** 随机挑一套；随机源可注入（测试传固定值）。 */
export function pickConsentVariant(random: () => number = Math.random): ConsentArtId {
  const i = Math.min(VARIANT_IDS.length - 1, Math.floor(random() * VARIANT_IDS.length))
  return VARIANT_IDS[i]
}

/**
 * 首启的一次性询问：只在同意态还是 `unset` 时出现一次（升版后重新询问同样用它）。
 *
 * 为什么需要它——只放在设置里的开关几乎收不到数据（绝大多数人不会去翻设置），
 * 而「默认打开、想关自己找」与本项目的本地优先承诺是冲突的。折中就是这个框：
 * **问一次，说清楚发什么、不发什么，两个选项一样好点**。
 *
 * 三条纪律，别改：
 *   ① 它出现之前一个事件都没发过（后端在 unset 时连 install_id 都不生成）；
 *   ② 「暂不」写的是 `disabled`，不是留在 unset —— 留着等于每次启动再问一遍，
 *      那是骚扰，不是征求同意；
 *   ③ 主次可分，但两个选项同样醒目、同样好点：同尺寸（lg）、同一行、都是真按钮；
 *      拒绝写 disabled 不再问。「同意」用 primary，「暂不」绝不做成文字链接、也不缩小。
 *
 * 每次弹出随机挑一套插画 + 第一句话；同一次打开里固定，不随重渲染变。
 */
export function TelemetryConsentDialog() {
  useTranslation('dialogs')
  const askOpen = useTelemetryStore((s) => s.askOpen)
  const choose = useTelemetryStore((s) => s.choose)
  const reconsent = useTelemetryStore((s) => s.settings?.needs_reconsent === true)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [variant, setVariant] = useState<ConsentArtId>(() => pickConsentVariant())
  const [wasOpen, setWasOpen] = useState(askOpen)
  // 每次从关到开重新抽一次、并把详情收回去；用「渲染期调整 state」而不是 effect，
  // 不会先画一帧旧的再跳。退场动画期间（askOpen 已为假）保持原样，不闪。
  if (askOpen !== wasOpen) {
    setWasOpen(askOpen)
    if (askOpen) {
      setVariant(pickConsentVariant())
      setDetailsOpen(false)
    }
  }

  return (
    <Dialog
      // 常驻挂载（Dialog 的常驻写法）：作答后有退场动画，不是瞬间消失
      open={askOpen}
      // 必须表态的询问：两颗按钮是仅有的出口。blockDismiss 把 × 去掉、Esc / 点外面吞掉——
      // 没有「看起来能关其实关不掉」的控件（2026-10-07 设计审计 §10.2 P0）。
      onOpenChange={() => {}}
      blockDismiss
      // split：标题 / 说明只给读屏，正文不带内边距——顶部插画要通到边
      chrome="split"
      title={tt('title')}
      description={tt(CONSENT_VARIANTS[variant].lead)}
      size="lg"
      anchor="telemetry-consent"
      footer={{
        // 主次可分、同尺寸同一行：「同意」是黑底白字的 primary；「暂不」是 secondary 再加一圈 border-strong
        // 描边——灰底胶囊在深色面板上偏淡，加描边才「同样醒目」（11-04 用户裁决）
        secondary: (
          <Button
            data-telemetry-decline
            variant="secondary"
            size="lg"
            className="inset-ring inset-ring-border-strong"
            onClick={() => void choose('disabled', 'first_run')}
          >
            {tt('decline')}
          </Button>
        ),
        primary: (
          <Button
            data-telemetry-allow
            variant="primary"
            size="lg"
            onClick={() => void choose('enabled', 'first_run')}
          >
            {tt(reconsent ? 'allowReconsent' : 'allow')}
          </Button>
        ),
      }}
    >
      <div className="flex min-h-0 flex-col" data-telemetry-variant={variant}>
        <ConsentArt id={variant} />
        <div className="flex min-h-0 flex-col gap-2.5 overflow-y-auto px-7 pb-2 pt-6">
          <h2 className="type-title">{tt('title')}</h2>
          <p data-telemetry-lead className="text-ink-2">{tt(CONSENT_VARIANTS[variant].lead)}</p>
          <p data-telemetry-fact className="text-ink-2">{tt('fact')}</p>
          <p className="type-meta text-ink-3">
            {tt('later')}
            {' · '}
            <button
              type="button"
              data-telemetry-details-toggle
              aria-expanded={detailsOpen}
              onClick={() => setDetailsOpen((v) => !v)}
              className="text-ink-3 underline underline-offset-2 hover:text-ink-2"
            >
              {tt(reconsent ? 'detailsReconsent' : 'details')}
            </button>
          </p>
          {detailsOpen && (
            <div data-telemetry-details className="flex flex-col gap-2.5 pb-1">
              {/* 重新同意（升版后）：「比上次多了什么」放在展开区最上面；首启不显示 */}
              {reconsent && <p data-telemetry-reconsent-delta className="text-ink-2">{tt('reconsentDelta')}</p>}
              {/* 发什么 / 绝不发什么：两行「标签 + 一句话」，细线隔开，不再用卡片和圆点清单（11-04 用户：简洁） */}
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 border-t border-border pt-3 type-meta">
                <dt className="text-ink-3">{tt('sendsTitle')}</dt>
                <dd data-telemetry-sends className="text-ink-2">
                  {listJoin([tt('sendsVersion'), tt('sendsPlatform'), tt('sendsFeatures'), tt('sendsOutcome'), tt('sendsErrorSummary')])}
                </dd>
                <dt className="text-ink-3">{tt('neverTitle')}</dt>
                <dd data-telemetry-never className="text-ink-2">
                  {listJoin([tt('neverFigures'), tt('neverScripts'), tt('neverPaths'), tt('neverData'), tt('neverPrompts')])}
                  {' · '}
                  <a
                    href={PRIVACY_DOC_URL}
                    target="_blank"
                    rel="noreferrer"
                    className="text-ink-3 underline underline-offset-2 hover:text-ink-2"
                  >
                    {tt('policy')}
                  </a>
                </dd>
              </dl>
            </div>
          )}
        </div>
      </div>
    </Dialog>
  )
}
