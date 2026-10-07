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
import type { DependencyPreparationOffer, PrivatePythonOffer } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { listJoin } from '@/i18n/format'
import { RepairProgressLine, RepairStageList } from './RepairProgressLine'
import {
  downloadFact,
  jointProgressText,
  oneClickEnvironmentSentence,
  oneClickSentence,
  packagesPhrase,
  repairShortMessage,
} from './DependencyRepairCard'

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
 *
 * **一键修复**（2026-09-29，与修复卡同一套说法）：没有装齐的用户环境、后端默认装进 Tavotto 自己的环境时，
 * 框里只有一句人话（为项目准备运行环境并装好哪些包、要不要下载多大）和一个主按钮；要装的完整需求串、
 * 约束、认不出的 import、目标单选都收进默认折叠的「高级」。默认目标是项目自己的 venv（会改用户环境）
 * 时不折叠——那是要用户看清再点的。
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
export const STATE_TEXT: Record<string, string> = {
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
  dependency_scope_conflict: 'engine.dependencyBlocked_dependency_scope_conflict',
  dependency_target_unavailable: 'engine.dependencyBlocked_dependency_target_unavailable',
}

/**
 * 一键修复的形态按**载荷**定：没有装齐的用户环境、后端默认装进 Tavotto
 * 自己的环境、这个环境又能建——授权只有一句人话和一个主按钮。`privatePython` 是默认目标（受管环境）上要不要先准备
 * 私有 Python 的披露。不满足时框里展开目标 / 用户环境的选择。
 */
