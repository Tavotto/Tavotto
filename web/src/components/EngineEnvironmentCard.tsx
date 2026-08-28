import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useEnvStore } from '@/store/envStore'
import { t as translate } from '@/i18n'
import type { EngineSource, ProjectEnvFailure } from '@/lib/api'
import { ManagedEnvironmentRow } from './DependencyRepairCard'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'

/**
 * 渲染环境的状态与出口。
 *
 * 三种局面，给的东西完全不同：
 *
 *  1. **一切正常**（多数用户，尤其 Windows 桌面版——安装包自带内置环境）。
 *     `compact` 时什么都不显示：正常工作流里不该有一个常驻卡片提醒你「环境没问题」。
 *     设置页里显示一行状态 + 折叠起来的高级入口。
 *  2. **缺环境**（源码 / pip 安装，机器上没有科学栈）：给「自动安装」按钮。
 *  3. **内置环境缺失或损坏**（桌面版）：这不是用户的环境问题，是我们的安装包
 *     不完整——只能让他重装，绝不假装能现场修（embeddable 里连 pip 都没有）。
 *
 * 「用户脚本要的包内置环境里没有」是第四种，由渲染错误单独引导（见
 * MissingDependencyCard），不在这里处理——那时环境本身是好的。
 */

/** 本卡片的文案在 errors:engine.* 下 */
const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

/** 后端给的是稳定的 source 枚举，人话在这里按当前语言取 */
const sourceLabel = (source: EngineSource): string =>
  en(`sourceLabel.${source || 'unknown'}`)

export function EngineEnvironmentCard({ compact }: { compact?: boolean }) {
  useTranslation('errors')
  const { env, log, installing, refresh, install, setPython } = useEnvStore()
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [advanced, setAdvanced] = useState(false)

  useEffect(() => {
    if (!env) void refresh()
  }, [env, refresh])

  if (!env) return null
  // 正常工作流里不制造多余提示：环境没问题时，紧凑位置（图内元素面板）什么都不显示
  if (env.ok && compact) return null

  const apply = async () => {
    const failure = await setPython(manual.trim() || null)
    setError(failure)
    if (!failure) setManual('')
  }

  const advancedBlock = (
    <div className="flex flex-col gap-1.5 border-t border-border pt-2.5">
      {advanced ? (
        <>
          <span className="text-xs text-ink-2">{en('useOther')}</span>
          <div className="flex items-center gap-1.5">
            <TextInput
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              placeholder={en('pathPlaceholder')}
              aria-label={en('pathAria')}
            />
            <Button onClick={() => void apply()}>{en('apply')}</Button>
          </div>
          <p className="text-xs leading-relaxed text-ink-3">
            {en('useOtherHintBefore')}
            <strong className="font-medium text-ink-2">{en('useOtherHintStrong')}</strong>
            {en('useOtherHintAfter')}
          </p>
          {error && <p className="text-xs text-danger">{error}</p>}
        </>
      ) : (
        <button
          type="button"
          onClick={() => setAdvanced(true)}
          className="self-start text-xs text-accent hover:underline"
        >
          {en('useOtherLink')}
        </button>
      )}
    </div>
  )

  // ---- 1. 一切正常 -------------------------------------------------------
  if (env.ok) {
    const label = sourceLabel(env.source)
    return (
      <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
        <div>
          <h3 className="text-xs font-medium text-ink">{en('okTitle')}</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {label}
            {env.matplotlib && (
              <span className="ml-1.5 font-mono text-ink-3">
                {en('matplotlibVersion', { version: env.matplotlib })}
              </span>
            )}
          </p>
          {env.bundled ? (
            <p className="mt-1 text-xs leading-relaxed text-ink-3">{en('bundledHint')}</p>
          ) : (
            <p className="mt-1 break-all font-mono text-xs text-ink-3">{env.python}</p>
          )}
          <ProjectEnvironmentLine compact={compact} />
        </div>
        {env.bundled && Object.keys(env.runtime?.packages ?? {}).length > 0 && (
          <details className="text-xs text-ink-3">
            <summary className="cursor-pointer select-none text-ink-2">
              {en('bundledPackages')}
            </summary>
            <ul className="mt-1 flex flex-col gap-0.5 font-mono">
              {Object.entries(env.runtime.packages).map(([name, ver]) => (
                <li key={name}>
                  {name} {ver}
                </li>
              ))}
            </ul>
          </details>
        )}
        {!compact && advancedBlock}
      </div>
    )
  }

  // ---- 3. 内置环境缺失 / 损坏（桌面版）-----------------------------------
  if (env.runtime?.expected) {
    return (
      <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
        <div>
          <h3 className="text-xs font-medium text-ink">{en('incompleteTitle')}</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            {en('incompleteBefore')}
            {en(env.code === 'bundled_runtime_invalid' ? 'incompleteInvalid' : 'incompleteMissing')}
            {en('incompleteAfter')}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-ink-3">{en('incompleteHint')}</p>
        </div>
        {!compact && advancedBlock}
      </div>
    )
  }

  // ---- 2. 缺环境（源码 / pip 安装）---------------------------------------
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">{en('missingTitle')}</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">{en('missingBody')}</p>
      </div>

      {env.can_install ? (
        <>
          <Button variant="primary" onClick={() => void install()} disabled={installing}>
            {en(installing ? 'installing' : 'autoInstall')}
          </Button>
          <p className="text-xs leading-relaxed text-ink-3">
            {en('autoInstallHintBefore')}
            <strong className="font-medium text-ink-2">{en('autoInstallHintStrong')}</strong>
            {en('autoInstallHintAfter')}
          </p>
        </>
      ) : (
        <p className="text-xs leading-relaxed text-danger">
          {en('noPythonBefore')}{' '}
          <a
            href="https://www.python.org/downloads/"
            target="_blank"
            rel="noreferrer"
            className="text-accent hover:underline"
          >
            {en('noPythonLink')}
          </a>
          {en('noPythonAfter')}
        </p>
      )}

      {log && (
        <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs text-ink-3">
          {log}
        </pre>
      )}

      {!compact && advancedBlock}
    </div>
  )
}

