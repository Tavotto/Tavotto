import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import type { DependencyRepairOffer, DependencyTarget } from '@/lib/api'
import { isRepairRunning, useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'

/**
 * 「这个项目还缺 lmfit」→ 点一次 →「安装并继续」→ 图出来（ADR 0019）。
 *
 * 界面纪律，每条都有理由：
 *
 * * **不写成 Python 教程**。主文案只有「这个项目还缺少 X」和一个主动作，
 *   pip / site-packages / virtualenv 这些词一个都不出现在主界面上。
 * * **改用户环境要说清楚**。装进项目 `.venv` 的按钮写「安装到项目环境」而
 *   不是「确定」，旁边一行说明这会修改这个项目现有的 Python 环境。不做
 *   恐吓式弹窗，但也不把「我们要改你的科研环境」藏起来。
 * * **进度按状态说人话，不甩 pip 日志**。几百行 pip 输出放在「安装详情」
 *   折叠区里。
 * * **解析不出包名就不给一键安装**。那时给「指定安装包…」和「选择其他
 *   Python」——绝不拿 import 名当包名装。
 */
const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

/** 安装状态 → 一句话（前端**只按 state 换文案**，不解析日志） */
const STATE_KEY: Record<string, string> = {
  preparing: 'repairPreparing',
  creating_env: 'repairCreatingEnv',
  installing: 'repairInstalling',
  verifying: 'repairVerifying',
  done: 'repairDone',
  failed: 'repairFailed',
  cancelled: 'repairCancelled',
}

export function DependencyRepairCard({
  offer,
  module,
  script,
}: {
  offer: DependencyRepairOffer
  module: string
  script: string
}) {
  useTranslation('errors')
  const { plan, progress, busy, errorCode, errorText, makePlan, install, cancel, reset } =
    useDepRepairStore()
  const [manual, setManual] = useState('')
  const running = isRepairRunning(progress)
  const pkg = offer.requirement?.distribution || module

  // ---- 安装进行中 / 刚结束：只显示进度，不再显示一堆选项 ------------------
  if (progress && (running || progress.state !== 'idle')) {
    return <RepairProgress module={module} onCancel={() => void cancel()} onDone={reset} />
  }

  // ---- 已经形成计划，等用户确认 ------------------------------------------
  if (plan) {
    const toProject = plan.target_kind === 'project_venv'
    return (
      <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
        <div>
          <h3 className="text-xs font-medium text-ink">
            {en('repairConfirmTitle', { module: pkg })}
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {toProject
              ? en('repairConfirmProject', { path: plan.python || '.venv' })
              : en('repairConfirmManaged')}
          </p>
          {toProject && (
            // 改用户自己的环境是不可逆的，这句不能藏起来
            <p className="mt-1 text-xs leading-relaxed text-warn">{en('repairModifiesEnv')}</p>
          )}
          <p className="mt-1 text-xs leading-relaxed text-ink-3">
            {en('repairWillInstall', { requirement: plan.requirement })}
            {plan.network_required ? ` · ${en('repairNeedsNetwork')}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button variant="primary" disabled={busy} onClick={() => install()}>
            {toProject ? en('repairInstallToProject') : en('repairPrepareAndContinue')}
          </Button>
          <Button onClick={reset}>{en('repairBack')}</Button>
        </div>
        <Failure code={errorCode} text={errorText} />
      </div>
    )
  }

  // ---- 起点：给出口 -------------------------------------------------------
  const targets = offer.targets.filter((tg) => tg.available !== false)
  const exhausted = offer.code === 'dependency_repair_rounds_exhausted'
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">{en('repairTitle', { module: pkg })}</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          {offer.requirement
            ? en('repairBody')
            : en('repairUnresolved', { module })}
        </p>
        {exhausted && (
          <p className="mt-1 text-xs leading-relaxed text-ink-3">{en('repairExhausted')}</p>
        )}
      </div>

      {offer.requirement && !exhausted && targets.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {targets.map((tg) => (
            // 一个目标一块：按钮在上、说明在下。**不并排**——Button 是
            // whitespace-nowrap + shrink-0 的，右栏只有 296px，英文按钮
            // 一旦并排就会把旁边那句挤没或把整栏撑破。
            <div key={tg.kind} className="flex flex-col gap-0.5">
              <Button
                className="self-start"
                variant={tg.kind === targets[0].kind ? 'primary' : 'ghost'}
                disabled={busy}
                onClick={() => makePlan({ module, script, target: tg.kind })}
              >
                {label(tg)}
              </Button>
              <span className="truncate text-xs text-ink-3">{hint(tg)}</span>
            </div>
          ))}
        </div>
      )}

      {/* 解析不出包名：用户可以自己指定，但那串东西同样要过后端的语法关 */}
      {!offer.requirement && !exhausted && (
        <div className="flex flex-col gap-1.5">
          <span className="text-xs text-ink-2">{en('repairSpecifyPackage')}</span>
          <div className="flex items-center gap-1.5">
            <TextInput
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              placeholder={en('repairPackagePlaceholder')}
              aria-label={en('repairPackageAria')}
            />
            <Button
              disabled={busy || !manual.trim()}
              onClick={() =>
                makePlan({
                  module,
                  script,
                  target: offer.targets[0]?.kind ?? 'tavotto_managed',
                  distribution: manual.trim(),
                })
              }
            >
              {en('repairContinue')}
            </Button>
          </div>
        </div>
      )}

      {/* **任何一条修复路径走不通时的兜底出口**（ADR 0019 §五 与兼容层
          Layer 4）：换一个已经装好那个包的 Python。它必须**始终在**——
          解析不出包名、没有可用目标、装完还是失败，用户都还有这条路。
          少了它，文案里那句「或者换一个已经装好它的 Python 环境」就指不出
          任何控件（e2e 抓到过一次：卡片只剩「指定安装包」）。 */}
      <OtherPython />

      <Failure code={errorCode} text={errorText} />
    </div>
  )
}

/**
 * 「选择其他 Python」——写项目作用域那一条（ADR 0018 的 `scope="project"`），
 * 不写全局：用户是在修**这个项目**的这个脚本，没理由改变别的项目的渲染环境。
 */
function OtherPython() {
  useTranslation('errors')
  const { setProjectPython } = useEnvStore()
  const [path, setPath] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <div className="flex flex-col gap-1.5 border-t border-border pt-2.5">
      <span className="text-xs text-ink-2">{en('repairUseOtherPython')}</span>
      <div className="flex items-center gap-1.5">
        <TextInput
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder={en('pathPlaceholder')}
          aria-label={en('pathAria')}
        />
        <Button
          disabled={!path.trim()}
          onClick={async () => setError(await setProjectPython(path.trim()))}
        >
          {en('apply')}
        </Button>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

/** 目标环境的按钮文案。**必须短**：按钮不换行，长文案会撑破右栏。 */
function label(target: DependencyTarget): string {
  if (target.kind === 'project_venv') return en('repairUseProjectEnv')
  return target.creates_environment ? en('repairCreateManaged') : en('repairUseManaged')
}

/** 按钮旁边那句「装到哪 / 会不会动你已有的东西」，长了就截断 */
function hint(target: DependencyTarget): string {
  if (target.kind === 'project_venv') return target.venv || '.venv'
  return en('repairManagedHint')
}

/**
 * 安装进度。四个阶段各一句话，pip 日志折叠在「安装详情」里。
 *
 * 取消之后**不假装完整回滚**：改的是用户自己的环境时如实说「可能已发生
 * 部分修改」——那正是「改用户环境必须明确确认」的另一面。
 */
function RepairProgress({
  module,
  onCancel,
  onDone,
}: {
  module: string
  onCancel: () => void
  onDone: () => void
}) {
  useTranslation('errors')
  const { progress } = useDepRepairStore()
  if (!progress) return null
  const running = isRepairRunning(progress)
  const key = STATE_KEY[progress.state] ?? 'repairPreparing'
  const failed = progress.state === 'failed'
  const cancelled = progress.state === 'cancelled'
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">
          {en(key, { module: progress.distribution || module })}
        </h3>
        {failed && (
          <p className="mt-1 text-xs leading-relaxed text-danger">
            {codeMessage(progress.code) ?? progress.error ?? ''}
          </p>
        )}
        {cancelled && (
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {progress.target_kind === 'project_venv'
              ? en('repairCancelledProjectEnv')
              : en('repairCancelledManaged')}
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {running ? (
          <Button onClick={onCancel}>{en('repairCancel')}</Button>
        ) : (
          <Button onClick={onDone}>{en('repairClose')}</Button>
        )}
      </div>
      {progress.log && (
        <details className="text-xs text-ink-3">
          <summary className="cursor-pointer select-none text-ink-2">
            {en('repairDetails')}
          </summary>
          <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs">
            {progress.log}
          </pre>
        </details>
      )}
    </div>
  )
}

/** 稳定错误码 → 当前语言的一句话；没登记的回 null（让调用方用后端原文兜底） */
function codeMessage(code: string): string | null {
  if (!code) return null
  const key = `repairError.${code}`
  const text = en(key)
  return text === `engine.${key}` ? null : text
}

function Failure({ code, text }: { code: string; text: string }) {
  if (!code && !text) return null
  return <p className="text-xs leading-relaxed text-danger">{codeMessage(code) ?? text}</p>
}

/**
 * Tavotto 受管环境的「重建」入口（设置页里）。
 *
 * 只对**我们自己建的**环境出现：用户的 `.venv` 不归我们重建，那是他的东西。
 */
export function ManagedEnvironmentRow() {
  useTranslation('errors')
  const { env } = useEnvStore()
  const { busy, rebuildManaged } = useDepRepairStore()
  const managed = env?.project?.managed
  if (!env?.project?.open || !managed?.exists) return null
  return (
    <div className="mt-1.5 flex flex-col gap-0.5 border-t border-border pt-1.5">
      <span className="text-xs text-ink-2">
        {en('managedEnvUsing', { version: managed.python_version || '?' })}
      </span>
      {managed.installed.length > 0 && (
        <span className="text-xs text-ink-3">
          {en('managedEnvInstalled', {
            packages: managed.installed
              .map((p) => `${p.distribution} ${p.resolved_version}`)
              .join('、'),
          })}
        </span>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={() => void rebuildManaged()}
        className="self-start text-xs text-accent hover:underline disabled:opacity-50"
      >
        {en('managedEnvRebuild')}
      </button>
    </div>
  )
}
