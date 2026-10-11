import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { isRepairRunning, useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { t as translate } from '@/i18n'
import { Button } from './ui/Button'
import { Details, Summary } from './ui/Details'
import { Dialog } from './ui/Dialog'
import { FieldGroup } from './ui/FormSection'
import { TextInput } from './ui/Input'
import { Notice } from './ui/Notice'
import { jointProgressText, repairShortMessage } from './DependencyRepairCard'
import { DistFailureList, OriginEntryList, UserSpecifiedBadge } from './OriginDetails'
import { RepairProgressLine } from './RepairProgressLine'
import { STATE_TEXT } from './DependencyPrepareDialog'

/**
 * 「确认包名」（Import Origin PR6，维护者裁决 C1：预填 + 用户确认）。
 *
 * 脚本里有 import，Tavotto 既没有可信的安装名、也不会去猜，但后端给了一个**预填**（`origins[].suggested_distribution`，
 * 只是输入框里的初始值，不是可信来源）：用户确认（可改，可带版本约束）之后，作为 `user_specified` 一次提交。
 *
 * 默认可见区只有：固定标题 + **一句话** + 每个带预填的 import 一行输入（名字 + 输入框，不是句子）+ 折叠的「详情」+ 一个主按钮
 * （和「稍后」）。两步，都在这一个框里：
 *
 * 1. **包名**：输入框只给带预填的条目（别的来源未定的 import 后端会以 `dependency_install_not_allowed` 拒收，所以只在详情里解释、
 *    不给输入框）。主按钮「核对包名」= `createJointDependencyPlan({ script, user_specified })`，不装。
 * 2. **确认页**：绑定回来的计划。那一句话**必须**说清这些包是「用户指定、未经可信来源核实」（读 `plan.impact.user_specified[].trust`，
 *    读不到披露就不让继续）；主按钮「确认并安装」把**这份计划自己的** `impact_digest` 原样交给 `/prepare`——用户看到的就是它。
 *
 * 一次授权：所有输入框一份计划一个摘要，不逐包弹窗。目标环境由后端默认（受管环境，或此刻选中的项目 venv），这里不另开选项。
 */
//: 文案键写成字面量（前缀 `prep.origin.`）
const ot = (key: string, v?: Record<string, unknown>) => translate(`prep.origin.${key}`, { ns: 'workspace', ...v })
const later = () => translate('prep.btn.later', { ns: 'workspace' })

//: 后端对「用户指定」的两种拒绝给人话（语法不对 / 这个 import 不能由用户指定）；别的码走修复卡那一套短句
const ERROR_TEXT: Record<string, string> = {
  package_requirement_invalid: 'error.package_requirement_invalid',
  dependency_install_not_allowed: 'error.dependency_install_not_allowed',
}

export function ImportOriginDialog() {
  useTranslation(['workspace', 'errors'])
  const current = useEnvStore((s) => s.originConfirmation)
  const draft = useDepRepairStore((s) => s.originDraft)
  const plan = useDepRepairStore((s) => s.originPlan)
  const jointPlan = useDepRepairStore((s) => s.jointPlan)
  const progress = useDepRepairStore((s) => s.progress)
  const busy = useDepRepairStore((s) => s.busy)
  const errorCode = useDepRepairStore((s) => s.errorCode)
  const setValue = useDepRepairStore((s) => s.setOriginValue)
  const bind = useDepRepairStore((s) => s.bindOrigin)
  const back = useDepRepairStore((s) => s.backToOriginDraft)
  const confirm = useDepRepairStore((s) => s.confirmOrigin)
  const cancel = useDepRepairStore((s) => s.cancelPreparation)
  const close = useDepRepairStore((s) => s.closeOrigin)
  // 常驻挂载（Dialog 的常驻写法）：关的那 90ms 里载荷已经清掉，正文按最后一份画
  const last = useRef(current)
  if (current) last.current = current
  const offer = last.current
  if (!offer) return null

  const running = isRepairRunning(progress) && progress?.flow === 'joint' && progress.plan_id === jointPlan?.plan_id
  const names = draft?.names ?? []
  const step: 'names' | 'confirm' = plan ? 'confirm' : 'names'
  // 确认页读后端的披露：用户指定的包、`trust` 恒为 `user_specified_unverified`。读不到 = 没法告诉用户他在授权什么，不让继续
  const disclosed = plan?.impact?.user_specified ?? []
  const unverified = disclosed.filter((d) => d.trust === 'user_specified_unverified')
  const filled = names.some((n) => (draft?.values[n] ?? '').trim() !== '')
  const code = errorCode
  const failing = !running && !!code
  const distReasons =
    failing && progress?.state === 'failed' && progress.code === code && progress.dist_reasons ? progress.dist_reasons : []
  const failure =
    distReasons.length > 0 && code === 'dependency_import_still_failed'
      ? ot('distFailed')
      : ERROR_TEXT[code]
        ? ot(ERROR_TEXT[code])
        : repairShortMessage(code)

  const sentence =
    step === 'names'
      ? names.length > 0
        ? ot('dialog.names')
        : ot('dialog.nothing')
      : unverified.length > 0
        ? ot('dialog.confirmUnverified', { count: unverified.length })
        : ot('dialog.noDisclosure')

  const primary = running ? (
    <Button variant="primary" size="lg" data-origin-running loading disabled>
      {ot('btn.confirmInstall')}
    </Button>
  ) : step === 'names' ? (
    names.length > 0 ? (
      <Button
        variant="primary"
        size="lg"
        data-origin-primary="review"
        disabled={busy || !filled}
        onClick={() => void bind()}
      >
        {ot('btn.review')}
      </Button>
    ) : undefined
  ) : (
    <Button
      variant="primary"
      size="lg"
      data-origin-primary="confirm"
      disabled={busy || unverified.length === 0}
      onClick={() => void confirm()}
    >
      {ot('btn.confirmInstall')}
    </Button>
  )

  return (
    <Dialog
      open={!!current}
      onOpenChange={(v) => {
        if (!v && !busy && !running) close()
      }}
      onEscape={busy || running ? undefined : close}
      title={ot('dialog.heading')}
      size="md"
      busy={busy || running}
      anchor="import-origin"
      footer={
        running
          ? {
              secondary: (
                <Button variant="secondary" size="lg" data-origin-cancel onClick={() => void cancel()}>
                  {translate('engine.dependencyPrepareCancel', { ns: 'errors' })}
                </Button>
              ),
              primary,
            }
          : {
              start:
                step === 'confirm' ? (
                  <Button variant="ghost" size="lg" data-origin-back disabled={busy} onClick={back}>
                    {ot('btn.back')}
                  </Button>
                ) : undefined,
              secondary: (
                <Button variant="secondary" size="lg" data-origin-later disabled={busy} onClick={close}>
                  {later()}
                </Button>
              ),
              primary,
            }
      }
    >
      <div className="flex flex-col gap-3" data-origin-step={step} data-origin-view="needs_confirm">
        {!running && !failing && (
          <p className="text-ink" data-origin-sentence>
            {sentence}
          </p>
        )}
        {!running && step === 'names' && names.length > 0 && (
          <div className="flex flex-col gap-1.5" data-origin-inputs>
            {names.map((name) => (
              <label key={name} className="flex items-center gap-2" data-origin-row={name}>
                <span className="w-32 shrink-0 truncate font-mono text-sm text-ink-2">{name}</span>
                <TextInput
                  value={draft?.values[name] ?? ''}
                  disabled={busy}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder={ot('dialog.placeholder')}
                  data-origin-input={name}
                  onChange={(e) => setValue(name, e.target.value)}
                />
              </label>
            ))}
          </div>
        )}
        {!running && step === 'confirm' && (
          <div className="flex flex-col gap-1.5" data-origin-inputs>
            {disclosed.map((d) => (
              <label key={d.import_name} className="flex items-center gap-2" data-origin-row={d.import_name}>
                <span className="w-32 shrink-0 truncate font-mono text-sm text-ink-2">{d.import_name}</span>
                <TextInput readOnly value={d.requirement} aria-readonly data-origin-confirmed={d.import_name} />
              </label>
            ))}
          </div>
        )}
        {running && progress && (
          <div data-dependency-state={progress.state}>
            <RepairProgressLine
              progress={progress}
              text={jointProgressText(progress, (state) =>
                translate(STATE_TEXT[state] ?? 'engine.dependencyPrepareState_preparing', { ns: 'errors' }),
              )}
            />
          </div>
        )}
        {!running && (
          <Details className="text-sm" data-origin-details>
            <Summary className="text-ink-2 hover:text-ink">{translate('engine.repairDetails', { ns: 'errors' })}</Summary>
            <FieldGroup className="mt-2">
              <div className="flex flex-col gap-1.5 text-sm text-ink-2">
                <p data-origin-trust-note>{ot('detail.trustNote')}</p>
                {step === 'confirm' &&
                  disclosed.map((d) => (
                    <p key={d.import_name} className="flex flex-wrap items-center gap-1.5" data-origin-disclosed={d.import_name}>
                      <span className="font-mono">{d.requirement}</span>
                      {d.trust === 'user_specified_unverified' && <UserSpecifiedBadge />}
                    </p>
                  ))}
                {step === 'confirm' && plan && (
                  <p data-origin-place>
                    {translate('prep.detail.installTo', { ns: 'workspace' })} ·{' '}
                    {plan.modifies_user_environment
                      ? translate('prep.install.toUserLong', { ns: 'workspace' })
                      : translate('prep.install.managedPlace', { ns: 'workspace' })}
                  </p>
                )}
                <OriginEntryList origins={step === 'names' ? offer.plan.origins ?? [] : plan?.joint?.origins ?? []} title={ot('detail.listIntro')} />
                {distReasons.length > 0 && <DistFailureList reasons={distReasons} />}
              </div>
            </FieldGroup>
          </Details>
        )}
        {failing && (
          <Notice tone="danger" data-origin-error={code}>
            {failure}
          </Notice>
        )}
      </div>
    </Dialog>
  )
}