/**
 * 当前项目用的是哪个渲染环境（ADR 0018）。
 *
 * 用内置环境时**什么都不显示**：那是默认，说一遍等于噪音。项目自己的
 * `.venv` 接手了才显示——那一刻用户需要知道「跑我脚本的不是 Tavotto 自带的
 * 那个 Python」，否则版本对不上时无从查起。
 */
function ProjectEnvironmentLine({ compact }: { compact?: boolean }) {
  useTranslation('errors')
  const { env, setProjectPython } = useEnvStore()
  const project = env?.project
  if (compact || !project?.open) return null
  // Tavotto 替这个项目建的环境是另一种局面：它归我们管，所以那一行还带
  // 「装了什么」与「重建」（ADR 0019），由 ManagedEnvironmentRow 单独渲染。
  if (project.source === 'managed_project_env') return <ManagedEnvironmentRow />
  if (project.source !== 'project_venv') return null
  return (
    <div className="mt-1.5 flex flex-col gap-0.5 border-t border-border pt-1.5">
      <span className="text-xs text-ink-2">
        {en('projectEnvUsing', { path: project.python || '.venv' })}
      </span>
      {project.automatic && project.module && (
        <span className="text-xs text-ink-3">
          {en('projectEnvWhy', { module: project.module })}
        </span>
      )}
      <button
        type="button"
        onClick={() => void setProjectPython(null)}
        className="self-start text-xs text-accent hover:underline"
      >
        {en('projectEnvUseBuiltIn')}
      </button>
    </div>
  )
}

