import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { isRepairRunning, useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { Button } from './ui/Button'
import { Details, Summary } from './ui/Details'
import { Dialog } from './ui/Dialog'
import { Radio } from './ui/Radio'
import { userEnvironmentName } from '@/lib/userEnvironmentText'

/**
 * 跑前的那一次授权（U04，ADR 0061 §六）：后端起第一个 worker 之前看一眼脚本开跑要的第三方包
 * 目标环境里缺不缺、缺的能不能一次装全——能就以 `dependency_preparation_required` 回来。这不是
 * 错误，是缺一次授权：整份联合计划（装什么 / 约束什么 / 装到哪 / 会不会改用户环境 / 认不出的
 * import）都在载荷里，这里只翻译、不裁决。
 *
 * 目标两档：Tavotto 自己的隔离环境（默认；可删可重建）/ 项目自己的 venv（只在它就是此刻选中的
 * 解释器时出现；会改用户环境，文案说清）。「准备并继续」= 绑定计划 + 执行 + 看进度，装完后端
 * 作废旧会话、前端重排失败的渲染；「稍后」只关框（这道门一直问到有答案，错误块里还能再开）；
 * 「不准备，直接运行」是明确的 skip（`POST /api/engine/dependencies/skip`）——之后缺包会以
 * `missing_dependency` 回来，走运行后那条修复路。
 *
 * **用户自己的环境**（ADR 0079）排在安装目标前面、同一组单选里：后端在这台电脑上找到的 Python
 * 里，装齐了的列出来（按后端的挑选顺序，第一个预选），点「改用这个环境」只交 id；没装齐的收在
 * 折叠里只说还缺什么。装齐的只有在后端**没有**自动改用时才会出现在这里（用户改回过 / 显式选过
 * 别的环境）——自动改用的那条走通知轨，不弹框。
 */
type Target = 'project_venv' | 'tavotto_managed'
/** 单选的值：安装目标，或 `env:<id>`（用户环境） */
type Choice = Target | `env:${string}`

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
  downloading_python: 'engine.dependencyPrepareState_downloading_python',
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
  const offer = useEnvStore((s) => s.dependencyPreparation)
  const dismiss = useEnvStore((s) => s.dismissDependencyPreparation)
  const progress = useDepRepairStore((s) => s.progress)
  const busy = useDepRepairStore((s) => s.busy)
  const errorCode = useDepRepairStore((s) => s.errorCode)
  const errorText = useDepRepairStore((s) => s.errorText)
  const blocked = useDepRepairStore((s) => s.jointBlocked)
  const prepare = useDepRepairStore((s) => s.prepare)
  const cancel = useDepRepairStore((s) => s.cancelPreparation)
  const skip = useDepRepairStore((s) => s.skipPreparation)
  const adoptEnv = useDepRepairStore((s) => s.adoptUserEnvironment)
  const [choice, setChoice] = useState<Choice>('tavotto_managed')
  useEffect(() => {
    // 每一份新载荷：有装齐的用户环境就预选后端排在第一的那个，否则从后端算出来的安装目标起步
    const first = offer?.user_environments?.find((e) => e.satisfies)
    setChoice(first ? `env:${first.id}` : (offer?.target_kind ?? 'tavotto_managed'))
  }, [offer])
  if (!offer) return null
  const complete = (offer.user_environments ?? []).filter((e) => e.satisfies)
  const partial = (offer.user_environments ?? []).filter((e) => e.ok && !e.satisfies)
  const envChosen = choice.startsWith('env:') ? choice.slice(4) : null
  const target = (envChosen ? offer.target_kind : choice) as Target
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
      description={
        complete.length
          ? en('engine.userEnvBody', { script: offer.script })
          : en('engine.dependencyPrepareBody', { script: offer.script })
      }
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
            <Button variant="secondary" size="md" disabled={busy} onClick={() => void skip()}>
              {en('engine.dependencyPrepareSkip')}
            </Button>
            {envChosen ? (
              <Button
                variant="primary"
                size="md"
                disabled={busy}
                onClick={() => void adoptEnv(envChosen, offer.script)}
              >
                {en('engine.userEnvUse')}
              </Button>
            ) : (
              <Button
                variant="primary"
                size="md"
                disabled={busy || !chosen || chosen.available === false}
                onClick={() => void prepare(target)}
              >
                {failed || code ? en('engine.dependencyPrepareRetry') : en('engine.dependencyPrepareRun')}
              </Button>
            )}
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
      {offer.user_environments && complete.length === 0 && (
        <p className="mt-2 text-xs leading-relaxed text-ink-3" data-user-env-none>
          {en('engine.userEnvNone')}
        </p>
      )}
      <fieldset className="mt-2 flex flex-col gap-1" data-dependency-target>
        <legend className="sr-only">
          {en(complete.length ? 'engine.userEnvLegend' : 'engine.dependencyPrepareTargetLegend')}
        </legend>
        {complete.map((env) => {
          const value: Choice = `env:${env.id}`
          const selected = choice === value
          return (
            <label
              key={env.id}
              className={cn(
                'flex cursor-pointer items-start gap-2 rounded-sm px-2 py-1.5',
                selected ? 'bg-selected' : 'hover:bg-surface-hover',
              )}
              data-user-env={env.source}
            >
              <Radio
                name="dependency-target"
                className="mt-0.5"
                checked={selected}
                disabled={busy || running}
                onChange={() => setChoice(value)}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink first-letter:uppercase">
                  {userEnvironmentName(env)}
                </span>
                <span className="mt-0.5 block text-xs leading-relaxed text-ink-3">
                  {en('engine.userEnvVersion', { version: env.python_version })} ·{' '}
                  {en('engine.userEnvComplete')}
                </span>
              </span>
            </label>
          )
        })}
        {targets.map((opt) => {
          const kind = opt.kind as Target
          const selected = choice === kind
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
                onChange={() => setChoice(kind)}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink">{en(TARGET_LABEL[kind])}</span>
                <span className="mt-0.5 block text-xs leading-relaxed text-ink-3">
                  {kind === 'project_venv'
                    ? en(TARGET_HINT[kind], { venv: opt.venv || opt.python })
                    : en(TARGET_HINT[kind])}
                </span>
                {opt.private_python && (
                  // 这台机器没有可用的 Python：这次授权包含先下载 Tavotto 自己的一份（U05）。
                  // 体积必须说出口；已有校验过的缓存时不联网。
                  <span className="mt-0.5 block text-xs leading-relaxed text-ink-2" data-dependency-private-python>
                    {opt.private_python.cached
                      ? en('engine.dependencyPreparePrivatePythonCached', {
                          version: opt.private_python.version,
                        })
                      : en('engine.dependencyPreparePrivatePython', {
                          version: opt.private_python.version,
                          mb: Math.max(1, Math.round(opt.private_python.download_bytes / 1048576)),
                        })}
                  </span>
                )}
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
      {partial.length > 0 && (
        <Details className="mt-2 text-xs text-ink-3" data-user-env-partial>
          <Summary className="cursor-pointer">{en('engine.userEnvPartial')}</Summary>
          <ul className="mt-1 flex flex-col gap-0.5 pl-3">
            {partial.map((env) => (
              <li key={env.id}>
                <span className="text-ink-2 first-letter:uppercase">{userEnvironmentName(env)}</span>
                {' · '}
                {en('engine.userEnvMissing', { packages: env.missing.join(', ') })}
              </li>
            ))}
          </ul>
        </Details>
      )}
      {!envChosen && (
        <p className="mt-2 text-xs leading-relaxed text-ink-3">{en('engine.dependencyPrepareNetwork')}</p>
      )}
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
