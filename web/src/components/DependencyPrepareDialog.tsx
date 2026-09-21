import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { isRepairRunning, useDepRepairStore } from '@/store/depRepairStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { Radio } from './ui/Radio'

/**
 * 跑前的那一次授权（U04，ADR 0061 §六）：后端起第一个 worker 之前看一眼脚本开跑要的第三方包
 * 目标环境里缺不缺、缺的能不能一次装全——能就以 `dependency_preparation_required` 回来。这不是
 * 错误，是缺一次授权：整份联合计划（装什么 / 约束什么 / 装到哪 / 会不会改用户环境 / 认不出的
 * import）都在载荷里，这里只翻译、不裁决。
 *
 * 目标两档：Tavotto 自己的隔离环境（默认；可删可重建）/ 项目自己的 venv（只在它就是此刻选中的
 * 解释器时出现；会改用户环境，文案说清）。「准备并继续」= 绑定计划 + 执行 + 看进度，装完后端
 * 作废旧会话、前端重排失败的渲染；「稍后」只关框——同一张图再渲染一次会直接运行（后端只问一次），
 * 缺包会以 `missing_dependency` 回来，错误块里还能再开这个框。
 */
type Target = 'project_venv' | 'tavotto_managed'

//: 文案键写成字面量：i18n 的死键门禁按「源码里出现过这个串」判活
const TARGET_LABEL: Record<Target, string> = {
  tavotto_managed: 'engine.dependencyTarget_tavotto_managed',
  project_venv: 'engine.dependencyTarget_project_venv',
}
const TARGET_HINT: Record<Target, string> = {
  tavotto_managed: 'engine.dependencyTargetHint_tavotto_managed',
  project_venv: 'engine.dependencyTargetHint_project_venv',
}
const STATE_TEXT: Record<string, string> = {
  preparing: 'engine.dependencyPrepareState_preparing',
  creating_env: 'engine.dependencyPrepareState_creating_env',
  installing: 'engine.dependencyPrepareState_installing',
  verifying: 'engine.dependencyPrepareState_verifying',
}
const BLOCKED_TEXT: Record<string, string> = {
  dependency_declaration_unsupported: 'engine.dependencyBlocked_dependency_declaration_unsupported',
  dependency_conflict: 'engine.dependencyBlocked_dependency_conflict',
  dependency_hashes_incomplete: 'engine.dependencyBlocked_dependency_hashes_incomplete',
  dependency_target_unavailable: 'engine.dependencyBlocked_dependency_target_unavailable',
}

