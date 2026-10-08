import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { backendCodeMsg, type PreparationReport } from '@/lib/api'
import { formatMessage, t as translate } from '@/i18n'
import { prepView, targetName, type PrepPrimary, type PrepView } from '@/lib/preparationText'
import { addRuntimePanelToCanvas, openFastEdit } from '@/store/workspace'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useEnvStore } from '@/store/envStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import {
  draftDiffers,
  scriptTarget,
  useProjectPreparationStore,
  type PrepEntry,
} from '@/store/projectPreparationStore'
import { useRenderStore } from '@/store/renderStore'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useUiStore } from '@/store/uiStore'
import { ProbeResultsDialog } from './left/ScriptLibrary'
import { ScriptArgvEditor } from './ScriptArgvEditor'
import { ScriptInputActions, ScriptInputFields, useScriptInputAnswer } from './ScriptInputForm'
import { TaskDiagnostic } from './TaskDiagnostic'
import { Button, IconButton } from './ui/Button'
import { Details, Summary } from './ui/Details'

const pt = (key: string, values?: Record<string, unknown>) =>
  translate(`prep.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 展示面的名字（`scriptInputStore.claimPresentation`）：面板挂着且正展示那一问时，原对话框让开 */
const SURFACE = 'prep-panel'

/** 加进画布的图首次编辑渲染好了没有：渲染态里那张图有了精确 manifest（这是前端自己观察到的事实，不是后端判据） */
function useEditReady(ids: string[]): boolean {
  return useRenderStore((s) =>
    ids.some((id) => Object.values(s.byKey).some((r) => r.fileId === id && r.status === 'ready' && r.manifest !== null)),
  )
}

/**
 * 准备面板（T09，ADR 0116）：所有「准备并打开」的 GUI 入口共用的**一个**可恢复展示面。
 *
 * ```text
 * plot.py 已经可以运行。                                  [确认并运行]  ×
 * ▸ 详情
 * ```
 *
 * * **一句话 + 至多一个主按钮**（用户硬性要求「卡片一句话读懂」）：句子与主按钮只在 `lib/preparationText.ts`；
 *   检查项、影响、参数、诊断、次要动作全在默认收起的「详情」里。
 * * **按钮就是真实动作**：确认并运行 / 准备依赖 / 使用这个环境 / 选择运行目录 / 指认数据 / 回答并继续 / 进入编辑——
 *   全部来自后端报告里的动作与待办；答题走既有端点（采用环境 / 运行目录 / 指认数据由原对话框作答，作答后会话只读地
 *   重新检查），面板不复制任何一个动作的实现。
 * * **运行时 input 在面板里答**：面板开着且正展示同一问时认领展示面（原对话框让开）；关掉面板就放手，原对话框接着
 *   显示同一问——只换展示，不取消脚本。
 * * **关闭只改呈现**：后台的执行 / 安装不停，订阅照旧；「停止」是会话的取消动作，按 owner 只退役本会话的工作。
 * * **进入编辑用这次捕获的图**：`report.captured` 直接加进画布，热会话重画，不为换界面再跑脚本；
 *   「已进入编辑」只在那张图的编辑渲染真的可用之后才说。
 */
export function PreparationPanel() {
  useTranslation('workspace')
  const open = useUiStore((s) => s.preparationOpen)
  const focus = useProjectPreparationStore((s) => s.focus)
  const entry = useProjectPreparationStore((s) => (s.focus ? s.entries[s.focus] : undefined))
  // 草稿变了要重新渲染（参数改了 → 「按新参数检查」）
  useScriptArgvStore((s) => s.drafts)
  const editReady = useEditReady(entry?.editing ?? [])
  if (!open || !focus || !entry) return null
  const argsChanged = draftDiffers(entry.target)
  const v = prepView(entry, { argsChanged, editReady })
  return <PanelBody entry={entry} view={v} />
}

function PanelBody({ entry, view }: { entry: PrepEntry; view: PrepView }) {
  useTranslation(['workspace', 'errors'])
  const report = entry.report
  const [localError, setLocalError] = useState<string | null>(null)
  const [resultsOpen, setResultsOpen] = useState(false)
  // 报告换了（新修订 / 新观察）就把上一次本地动作的失败收起
  useEffect(() => setLocalError(null), [report?.config_revision, report?.observation_seq])
  const script = targetName(entry)
  const store = useProjectPreparationStore.getState()

  const runPrimary = async (p: PrepPrimary) => {
    setLocalError(null)
    switch (p.kind) {
      case 'action':
        await store.act(entry.key, p.action)
        return
      case 'adopt': {
        const err = await useEnvStore.getState().adoptCandidate(p.candidate, script)
        if (err) setLocalError(err)
        // 成功：envStore 的环境变了 → 会话只读地重新检查（store 订阅），下一步仍由报告给出
        return
      }
      case 'workdir':
        // 原对话框作答（同一份载荷、同一次 PATCH）；答完会话重新检查
        useEnvStore.getState().requestWorkdirConfirmation(p.payload, entry.pj)
        return
      case 'missing_input':
        useEnvStore.getState().requestMissingInput(p.payload, entry.pj)
        return
      case 'enter_edit': {
        const figures = report?.captured ?? []
        if (figures.length !== 1) {
          setResultsOpen(true) // 多张图：每一张都列出来、各自加进画布（绝不只取第一张）
          return
        }
        const d = figures[0]
        // 进入编辑 = 稳定动作 `openFastEdit`（加进文档并说出口 → 进入图内编辑 → 引擎按热会话渲染，脚本不再跑）。
        // 素材清单还没取到这张新图时先用这次捕获的描述符把它加进画布，再进入编辑
        await useRuntimeAssetStore.getState().loadAssets()
        if (!(useRuntimeAssetStore.getState().assets ?? []).some((a) => a.id === d.asset_id)) {
          addRuntimePanelToCanvas(d)
        }
        openFastEdit(d.asset_id)
        store.noteEditing(entry.key, d.asset_id)
        return
      }
      case 'reopen':
        await store.open('id' in entry.target ? entry.target : scriptTarget(entry.target.script))
        return
      case 'open_environment':
        useUiStore.getState().setEngineEnvOpen(true)
        return
      case 'open_registry':
        useProjectReadinessStore.getState().openCenter({ source: 'panel' })
        return
    }
  }

  const sentence = localError ?? pt(`line.${view.sentence.key}`, view.sentence.values)

  return (
    <section
      data-preparation-panel
      data-prep-state={localError ? 'action_failed' : view.state}
      data-prep-phase={report?.phase ?? ''}
      data-prep-session={report?.session_id ?? ''}
      data-prep-connection={entry.connection}
      aria-label={pt('aria', { script })}
      className="flex flex-col gap-1.5 border-b border-border bg-surface px-3 py-2 text-xs text-ink-2"
    >
      <div className="flex items-center gap-2">
        <p className="min-w-0 flex-1 truncate text-ink" data-prep-line aria-live="polite" title={sentence}>
          {sentence}
        </p>
        {view.primary && (
          <Button
            variant="primary"
            size="sm"
            className="shrink-0"
            data-prep-primary={primaryId(view.primary)}
            loading={entry.pending !== null}
            disabled={entry.pending !== null}
            onClick={() => void runPrimary(view.primary!)}
          >
            {primaryLabel(view.primary)}
          </Button>
        )}
        <IconButton
          iconSize="sm"
          label={pt('close')}
          data-prep-close
          className="shrink-0 text-ink-3"
          onClick={() => useUiStore.getState().setPreparationOpen(false)}
        >
          <X size={ICON_SIZE.sm} />
        </IconButton>
      </div>
      {view.input && report?.runtime_input && <EmbeddedInput requestId={report.runtime_input.id} />}
      {view.argsOpen && !('id' in entry.target) && <ScriptArgvEditor script={entry.target.script} />}
      <PanelDetails entry={entry} view={view} />
      {report && (report.captured ?? []).length > 1 && (
        <ProbeResultsDialog
          script={script}
          descriptors={report.captured ?? []}
          dropped={0}
          open={resultsOpen}
          onOpenChange={setResultsOpen}
          onAdded={(d) => useProjectPreparationStore.getState().noteEditing(entry.key, d.asset_id)}
        />
      )}
    </section>
  )
}

const primaryId = (p: PrepPrimary): string => (p.kind === 'action' ? p.action : p.kind)

function primaryLabel(p: PrepPrimary): string {
  switch (p.kind) {
    case 'action':
      return pt(`btn.${p.label}`)
    case 'adopt':
      return pt('btn.useEnvironment')
    case 'workdir':
      return pt('btn.chooseWorkdir')
    case 'missing_input':
      return pt('btn.pointAtData')
    case 'enter_edit':
      return p.count > 1 ? pt('btn.viewFigures', { count: p.count }) : pt('btn.enterEdit')
    case 'reopen':
      return pt(p.label === 'checkArgs' ? 'btn.checkArgs' : 'btn.retry')
    case 'open_environment':
      return pt('btn.openEnvironment')
    case 'open_registry':
      return pt('btn.openRegistry')
  }
}

/**
 * 正在等的那一问：面板展示期间认领展示面，原对话框让开；卸载（关面板 / 问题答完 / 换了一问）就放手。
 * 只在队首就是报告里的那一问时才认领——同一请求只有一个展示面，别的脚本的问照旧由原对话框展示。
 */
function EmbeddedInput({ requestId }: { requestId: string }) {
  const answer = useScriptInputAnswer()
  const mine = answer.head?.id === requestId
  useEffect(() => {
    if (!mine) return
    const s = useScriptInputStore.getState()
    s.claimPresentation(SURFACE)
    return () => useScriptInputStore.getState().releasePresentation(SURFACE)
  }, [mine])
  if (!mine) return null
  return (
    <div className="flex flex-col gap-2 rounded-sm bg-surface-2 p-2" data-prep-input>
      <ScriptInputFields answer={answer} />
      <div className="flex items-center gap-2">
        <ScriptInputActions answer={answer} />
      </div>
    </div>
  )
}

const CHECK_IDS = ['target', 'environment', 'workdir', 'dependencies', 'data', 'arguments'] as const

/** 默认收起的「详情」：检查项、要装什么、参数、失败原文与那一次的诊断、次要动作 */
function PanelDetails({ entry, view }: { entry: PrepEntry; view: PrepView }) {
  useTranslation(['workspace', 'errors'])
  const report = entry.report
  const store = useProjectPreparationStore.getState()
  const primary = view.primary && view.primary.kind === 'action' ? view.primary.action : null
  const secondary = (['recheck', 'cancel'] as const).filter(
    (k) => k !== primary && report?.actions.some((a) => a.kind === k),
  )
  const prepare = report?.actions.find((a) => a.kind === 'prepare_dependencies')
  const env = report?.requirements.find((r) => r.kind === 'environment_choice')
  const error = report?.result?.error
  const attempt = report?.provider.attempt_id
  const dep = report?.provider.dependency
  return (
    <Details data-prep-details>
      <Summary className="type-meta">{pt('details')}</Summary>
      <div className="mt-1.5 flex flex-col gap-1.5">
        {report && (
          <ul className="flex flex-col gap-0.5" data-prep-checks>
            {CHECK_IDS.map((id) => {
              const c = report.checks.find((x) => x.id === id)
              if (!c) return null
              return (
                <li key={id} className="flex items-center gap-2" data-prep-check={id} data-status={c.status}>
                  <span className="min-w-0 flex-1 truncate text-ink">{pt(`check.${id}`)}</span>
                  <span className="shrink-0 text-ink-3">{pt(`status.${c.status}`)}</span>
                </li>
              )
            })}
          </ul>
        )}
        {report?.facts && <Facts report={report} editing={entry.editing.length > 0} />}
        {prepare && (prepare.impact.installs?.length ?? 0) > 0 && (
          <div data-prep-impact className="flex flex-col gap-0.5">
            <span>{pt('impact.installs', { list: (prepare.impact.installs ?? []).join(', ') })}</span>
            <span>
              {pt(prepare.impact.modifies_user_environment ? 'impact.userEnvironment' : 'impact.managed')}
            </span>
          </div>
        )}
        {env && (
          <div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void useEnvStore.getState().setProjectPython(null)}
            >
              {pt('btn.useBuiltin')}
            </Button>
          </div>
        )}
        {!view.argsOpen && !('id' in entry.target) && <ScriptArgvEditor script={entry.target.script} />}
        {entry.restarted && <p data-prep-restarted>{pt('restartedNote')}</p>}
        {entry.rejection && (
          <p data-prep-rejection={entry.rejection.code}>
            {formatMessage(backendCodeMsg(entry.rejection.code, entry.rejection.params, ''))}
          </p>
        )}
        {entry.failure && (
          <p className="text-danger">
            {formatMessage(backendCodeMsg(entry.failure.code, entry.failure.params, entry.failure.message))}
          </p>
        )}
        {error?.code && report?.phase !== 'completed' && (
          <p className="text-danger" data-prep-error={error.code}>
            {formatMessage(backendCodeMsg(error.code, error as Record<string, unknown>, error.message ?? ''))}
          </p>
        )}
        {/* 那一次的诊断（T04）：失败尝试的冻结快照，取代旧的「复制诊断」（报错原文进剪贴板） */}
        {attempt && report?.outcome.kind === 'failed' && report.outcome.reason !== 'dependency_preparation' && (
          <TaskDiagnostic key={attempt} kind="preparation" refId={attempt} folded={false} />
        )}
        {dep && report?.outcome.reason === 'dependency_preparation' && (
          <TaskDiagnostic key={dep.plan_id} kind="dependency" refId={dep.plan_id} folded={false} />
        )}
        {secondary.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {secondary.map((k) => (
              <Button
                key={k}
                variant="secondary"
                size="sm"
                data-prep-secondary={k}
                disabled={entry.pending !== null}
                onClick={() => void store.act(entry.key, k)}
              >
                {pt(k === 'cancel' ? 'btn.stop' : 'btn.recheck')}
              </Button>
            ))}
          </div>
        )}
      </div>
    </Details>
  )
}

/** 执行结束 / 捕获到图 / 首次编辑渲染：三件事分开说（`null` = 不适用，不说） */
function Facts({ report, editing }: { report: PreparationReport; editing: boolean }) {
  const { execution_finished: finished, figure_captured: captured } = report.facts
  if (finished === null && captured === null) return null
  return (
    <ul className="flex flex-col gap-0.5" data-prep-facts>
      {finished !== null && <li data-fact="execution_finished" data-value={String(finished)}>{pt(finished ? 'fact.finished' : 'fact.notFinished')}</li>}
      {captured !== null && <li data-fact="figure_captured" data-value={String(captured)}>{pt(captured ? 'fact.captured' : 'fact.notCaptured')}</li>}
      {editing && <li data-fact="first_edit_ready">{pt('fact.editRequested')}</li>}
    </ul>
  )
}