/**
 * 用户脚本 import 了当前渲染环境里没有的包。
 *
 * 顺序是有讲究的：**先给项目自己的环境，再给手填路径**。绝大多数科研项目
 * 旁边就有一个能跑通的 `.venv`，一键切过去比让用户去翻自己 conda 环境的
 * 解释器路径低太多门槛（这正是 Session 7 的起点）。
 *
 * 这一档**始终不提供「帮你装上」**：往内置环境里随便 pip install 会让它不再
 * 可复现，也让「重装就能修」这条退路失效。
 *
 * `projectEnv` 是后端说明「自动接手为什么没成」的结构化原因——四种情况用户
 * 要做的事完全不同，混成一句话等于把可执行的出路藏起来。
 */
export function MissingDependencyCard({
  module,
  projectEnv,
}: {
  module: string
  projectEnv?: ProjectEnvFailure
}) {
  useTranslation('errors')
  const { env, setPython, setProjectPython } = useEnvStore()
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const pkg = module || en('missingModulePackage')
  // 后端发现到但还没在用的候选。`projectEnv.candidates` 是这次失败时算出来的，
  // 没有它就退回环境状态里那份（用户是从设置页看到这张卡的场合）。
  const candidates = projectEnv?.candidates?.length
    ? projectEnv.candidates
    : (env?.project?.can_use_project_venv ?? [])

  /** 四种「没接手成」各有各的下一步，绝不合并成一句 */
  const reason = (() => {
    switch (projectEnv?.code) {
      case 'project_env_module_missing':
        return en('projectEnvAlsoMissing', { venv: projectEnv.venv || '.venv', module: pkg })
      case 'project_env_no_matplotlib':
        return en('projectEnvNoMatplotlib', { venv: projectEnv.venv || '.venv' })
      case 'project_env_unsupported_python':
        return en('projectEnvUnsupported', {
          venv: projectEnv.venv || '.venv',
          version: projectEnv.python_version || '?',
        })
      case 'project_env_unusable':
        return en('projectEnvUnusable', { venv: projectEnv.venv || '.venv' })
      case 'project_env_not_found':
        return en('projectEnvNotFound', { module: pkg })
      default:
        return null
    }
  })()

  const applyVenv = async (rel: string) => {
    setBusy(true)
    setError(await setProjectPython(rel))
    setBusy(false)
  }

  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-surface p-3">
      <div>
        <h3 className="text-xs font-medium text-ink">
          {/* 包名是脚本里的标识符，原样显示 */}
          {en('missingModuleTitle', { module: pkg })}
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          {reason ?? en('missingModuleBody')}
        </p>
      </div>

      {/* 1. 项目自己就有能用的环境——一键切过去，门槛最低的那条路 */}
      {candidates.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-xs text-ink-2">{en('projectEnvPick')}</span>
          <div className="flex flex-wrap gap-1.5">
            {candidates.map((rel) => (
              <Button key={rel} disabled={busy} onClick={() => void applyVenv(rel)}>
                {rel}
              </Button>
            ))}
          </div>
        </div>
      )}

      {/* 2. 都不行时才轮到手填路径（Conda / pyenv / 项目外的环境） */}
      <div className="flex flex-col gap-1.5">
        <span className="text-xs text-ink-2">{en('useOther')}</span>
        <div className="flex items-center gap-1.5">
          <TextInput
            value={manual}
            onChange={(e) => setManual(e.target.value)}
            placeholder={en('pathPlaceholder')}
            aria-label={en('pathAria')}
          />
          <Button onClick={() => void setPython(manual.trim() || null).then(setError)}>
            {en('apply')}
          </Button>
        </div>
      </div>

      {error && <p className="text-xs text-danger">{error}</p>}
      <p className="text-xs leading-relaxed text-ink-3">
        {en('missingModuleNoteBefore')}
        <strong className="font-medium text-ink-2">{en('missingModuleNoteStrong')}</strong>
        {en('missingModuleNoteAfter')}
      </p>
    </div>
  )
}
