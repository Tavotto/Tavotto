import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import {
  backendErrorText,
  type DependencyPreparationOffer,
  type InputRemapRule,
  listScriptBackups,
  type MissingInputOffer,
  restoreScriptBackup,
  type ScriptBackup,
  type WorkdirConfirmation,
  type WorkdirMode,
} from '@/lib/api'
import { useUiStore } from '@/store/uiStore'
import { useEnvStore } from '@/store/envStore'
import { SettingRow } from './settings/SettingRow'
import { Button } from './ui/Button'
import { Segmented } from './ui/Segmented'

const en = (key: string, values?: Record<string, unknown>) =>
  translate(`engine.${key}`, { ns: 'errors', ...(values ?? {}) })

//: 三档的文案键写成字面量（i18n 死键门禁按「源码里出现过这个串」判活）
const MODE_LABEL: Record<WorkdirMode, string> = {
  sandbox: 'engine.workdirMode_sandbox',
  project: 'engine.workdirMode_project',
  project_root: 'engine.workdirMode_project_root',
}
const MODE_STATUS: Record<WorkdirMode, string> = {
  sandbox: 'engine.workdirHintSandbox',
  project: 'engine.workdirHintProject',
  project_root: 'engine.workdirHintProjectRoot',
}
const MODES: WorkdirMode[] = ['sandbox', 'project', 'project_root']

/**
 * 「脚本的运行目录」——safe worker 工作目录模式的项目级三档（ADR 0047 / 0057）：
 * 沙盒（默认）/ 脚本目录 / 项目根。
 *
 * 文案与机制逐条一致：真实目录下脚本用相对路径读的数据找得到、用相对路径写的
 * 文件落进项目目录；Tavotto 仍然不替它保存图片、不删不改项目里的文件。切到两个
 * 真实目录都要确认一次（在 envStore.setWorkdirMode 里），切回沙盒不用。
 */
export function WorkdirRow() {
  const { t } = useTranslation('errors')
  const { env, setWorkdirMode } = useEnvStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const project = env?.project
  if (!project?.open || !project.workdir) return null
  const mode = project.workdir.mode
  // 没决定过、因为用的是用户自己的 Python 而默认在脚本目录（ADR 0107 §二）：现状那句说出这个来由
  const status =
    project.workdir.implied_by === 'user_interpreter' && mode === 'project'
      ? 'engine.workdirHintProjectNative'
      : MODE_STATUS[mode]
  // 老服务端只有两档：它报的 `modes` 里没有第三档时不摆出来
  const available = MODES.filter((m) => (project.workdir?.modes ?? MODES).includes(m))
  const pick = async (next: WorkdirMode) => {
    setBusy(true)
    setError(await setWorkdirMode(next))
    setBusy(false)
  }
  return (
    <div className="mt-1.5 border-t border-border pt-1.5">
      {/* 标准设置行（全面打磨 D14）。当前档那句话是**现状**不是说明（§13：低调提醒走
          `status`），常驻在标题列里，不收进问号。控件整行宽：三档分段放不进定宽控件列 */}
      <SettingRow label={en('workdirLabel')} status={t(status)} control="fill">
        <Segmented
          value={mode}
          onChange={(v) => void pick(v)}
          ariaLabel={en('workdirLabel')}
          data-testid="setting-workdir-mode"
          items={available.map((m) => ({
            value: m,
            label: t(MODE_LABEL[m]),
            disabled: busy,
          }))}
        />
      </SettingRow>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

/**
 * 「脚本跑完没出图」错误块里的出口：多半是沙盒 cwd 下相对路径找不到数据。
 * 已经是真实目录模式时不显示——那时原因在别处。
 */
export function WorkdirSuggestion() {
  useTranslation('errors')
  const { env, setWorkdirMode } = useEnvStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const project = env?.project
  if (!project?.open || project.workdir?.mode !== 'sandbox') return null
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <p className="text-xs leading-relaxed text-ink-2">{en('workdirSuggest')}</p>
      <Button
        className="self-start"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setError(await setWorkdirMode('project'))
          setBusy(false)
        }}
      >
        {en('workdirSuggestButton')}
      </Button>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

/**
 * 「先准备依赖」错误块里的出口（U04）：授权框被「稍后」关掉之后，从这里再打开——
 * 载荷留在渲染条目上（`PanelRender.dependencyPreparation`），这里只把它交回 envStore。
 */
export function DependencyPrepareButton({ offer }: { offer: DependencyPreparationOffer | null }) {
  useTranslation('errors')
  const request = useEnvStore((s) => s.requestDependencyPreparation)
  if (!offer) return null
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <Button className="self-start" onClick={() => request(offer)}>
        {en('dependencyPrepareOpen')}
      </Button>
    </div>
  )
}

/**
 * 「先选运行目录」错误块里的出口（U03）：确认框被「稍后」关掉之后，从这里再打开——
 * 载荷留在渲染条目上（`PanelRender.confirmation`），这里只把它交回 envStore。
 */
export function WorkdirChooseButton({ confirmation }: { confirmation: WorkdirConfirmation | null }) {
  useTranslation('errors')
  const request = useEnvStore((s) => s.requestWorkdirConfirmation)
  if (!confirmation) return null
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <p className="text-xs leading-relaxed text-ink-2">{en('workdirChooseSuggest')}</p>
      <Button className="self-start" onClick={() => request(confirmation)}>
        {en('workdirChooseButton')}
      </Button>
    </div>
  )
}

