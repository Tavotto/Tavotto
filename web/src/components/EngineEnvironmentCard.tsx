import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useEnvStore } from '@/store/envStore'
import { useDepRepairStore } from '@/store/depRepairStore'
import { askConfirm } from '@/store/uiStore'
import { msg, t as translate } from '@/i18n'
import type { EngineSource, ProjectEnvFailure } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { currentProjectId } from '@/lib/session'
import { InputRemapRows, ScriptBackupRows, WorkdirRow } from './WorkdirRow'
import { GroupNotice, SettingRow } from './settings/SettingRow'
import { Ellipsis, RotateCcw } from './ui/icons'
import { ICON_SIZE } from './ui/Icon'
import { Button, IconButton } from './ui/Button'
import { FieldGroup } from './ui/FormSection'
import { TextInput } from './ui/Input'
import { Card } from './ui/Card'
import { Menu, MenuItem } from './ui/Menu'
import { Notice } from './ui/Notice'

/**
 * 渲染环境的状态与出口。
 *
 * 三种局面，给的东西完全不同：
 *
 *  1. **一切正常**（多数用户，尤其 Windows 桌面版——安装包自带内置环境）。
 *     `compact` 时什么都不显示：正常工作流里不该有一个常驻卡片提醒你「环境没问题」。
 *     设置页 / 「渲染环境」对话框里是一组行（解释器 / 项目环境 / 运行目录 / 记住的数据位置 / 脚本备份）。
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

/** 后端给的是稳定的 source 枚举，人话在这里按当前语言取。
 *  产品名走 `brand.ts`——界面文案里不手写它（根 AGENTS.md）。 */
const sourceLabel = (source: EngineSource): string =>
  en(`sourceLabel.${source || 'unknown'}`, { product: PRODUCT_NAME })

export function EngineEnvironmentCard({
  compact,
  hideTitle,
}: {
  compact?: boolean
  /**
   * 不画卡片自己的标题（「尚未配置渲染环境」那类）：外面已经有一个说同一件事的标题时用——
   * 「渲染环境」对话框的标题栏。设置页的分组版本没有卡片标题（组标题由页面给）。
   */
  hideTitle?: boolean
}) {
  useTranslation('errors')
  const { env, log, installing, refresh } = useEnvStore()

  useEffect(() => {
    if (!env) void refresh()
  }, [env, refresh])

  if (!env) return null
  // 正常工作流里不制造多余提示：环境没问题时，紧凑位置（图内元素面板）什么都不显示
  if (env.ok && compact) return null

  // ---- 紧凑位置（图内元素面板 / 脚本区里的错误块）：一张卡 ------------------------
  // 它在那里是插进别的内容之间的一段独立提示，不套框就散了
  if (compact) {
    const title = (key: string) => (hideTitle ? null : <h3 className="type-section">{en(key)}</h3>)
    if (env.runtime?.expected) {
      return (
        <Card data-engine-env-card appearance="raised" padding="md" className="flex flex-col gap-2.5">
          <div>
            {title('incompleteTitle')}
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              {en('incompleteBefore')}
              {en(env.code === 'bundled_runtime_invalid' ? 'incompleteInvalid' : 'incompleteMissing')}
              {en('incompleteAfter')}
            </p>
            <p className="mt-1 text-xs leading-relaxed text-ink-3">{en('incompleteHint')}</p>
          </div>
        </Card>
      )
    }
    return (
      <Card data-engine-env-card appearance="raised" padding="md" className="flex flex-col gap-2.5">
        <div>
          {title('missingTitle')}
          <p className="mt-1 text-xs leading-relaxed text-ink-2">{en('missingBody')}</p>
        </div>
        <AutoInstall />
        {log && <InstallLog log={log} />}
      </Card>
    )
  }

  /**
   * 设置页 / 「渲染环境」对话框：**一组行**（2026-10-07 设计审计 §9.1，P0「Python 与运行」组）。
   * 此前是重复标题 + 段落 + 三段 `border-t` 碎片 + 左对齐按钮——读起来是一篇说明，不是设置。
   * 现在：解释器 / 项目环境 / 运行目录 / 记住的数据位置 / 脚本备份各一行，缺件是组内一条 danger Notice。
   * 这一组的根就是 `data-engine-env-card`（判「这里只有一份环境界面」认它）。
   */
  return (
    <FieldGroup data-engine-env-card>
      {!env.ok && env.runtime?.expected && (
        <GroupNotice tone="danger" title={hideTitle ? undefined : en('incompleteTitle')}>
          {en('incompleteBefore')}
          {en(env.code === 'bundled_runtime_invalid' ? 'incompleteInvalid' : 'incompleteMissing')}
          {en('incompleteAfter')} {en('incompleteHint')}
        </GroupNotice>
      )}
      {!env.ok && !env.runtime?.expected && (
        <GroupNotice
          tone="danger"
          title={hideTitle ? undefined : en('missingTitle')}
          action={env.can_install ? <AutoInstallButton size="sm" /> : undefined}
        >
          {en('missingBody')}
          {env.can_install ? (
            <span className="mt-1 block">
              {en('autoInstallHintBefore')}
              <strong className="font-medium">{en('autoInstallHintStrong')}</strong>
              {en('autoInstallHintAfter')}
            </span>
          ) : (
            <span className="mt-1 block">
              <NoPython />
            </span>
          )}
        </GroupNotice>
      )}
      {!env.ok && log && (
        <div>
          <InstallLog log={log} />
        </div>
      )}
      <InterpreterRow />
      {env.ok && <ProjectEnvironmentRow />}
      {env.ok && <WorkdirRow />}
      {env.ok && <InputRemapRows />}
      {env.ok && <ScriptBackupRows />}
      {installing && <span className="sr-only">{en('installing')}</span>}
    </FieldGroup>
  )
}