export function DependencyPrepareDialog() {
  const { t } = useTranslation('errors')
  const offer = useDepRepairStore((s) => s.preparation)
  const progress = useDepRepairStore((s) => s.progress)
  const busy = useDepRepairStore((s) => s.busy)
  const errorCode = useDepRepairStore((s) => s.errorCode)
  const errorText = useDepRepairStore((s) => s.errorText)
  const blocked = useDepRepairStore((s) => s.jointBlocked)
  const dismiss = useDepRepairStore((s) => s.dismissPreparation)
  const prepare = useDepRepairStore((s) => s.prepare)
  const cancel = useDepRepairStore((s) => s.cancelPreparation)
  const [target, setTarget] = useState<Target>('tavotto_managed')
  useEffect(() => {
    // 每一份新载荷从后端算出来的目标起步（项目 venv 是此刻选中的解释器时就是它）
    setTarget(offer?.target_kind ?? 'tavotto_managed')
  }, [offer])
  if (!offer) return null
  const en = (key: string, values?: Record<string, unknown>) => t(key, values)
  const plan = offer.plan
  const running = isRepairRunning(progress) && progress?.flow === 'joint'
  const failed = !!progress && progress.flow === 'joint' && (progress.state === 'failed' || progress.state === 'cancelled')
  const code = errorCode || (failed ? progress?.code || '' : '')
  const codeKey = code ? `engine.repairError.${code}` : ''
  const errorLine = code
    ? t(codeKey, { defaultValue: errorText || progress?.error || code })
    : errorText || progress?.error || ''
  const targets = offer.targets.filter((o) => o.kind !== 'system_interpreter')
  const chosen = targets.find((o) => o.kind === target)
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !busy && !running) dismiss()
      }}
      title={en('engine.dependencyPrepareTitle', { count: plan.requirements.length })}
      description={en('engine.dependencyPrepareBody', { script: offer.script })}
      size="sm"
      busy={busy || running}
      anchor="dependency-prepare"
      footer={
        running ? (
          <>
            <Button variant="secondary" size="md" onClick={() => void cancel()}>
              {en('engine.dependencyPrepareCancel')}
            </Button>
          </>
        ) : (
          <>
            <Button variant="secondary" size="md" disabled={busy} onClick={dismiss}>
              {en('engine.dependencyPrepareLater')}
            </Button>
            <Button
              variant="primary"
              size="md"
              disabled={busy || !chosen || chosen.available === false}
              onClick={() => void prepare(target)}
            >
              {failed || code ? en('engine.dependencyPrepareRetry') : en('engine.dependencyPrepareRun')}
            </Button>
          </>
        )
      }
    >
      {/* 要装的：项目声明的完整形态（extras / 版本），用户自己的名字，不翻译 */}
      <ul className="flex flex-col gap-0.5 font-mono text-xs text-ink-2" data-dependency-requirements>
        {plan.requirements.map((req) => (
          <li key={req}>{req}</li>
        ))}
      </ul>
      {plan.constraints.length > 0 && (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-3">
          {en('engine.dependencyPrepareConstraints', { count: plan.constraints.length })}
        </p>
      )}
      {plan.unknown.length > 0 && (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-2" data-dependency-unknown>
          {en('engine.dependencyPrepareUnknown', { modules: plan.unknown.join(', ') })}
        </p>
      )}
      <fieldset className="mt-2 flex flex-col gap-1" data-dependency-target>
        <legend className="sr-only">{en('engine.dependencyPrepareTargetLegend')}</legend>
        {targets.map((opt) => {
          const kind = opt.kind as Target
          const selected = target === kind
          return (
            <label
              key={kind}
              className={cn(
                'flex cursor-pointer items-start gap-2 rounded-sm px-2 py-1.5',
                selected ? 'bg-selected' : 'hover:bg-surface-hover',
                opt.available === false && 'opacity-60',
              )}
              data-dependency-option={kind}
            >
              <Radio
                name="dependency-target"
                className="mt-0.5"
                checked={selected}
                disabled={busy || running || opt.available === false}
                onChange={() => setTarget(kind)}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink">{en(TARGET_LABEL[kind])}</span>
                <span className="mt-0.5 block text-xs leading-relaxed text-ink-3">
                  {kind === 'project_venv'
                    ? en(TARGET_HINT[kind], { venv: opt.venv || opt.python })
                    : en(TARGET_HINT[kind])}
                </span>
                {opt.available === false && (
                  <span className="mt-0.5 block text-xs text-danger">
                    {t(`engine.repairError.${opt.reason}`, { defaultValue: opt.reason })}
                  </span>
                )}
              </span>
            </label>
          )
        })}
      </fieldset>
      <p className="mt-2 text-xs leading-relaxed text-ink-3">{en('engine.dependencyPrepareNetwork')}</p>
      {running && progress && (
        <p className="mt-2 text-xs text-ink-2" data-dependency-state={progress.state}>
          {en(STATE_TEXT[progress.state] ?? 'engine.dependencyPrepareState_preparing')}
        </p>
      )}
      {blocked && blocked.blocked.length > 0 && (
        <ul className="mt-2 flex flex-col gap-0.5 text-xs text-danger" data-dependency-blocked>
          {blocked.blocked.map((b) => (
            <li key={b.code}>{en(BLOCKED_TEXT[b.code] ?? 'engine.dependencyBlocked_dependency_target_unavailable')}</li>
          ))}
        </ul>
      )}
      {!running && errorLine && (
        <p className="mt-1 text-xs text-danger" data-dependency-error={code}>
          {errorLine}
        </p>
      )}
    </Dialog>
  )
}