/**
 * 「找不到数据」错误块里的出口（ADR 0106）：对话框被「稍后」关掉之后，从这里再打开——
 * 载荷留在渲染条目上（`PanelRender.missingInput`），这里只把它交回 envStore。
 */
export function MissingInputButton({ offer }: { offer: MissingInputOffer | null }) {
  useTranslation('errors')
  const request = useEnvStore((s) => s.requestMissingInput)
  if (!offer) return null
  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <Button className="self-start" data-testid="missing-input-open" onClick={() => request(offer)}>
        {en('missingInputOpen')}
      </Button>
    </div>
  )
}

/**
 * 设置 › 渲染环境：本项目记住的数据位置（ADR 0106）。每条一行「脚本写的 → 现在去哪找」，可删；
 * 删了就回到「找不到就报错」。没有规则时不占地方。
 */
export function InputRemapRows() {
  const { t } = useTranslation('errors')
  const { env, forgetInputRemap } = useEnvStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const rules = env?.project?.open ? (env.project.input_remap?.rules ?? []) : []
  if (!rules.length) return null
  const forget = async (rule: InputRemapRule) => {
    setBusy(true)
    setError(await forgetInputRemap(rule))
    setBusy(false)
  }
  return (
    <div className="mt-1.5 border-t border-border pt-1.5" data-testid="input-remap-rows">
      <p className="text-xs text-ink-2">{en('inputRemapLabel')}</p>
      <ul className="mt-1 flex flex-col gap-1">
        {rules.map((r) => (
          <li key={`${r.kind}:${r.from}`} className="flex items-start gap-2">
            {/* 两侧都是用户自己的路径，不翻译 */}
            <span className="min-w-0 flex-1 break-all font-mono text-xs text-ink-2">
              {r.from === '' ? t('engine.inputRemapAnyRelative') : r.from}
              {' → '}
              {r.to}
              {r.target_exists === false && (
                <span className="ml-1 font-sans text-danger">{en('inputRemapTargetGone')}</span>
              )}
            </span>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => void forget(r)}>
              {en('inputRemapForget')}
            </Button>
          </li>
        ))}
      </ul>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

//: 备份此刻的状态：键写成字面量（i18n 死键门禁按「源码里出现过这个串」判活）
const BACKUP_STATE_TEXT = {
  current: 'engine.scriptBackupState.current',
  before: 'engine.scriptBackupState.before',
  changed: 'engine.scriptBackupState.changed',
} as const

/**
 * 设置 › 渲染环境：改写脚本之前留下的备份（ADR 0110 §七）。每条一行「哪份脚本、什么时候、此刻是什么状态」：
 * 此刻就是改后那份 → 「恢复原脚本」；之后又被改过 → 「只撤销那几处路径」（其余修改保留）或「整份恢复」
 * （当前版本先另存一份）；此刻已是改前那份 → 没有按钮。状态是后端对着磁盘现算的，前端只翻译。
 */
export function ScriptBackupRows() {
  const { t } = useTranslation('errors')
  const open = useEnvStore((s) => Boolean(s.env?.project?.open))
  const generation = useEnvStore((s) => s.scriptBackupGeneration)
  const bump = useEnvStore((s) => s.bumpScriptBackups)
  const [items, setItems] = useState<ScriptBackup[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!open) {
      setItems([])
      return
    }
    let live = true
    listScriptBackups()
      .then((r) => live && setItems(r.backups))
      .catch(() => live && setItems([]))
    return () => {
      live = false
    }
  }, [open, generation])
  const shown = items.filter((b) => b.kind === 'input_path')
  if (!shown.length) return null
  const restore = async (b: ScriptBackup, mode: 'full' | 'undo_edits') => {
    setBusy(true)
    setError(null)
    try {
      await restoreScriptBackup(b.id, mode)
      useUiStore.getState().setStatus(msg('engine.scriptBackupRestored', { script: b.script }, 'errors'))
      const { useRenderStore } = await import('@/store/renderStore')
      useRenderStore.getState().retryEnvironmentFailures()
    } catch (e) {
      setError(backendErrorText(e))
    }
    setBusy(false)
    bump()
  }
  return (
    <div className="mt-1.5 border-t border-border pt-1.5" data-testid="script-backup-rows">
      <p className="text-xs text-ink-2">{en('scriptBackupLabel')}</p>
      <ul className="mt-1 flex flex-col gap-1">
        {shown.map((b) => (
          <li key={b.id} className="flex flex-wrap items-start gap-2" data-script-backup={b.id}>
            <span className="min-w-0 flex-1 text-xs text-ink-2">
              {/* 脚本名是用户自己的路径，不翻译 */}
              <span className="break-all font-mono">{b.script}</span>
              {' · '}
              {new Date(b.created * 1000).toLocaleString()}
              {' · '}
              {t(BACKUP_STATE_TEXT[b.state] ?? BACKUP_STATE_TEXT.changed)}
            </span>
            {b.state === 'current' && (
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => void restore(b, 'full')}>
                {en('scriptBackupRestore')}
              </Button>
            )}
            {b.state === 'changed' && (
              <>
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => void restore(b, 'undo_edits')}>
                  {en('scriptBackupUndoEdits')}
                </Button>
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => void restore(b, 'full')}>
                  {en('scriptBackupRestoreFull')}
                </Button>
              </>
            )}
          </li>
        ))}
      </ul>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}