/** 「自动安装」那颗钮（紧凑卡里是整宽主按钮 + 一句说明；组里是 Notice 的动作） */
function AutoInstallButton({ size }: { size?: 'sm' | 'md' }) {
  const { installing, install } = useEnvStore()
  return (
    <Button variant="primary" size={size} onClick={() => void install()} disabled={installing}>
      {en(installing ? 'installing' : 'autoInstall')}
    </Button>
  )
}

function AutoInstall() {
  const env = useEnvStore((s) => s.env)
  if (!env?.can_install) {
    return (
      <p className="text-xs leading-relaxed text-danger">
        <NoPython />
      </p>
    )
  }
  return (
    <>
      <AutoInstallButton />
      <p className="text-xs leading-relaxed text-ink-3">
        {en('autoInstallHintBefore')}
        <strong className="font-medium text-ink-2">{en('autoInstallHintStrong')}</strong>
        {en('autoInstallHintAfter')}
      </p>
    </>
  )
}

function NoPython() {
  return (
    <>
      {en('noPythonBefore')}{' '}
      <a
        href="https://www.python.org/downloads/"
        target="_blank"
        rel="noreferrer"
        className="underline underline-offset-2 hover:text-ink"
      >
        {en('noPythonLink')}
      </a>
      {en('noPythonAfter')}
    </>
  )
}

function InstallLog({ log }: { log: string }) {
  return (
    <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-sm bg-surface-2 p-1.5 font-mono text-xs text-ink-3">
      {log}
    </pre>
  )
}

/**
 * 解释器一行：现状是「用的是哪一个」（来源的人话；外部解释器在 fill 行再给路径），控件列「更换…」——点开
 * 才在这一行下面展开路径输入框（fill 行）。此前它是卡片底部一颗 ghost「使用其他 Python 环境…」。
 */
function InterpreterRow() {
  useTranslation('errors')
  const { env, setPython } = useEnvStore()
  const [editing, setEditing] = useState(false)
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)
  if (!env) return null
  const apply = async () => {
    const failure = await setPython(manual.trim() || null)
    setError(failure)
    if (!failure) {
      setManual('')
      setEditing(false)
    }
  }
  const editorId = 'engine-interpreter-editor'
  // 内置环境不再重复说明自带科学栈；只有外部解释器才需要露出具体路径
  const showPath = env.ok && !env.bundled
  return (
    <SettingRow
      label={en('interpreterLabel')}
      status={env.ok ? sourceLabel(env.source) : undefined}
      data-settings-anchor="project.python"
      data-engine-interpreter
      below={
        showPath || editing ? (
          <div className="flex min-w-0 flex-col gap-2">
            {showPath && <p className="break-all font-mono text-xs text-ink-3">{env.python}</p>}
            {editing && (
              <div id={editorId} className="flex min-w-0 flex-col gap-1.5">
                <div className="flex items-center gap-1.5">
                  <TextInput
                    value={manual}
                    autoFocus
                    onChange={(e) => setManual(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void apply()
                    }}
                    placeholder={en('pathPlaceholder')}
                    aria-label={en('pathAria')}
                    className="min-w-0 flex-1"
                  />
                  <Button variant="secondary" size="sm" onClick={() => void apply()}>
                    {en('apply')}
                  </Button>
                </div>
                <p className="type-caption">
                  {en('useOtherHintBefore')}
                  <strong className="font-medium text-ink-2">{en('useOtherHintStrong')}</strong>
                  {en('useOtherHintAfter')}
                </p>
                {error && <Notice tone="danger">{error}</Notice>}
              </div>
            )}
          </div>
        ) : undefined
      }
    >
      <Button
        variant="secondary"
        size="sm"
        aria-expanded={editing}
        aria-controls={editing ? editorId : undefined}
        onClick={() => setEditing((v) => !v)}
      >
        {en('changeInterpreter')}
      </Button>
    </SettingRow>
  )
}