export function oneClickShape(offer: DependencyPreparationOffer): {
  simple: boolean
  privatePython: PrivatePythonOffer | null
} {
  const complete = (offer.user_environments ?? []).filter((e) => e.satisfies)
  const managed = offer.targets.find((o) => o.kind === 'tavotto_managed')
  return {
    simple: complete.length === 0 && offer.target_kind === 'tavotto_managed' && managed?.available !== false,
    privatePython: managed?.private_python ?? offer.private_python ?? null,
  }
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
  // 候选解释器不在没有用户动作时被起（ADR 0114）：没检查过的单列，选它 = 「检查并使用」，后端现场体检、装齐才改用
  const unchecked = (offer.user_environments ?? []).filter((e) => e.checked === false)
  const partial = (offer.user_environments ?? []).filter((e) => e.checked !== false && e.ok && !e.satisfies)
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
  // 故障同样只露一句（原因 + 下一步，`repairShortMessage`）：一键修复框里它就是标题，别的框里它接在标题下面、
  // 顶掉那段说明；完整的说明、错误码、blocked 的逐条理由都在「详情」里
  const failing = !running && !!errorLine
  const targets = offer.targets.filter((o) => o.kind !== 'system_interpreter')
  const chosen = targets.find((o) => o.kind === target)
  // 一键修复的形态按**载荷**定（不按此刻的单选）：在「高级」里换了目标，版面不跳
  const { simple } = oneClickShape(offer)
  const oneClick = simple && !envChosen && target === 'tavotto_managed'
  // 私有 Python 的披露跟**此刻选中的目标**走（Codex #742）：装进项目 venv / 改用用户环境都不下载、不供应 Python，
  // 标题与「详情」都不许替那条路说「要下载」。载荷顶层那份（干净机器）同样只属于受管目标
  const privatePython =
    !envChosen && chosen?.kind === 'tavotto_managed' ? (chosen.private_python ?? offer.private_python ?? null) : null
  const packages = packagesPhrase(plan.requirements)
  // 「详情」里「要下载什么」那一条：只准备环境（没有要装的包）时不说「装包需要联网」
  const cost = downloadFact(privatePython, { packages: plan.requirements.length > 0 })
  const targetChoice = (
    <>
      <fieldset className="flex flex-col gap-1" data-dependency-target>
        <legend className="sr-only">
          {en(complete.length || unchecked.length ? 'engine.userEnvLegend' : 'engine.dependencyPrepareTargetLegend')}
        </legend>
        {complete.map((env) => {
          const value: Choice = `env:${env.id}`
          const selected = choice === value
          return (
            <label
              key={env.id}
              className={cn(
                'flex items-start gap-2 rounded-sm px-2 py-1.5',
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
        {unchecked.map((env) => {
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
              data-user-env-unchecked
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
                  {en('engine.userEnvUnchecked')}
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
                'flex items-start gap-2 rounded-sm px-2 py-1.5',
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
                <span className="block text-sm text-ink">{en(TARGET_LABEL[kind], { product: PRODUCT_NAME })}</span>
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
    </>
  )
  const details = (
    <>
      {/* 「详情」第一段：将安装的全部包；下面是项目声明的完整形态（extras / 版本），用户自己的名字，不翻译 */}
      {plan.requirements.length > 0 && (
        <p className="mb-1 text-xs leading-relaxed text-ink-2" data-dependency-will-install>
          {t('engine.repairWillInstall', { requirement: listJoin(plan.requirements) })}
        </p>
      )}
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
      {offer.user_environments && complete.length === 0 && unchecked.length === 0 && (
        <p className="mt-2 text-xs leading-relaxed text-ink-3" data-user-env-none>
          {en('engine.userEnvNone')}
        </p>
      )}
      {partial.length > 0 && (
        <Details className="mt-2 text-xs text-ink-3" data-user-env-partial>
          <Summary>{en('engine.userEnvPartial')}</Summary>
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
      {!envChosen && target === 'tavotto_managed' && (
        // 装进 Tavotto 自己的环境：要装什么是上面那张清单，这里只补另外两件事、各一条（与修复卡同一套，不重复）——
        // 要下载什么（私有 Python 多大 / 自带的是哪一份；联网只在这一条里说）、不改动什么
        <>
          {cost && (
            <p
              className="mt-2 text-xs leading-relaxed text-ink-2"
              data-one-click-cost
              {...(privatePython ? { 'data-dependency-private-python': '' } : {})}
            >
              {cost}
            </p>
          )}
          <p className="mt-1 text-xs leading-relaxed text-ink-3">{en('engine.repairFactUntouched')}</p>
        </>
      )}
      {!envChosen && target === 'project_venv' && plan.requirements.length > 0 && (
        <p className="mt-2 text-xs leading-relaxed text-ink-3">{en('engine.dependencyPrepareNetwork')}</p>
      )}
    </>
  )
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !busy && !running) dismiss()
      }}
      title={
        simple && failing
          ? repairShortMessage(code)
          : simple
          ? // 干净机器上什么包都不缺（`requirements: []`）时，要授权的是准备环境本身：换一句，不写「还缺 」（Codex #742）
            plan.requirements.length
            ? oneClickSentence(packages, privatePython)
            : oneClickEnvironmentSentence(privatePython)
          : en('engine.dependencyPrepareTitle', { count: plan.requirements.length })
      }
      description={
        !simple && complete.length && !failing ? en('engine.userEnvBody', { script: offer.script }) : undefined
      }
      size="sm"
      busy={busy || running}
      anchor="dependency-prepare"
      footer={
        running ? (
          <>
            <Button variant="secondary" size="md" data-dependency-prepare-cancel onClick={() => void cancel()}>
              {en('engine.dependencyPrepareCancel')}
            </Button>
          </>
        ) : (
          <>
            <Button variant="secondary" size="md" data-dependency-prepare-later disabled={busy} onClick={dismiss}>
              {en('engine.dependencyPrepareLater')}
            </Button>
            {envChosen ? (
              <Button
                variant="primary"
                size="md"
                data-dependency-prepare-start
                disabled={busy}
                onClick={() => void adoptEnv(envChosen, offer.script)}
              >
                {unchecked.some((e) => e.id === envChosen) ? en('engine.userEnvCheckUse') : en('engine.userEnvUse')}
              </Button>
            ) : (
              <Button
                variant="primary"
                size="md"
                data-dependency-prepare-start
                disabled={busy || !chosen || chosen.available === false}
                onClick={() => void prepare(target)}
              >
                {failed || code
                  ? en('engine.dependencyPrepareRetry')
                  : oneClick
                    ? en('engine.oneClickRepair')
                    : en('engine.dependencyPrepareRun')}
              </Button>
            )}
          </>
        )
      }
    >
      {/* 默认可见的只有标题那一句（+ 非一键修复时的目标单选，每个选项一句短语）与底部「稍后」+ 一个主按钮；
          下载大小、要装的完整需求串、「不准备，直接运行」都在「详情」里。进行中只剩一行进度 */}
      {!simple && !running && targetChoice}
      {!running && (
        <Details className={cn('text-xs', !simple && 'mt-2')} data-repair-advanced>
          <Summary className="type-meta">{en('engine.repairAdvanced')}</Summary>
          <div className="mt-2">
            {failing && (
              <div className="mb-2 flex flex-col gap-0.5 text-xs leading-relaxed text-ink-3" data-dependency-error-detail>
                <p>{errorLine}</p>
                {blocked && blocked.blocked.length > 0 && (
                  <ul className="flex flex-col gap-0.5" data-dependency-blocked>
                    {blocked.blocked.map((b) => (
                      <li key={b.code}>
                        {en(BLOCKED_TEXT[b.code] ?? 'engine.dependencyBlocked_dependency_target_unavailable')}
                      </li>
                    ))}
                  </ul>
                )}
                {code && <p className="font-mono">{en('engine.repairErrorCode', { code })}</p>}
              </div>
            )}
            {details}
            {simple && <div className="mt-2">{targetChoice}</div>}
            <Button
              variant="ghost"
              size="sm"
              className="mt-2"
              disabled={busy}
              data-dependency-skip
              onClick={() => void skip()}
            >
              {en('engine.dependencyPrepareSkip')}
            </Button>
          </div>
        </Details>
      )}
      {running && progress && (
        <div data-dependency-state={progress.state}>
          <RepairProgressLine
            progress={progress}
            text={jointProgressText(progress, (state) => en(STATE_TEXT[state] ?? 'engine.dependencyPrepareState_preparing'))}
          />
          {/* 与修复卡同一套：默认只有那一行，完整的阶段列表、换用 PyPI 镜像的说明（进度记录顶层的 `pypi_mirror`，
              #743 的联合准备两条路与单包修复同一个字段；没有这个键时一个字都不说）、日志都折叠在「详情」里 */}
          <Details className="mt-1.5 text-xs text-ink-3" data-repair-progress-details>
            <Summary className="text-ink-2">{en('engine.repairDetails')}</Summary>
            <div className="mt-1">
              <RepairStageList progress={progress} />
            </div>
            {progress.pypi_mirror && (
              <p className="mt-1 leading-relaxed" data-repair-pypi-mirror>
                {en('engine.repairPypiMirror', { mirror: progress.pypi_mirror })}
              </p>
            )}
            {progress.log && (
              <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs">
                {progress.log}
              </pre>
            )}
          </Details>
        </div>
      )}
      {failing && !simple && (
        <p className="mt-2 text-xs text-danger" data-dependency-error={code}>
          {repairShortMessage(code)}
        </p>
      )}
    </Dialog>
  )
}