/**
 * 当前项目用的是哪个渲染环境（ADR 0018）。
 *
 * 用内置环境时**什么都不显示**：那是默认，说一遍等于噪音。项目自己的
 * `.venv` 接手了才显示——那一刻用户需要知道「跑我脚本的不是 Tavotto 自带的
 * 那个 Python」，否则版本对不上时无从查起。
 */
function ProjectEnvironmentRow() {
  useTranslation('errors')
  const { env, setProjectPython } = useEnvStore()
  const project = env?.project
  if (!project?.open) return null
  // Tavotto 替这个项目建的环境是另一种局面：它归我们管，所以那一行还带
  // 「装了什么」与「重建」（ADR 0019）
  if (project.source === 'managed_project_env') return <ManagedEnvironmentRow />
  // 项目之外的解释器（用户为这个项目挑的，或从依赖修复面板采用的系统 Python，
  // ADR 0044）与项目自带的 `.venv` 是两种局面：前者显示绝对路径、措辞是
  // 「你选了」，不能套「项目环境：…」那句——`/usr/bin/python3` 不是项目环境。
  const system = project.source === 'system'
  if (project.source !== 'project_venv' && !system) return null
  return (
    <SettingRow
      label={en('projectEnvLabel')}
      status={
        system
          ? en('projectEnvUsingSystem', { path: project.python || '' })
          : en('projectEnvUsing', { path: project.python || '.venv' })
      }
      description={
        project.module && (project.automatic || system)
          ? // 缺包时无提示自动采用的（ADR 0107）不是「你选的」：措辞按 automatic 分
            system
            ? project.automatic
              ? en('projectEnvWhySystemAuto', { module: project.module })
              : en('projectEnvWhySystem', { module: project.module })
            : en('projectEnvWhy', { module: project.module })
          : undefined
      }
    >
      <Button variant="ghost" size="sm" onClick={() => void setProjectPython(null)}>
        {en('projectEnvUseBuiltIn')}
      </Button>
    </SettingRow>
  )
}

/**
 * Tavotto 替这个项目建的环境（ADR 0019）：现状 = 版本，说明 = 装了什么；「重建」是会真动环境的高影响动作，
 * 收进行尾 ⋯ 并先确认（与包管理页同一句确认、同一个 `rebuildManaged`）。此前它是 `DependencyRepairCard`
 * 里的 `ManagedEnvironmentRow`（一段 `border-t` 碎片 + 左对齐的 secondary，点了就重建、不问）。
 * 只对**我们自己建的**环境出现：用户的 `.venv` 不归我们重建，那是他的东西。
 */
function ManagedEnvironmentRow() {
  useTranslation('errors')
  const env = useEnvStore((s) => s.env)
  const repairBusy = useDepRepairStore((s) => s.busy)
  const rebuildRunningFor = useDepRepairStore((s) => s.rebuildRunningFor)
  const rebuildManaged = useDepRepairStore((s) => s.rebuildManaged)
  // 本项目的重建还没结束时起不了第二次（别的项目的重建不挡这里，#606）
  const busy = repairBusy || rebuildRunningFor(currentProjectId())
  const managed = env?.project?.managed
  if (!env?.project?.open || !managed?.exists) return null
  const rebuild = async () => {
    const ok = await askConfirm({
      title: msg('settings.packages.confirm.rebuildTitle', undefined, 'dialogs'),
      body: msg('settings.packages.confirm.rebuildBody', undefined, 'dialogs'),
      confirmLabel: msg('settings.packages.confirm.rebuildAction', undefined, 'dialogs'),
      danger: true,
    })
    if (ok) await rebuildManaged()
  }
  return (
    <SettingRow
      label={en('projectEnvLabel')}
      status={en('managedEnvUsing', { version: managed.python_version || '?', product: PRODUCT_NAME })}
      description={
        managed.installed.length > 0
          ? en('managedEnvInstalled', {
              packages: managed.installed.map((p) => `${p.distribution} ${p.resolved_version}`).join('、'),
            })
          : undefined
      }
      data-managed-env
    >
      <Menu
        align="end"
        width={220}
        trigger={
          <IconButton label={en('envActions')} iconSize="sm">
            <Ellipsis size={ICON_SIZE.sm} aria-hidden />
          </IconButton>
        }
      >
        <MenuItem icon={RotateCcw} danger disabled={busy} onSelect={() => void rebuild()}>
          {en('managedEnvRebuild')}
        </MenuItem>
      </Menu>
    </SettingRow>
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
      case 'project_env_worker_import_failed':
        return en('projectEnvWorkerImport', { venv: projectEnv.venv || '.venv' })
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
    <Card className="flex flex-col gap-2.5">
      <div>
        <h3 className="type-section">
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
    </Card>
  )
}
