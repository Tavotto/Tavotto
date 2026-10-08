import { useEffect, useRef, useState } from 'react'
import { captureProjectEpoch, type ProjectEpochGuard } from '@/lib/projectEpoch'
import { useTranslation } from 'react-i18next'
import { Ban, Copy, CornerDownLeft, Play, SearchX, Settings, Square } from '@/components/ui/icons'
import { listRowClass } from '@/components/ui/listRow'
import { cn } from '@/lib/utils'
import { Details, Summary } from '@/components/ui/Details'
import { ICON_SIZE } from '@/components/ui/Icon'
import { backendCodeMsg, type CapturedFigureDescriptor, type ScriptInventoryEntry } from '@/lib/api'
import { formatCm } from '@/lib/units'
import { formatMessage, msg, t as translate } from '@/i18n'
import { addRuntimePanelToCanvas } from '@/store/workspace'
import type { PanelObject } from '@/types/document'
import { preparationPanelEnabled } from '@/lib/preparationFlag'
import { prepRowKey } from '@/lib/preparationText'
import {
  scriptTarget,
  useProjectPreparationStore,
  type PrepEntry,
} from '@/store/projectPreparationStore'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import {
  isBusyPhase,
  isGatePhase,
  needsNative,
  useScriptRunStore,
  type ScriptRunState,
} from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { ScriptArgvEditor } from '../ScriptArgvEditor'
import { TaskDiagnostic } from '../TaskDiagnostic'
import { EmptyState } from '../ui/EmptyState'
import { MenuItem, MenuSeparator } from '../ui/Menu'
import { Notice } from '../ui/Notice'
import { RowMenu } from '../ui/RowMenu'
import { useRowMenu } from '../ui/useRowMenu'
import { useAssetStore } from '@/store/assetStore'
import { useProjectStore } from '@/store/projectStore'
import { DependencyRepairCard } from '../DependencyRepairCard'
import { useDepRepairStore, type ScriptRepairOffer } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { WorkdirChooseButton } from '../WorkdirRow'

/**
 * 素材库「脚本」区（Session 5，普通入口）：项目里每个合理 .py 一行，
 * 「运行并发现图」在这里，不再埋在 RegistryDialog（那边继续做冲突裁决 /
 * 手工 stem / 高级诊断）。数据三来源：清单（scriptLibraryStore，
 * `/api/registry` 的 all_scripts）、注册表（同一响应的 scripts 表，
 * 已关联数量）、运行状态机（scriptRunStore）。
 *
 * 文案纪律：不默认暴露 stem / registry / probe 这类内部术语——状态用
 * 人话（「尚未运行」「输出名称只能在运行后确定」），高级详情可折叠。
 */
const sc = (key: string, values?: Record<string, unknown>) =>
  translate(`scripts.${key}`, { ns: 'workspace', ...(values ?? {}) })


type Group = 'needsFix' | 'linked' | 'notRun' | 'runtimeNames' | 'needsEnv' | 'infra'
const GROUP_ORDER: Group[] = ['needsFix', 'linked', 'notRun', 'runtimeNames', 'needsEnv', 'infra']

function groupOf(entry: ScriptInventoryEntry, run: ScriptRunState | undefined): Group {
  // 缺包是能一键修好的那一类：单独一组「需要修复」、排在最前（2026-09-29：「可能需要原环境」对不懂 Python 的
  // 用户是术语）。超时与一般失败仍在下面那组——它们真的可能与原来的环境 / 运行方式有关
  // 「开跑前要先准备依赖」（联合准备的授权）同样能一键修好：与缺包同一组
  if (run?.phase === 'missing_dependency' || run?.phase === 'needs_preparation') return 'needsFix'
  // 本会话 safe 运行失败且形状像环境问题的，收进「可能需要原环境」——
  // 恢复路径文案（总纲 §四）挂在组上，一眼看全
  if (needsNative(run)) return 'needsEnv'
  if (entry.registered) return 'linked'
  if (entry.reason === 'infrastructure') return 'infra'
  if (entry.reason === 'dynamic_stems' || entry.reason === 'unparseable') return 'runtimeNames'
  return 'notRun' // static_candidate / no_static_output
}

export function ScriptLibrary({ query }: { query: string }) {
  useTranslation('workspace')
  const view = useScriptLibraryStore((s) => s.view)
  const loading = useScriptLibraryStore((s) => s.loading)
  const loaded = useScriptLibraryStore((s) => s.loaded)
  const error = useScriptLibraryStore((s) => s.error)
  const runStates = useScriptRunStore((s) => s.byScript)

  const answersLoaded = useScriptInputStore((s) => s.answers !== null)
  const repairOwner = useRepairOwner()

  useEffect(() => {
    if (!loaded) void useScriptLibraryStore.getState().load()
  }, [loaded])
  // 脚本 input() 记住的答案（ADR 0099）：有答案的脚本行才出现「记住的输入」入口
  useEffect(() => {
    if (!answersLoaded) void useScriptInputStore.getState().loadAnswers()
  }, [answersLoaded])

  const q = query.trim().toLowerCase()
  const scripts = (view?.all_scripts ?? []).filter(
    (s) => !q || s.script.toLowerCase().includes(q),
  )
  // 同一个包缺在好几个脚本上：只挂**一张**修复卡（装进项目的环境，一次就修好全部；装好后
  // `depRepairStore` 把同样缺它的那几行都重跑）。修复进行中的那一行优先，其余按列表顺序取第一行
  const repairCards = new Set<string>()
  const seenModules = new Set<string>()
  const ordered = [...scripts].sort(
    (a, b) => Number(b.script === repairOwner.owner) - Number(a.script === repairOwner.owner),
  )
  for (const entry of ordered) {
    const found = rowRepairOffer(entry.script, runStates[entry.script], repairOwner)
    if (!found || seenModules.has(found.module)) continue
    seenModules.add(found.module)
    repairCards.add(entry.script)
  }

  const groups = new Map<Group, ScriptInventoryEntry[]>()
  for (const entry of scripts) {
    const g = groupOf(entry, runStates[entry.script])
    const list = groups.get(g)
    if (list) list.push(entry)
    else groups.set(g, [entry])
  }

  // 三种「没有行」各有自己的形态（2026-10-07 设计审计 §10.3：此前都是一行裸字）：读不出来是一条 Notice、
  // 还在读是静态骨架（加载四种写法之一）、筛不到是 EmptyState
  if (error && !view) {
    return (
      <div className="px-3 pb-2">
        <Notice tone="danger" data-script-load-failed>
          {sc('loadFailed', { error })}
        </Notice>
      </div>
    )
  }
  if (!view) {
    return loading ? <ScriptSkeleton /> : null
  }
  if (scripts.length === 0) {
    return q ? (
      <EmptyState icon={SearchX} title={sc('noMatch')} data-script-no-match />
    ) : (
      <div className="px-3">
        <EmptyState icon={Play} title={sc('emptyTitle')} />
      </div>
    )
  }

  return (
    <div className="flex flex-col px-2 pb-2">
      {/* 五个组同一种组头：名字 + meta 数字（2026-09-15 打磨批次 G）。「工具与配置脚本」
          此前是可折叠的 Details——同一列表两种组头；它已排最后、通常只有一两项，不值得一副
          折叠骨架（左栏审计 L07）。组名 `px-1` 与卡片、搜索框落在同一条竖线上（L05） */}
      {GROUP_ORDER.filter((g) => groups.has(g)).map((g) => {
        const label = g === 'infra' ? sc('groupInfraName') : sc(`group_${g}`)
        return (
          <section key={g} className="mt-1">
            {/* 组头与左栏其它子节头同一种写法：28px · type-section 名字 + type-meta 计数（审计 §10.3） */}
            <h4 className="flex h-7 items-center gap-1.5 px-1" data-script-group={g}>
              <span className="type-section">{label}</span>
              <span className="type-meta">{groups.get(g)!.length}</span>
            </h4>
            <ul aria-label={label}>
              {groups.get(g)!.map((entry) => (
                <ScriptRow
                  key={entry.script}
                  entry={entry}
                  stems={view.scripts[entry.script]?.stems ?? []}
                  repairCard={repairCards.has(entry.script)}
                />
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}

/** 读清单时的静态骨架：与真实的行同高（28），不呼吸 */
function ScriptSkeleton() {
  return (
    <div className="flex flex-col px-2 pb-2" aria-busy="true" data-script-loading>
      <span role="status" className="sr-only">
        {sc('loading')}
      </span>
      {[62, 48, 70].map((w, i) => (
        <span key={i} aria-hidden className="mx-1 flex h-7 items-center gap-1.5 pl-1.5">
          <span className="size-1.5 shrink-0 rounded-full bg-surface-hover opacity-65" />
          <span className="ml-1 h-2.5 rounded-xs bg-surface-hover opacity-65" style={{ width: `${w}%` }} />
        </span>
      ))}
    </div>
  )
}

/**
 * 一行脚本下面的「第二行」：所有恢复 / 补救入口（一键修复卡、跑前准备、运行目录、缺数据、失败详情）
 * 都是这一行的延续，**同一种缩进、同一种间距**（2026-10-07 设计审计 §10.3：此前五种写法）——
 * 缩进到文件名那一列（状态点列 + 间距），不套框。
 */
function SecondRow({ children, ...rest }: { children: React.ReactNode } & Record<`data-${string}`, string | boolean | undefined>) {
  return (
    <div {...rest} className="flex flex-wrap items-center gap-1.5 pb-1.5 pl-8 pr-2">
      {children}
    </div>
  )
}

/** 项目根 + 相对脚本名 → 绝对路径；根是 Windows 写法（只有反斜杠）时整条按反斜杠拼 */
function joinProjectPath(root: string, rel: string): string {
  const win = root.includes('\\') && !root.includes('/')
  const sep = win ? '\\' : '/'
  return `${root.replace(/[\\/]+$/, '')}${sep}${win ? rel.replace(/\//g, '\\') : rel}`
}

/**
 * 一行脚本（Tavotto File Row）：状态点 | 文件名 | 状态一句话 | 运行图标钮。
 *
 *   ● plot.py            已关联 2 张图     ▶
 *   ○ analyze.py         这个脚本尚未运行   ▶
 *
 * 28px 一行，与树行、列表行同一种外观（`listRowClass`）。运行 / 取消是**同一个
 * 按钮**（busy 态翻转成取消）：取消后焦点自然留在原脚本行的这个按钮上，不需要
 * 任何焦点搬运。它常驻但常态是 ink-3，行 hover 时才与文字同色——它是这一行唯一
 * 的操作，不该比文件名更响。状态那一段 aria-live=polite——只在相位变化时更新
 * 一次，不高频播报。
 */
function ScriptRow({
  entry,
  stems,
  repairCard,
}: {
  entry: ScriptInventoryEntry
  stems: string[]
  /** 这一行挂修复卡（同一个包缺在几行上时只有一行挂，见 `ScriptLibrary`） */
  repairCard: boolean
}) {
  useTranslation('workspace')
  const run = useScriptRunStore((s) => s.byScript[entry.script])
  const prep = useProjectPreparationStore((s) => s.entries[`script:${entry.script}`])
  const busy = !!run && isBusyPhase(run.phase)
  const [resultsOpen, setResultsOpen] = useState(false)
  const hasAnswers = useScriptInputStore((s) => (s.answers?.[entry.script]?.length ?? 0) > 0)
  // T09（ADR 0116）：默认这颗钮打开准备面板（后端会话：检查 → 确认 → 运行 → 进入编辑），不直接执行；
  // 本地开关关掉时回到旧的同步试运行（保留一版）
  const viaPanel = preparationPanelEnabled() && !busy
  // 脚本名相对项目根：根以项目状态（`/api/project` 的 figures_dir）为准，不等素材清单——/api/panels
  // 失败时 assetStore.figuresDir 是空的，而脚本行照样在（Codex #832）；两边都不知道就不给「复制路径」
  const projectRoot = useProjectStore((s) => s.project?.figures_dir)
  const assetRoot = useAssetStore((s) => s.figuresDir)
  const root = projectRoot || assetRoot
  // ⋯ / 右键 / ⇧F10 同一份菜单（`ui/RowMenu`，审计 §10.3）
  const menu = useRowMenu()

  const onRunOrCancel = () => {
    if (viaPanel) {
      void useProjectPreparationStore.getState().open(scriptTarget(entry.script))
      return
    }
    const store = useScriptRunStore.getState()
    if (busy) store.cancel(entry.script)
    else void store.run(entry.script)
  }
  const path = root ? joinProjectPath(root, entry.script) : null
  const copyPath = () => {
    if (!path) return
    const failed = () => useUiStore.getState().setStatus(msg('scripts.pathCopyFailed', { path }, 'workspace'), 'error')
    // 非安全上下文 / 某些 WebView 里没有 `navigator.clipboard`：照样说「没复制成、路径在这」，不静默（Codex #832）
    if (typeof navigator.clipboard?.writeText !== 'function') return failed()
    void Promise.resolve()
      .then(() => navigator.clipboard.writeText(path))
      .then(() => useUiStore.getState().setStatus(msg('scripts.pathCopied', { path }, 'workspace'), 'done'), failed)
  }

  return (
    <li className="flex flex-col" data-script-row={entry.script}>
      <div {...menu.rowProps} className={cn(listRowClass(), 'gap-1.5 pl-1.5 pr-0.5')}>
        <StatusDot entry={entry} run={run} prep={prep} />
        {/* 脚本名是这一行的主文字：等宽（路径 / 脚本名那一档）但字号跟正文走 12，
            与右侧 11px 的状态一句话差一个台阶（左栏审计 L02） */}
        <span
          className="min-w-0 flex-1 truncate font-mono text-sm text-ink"
          title={entry.script}
        >
          {entry.script}
        </span>
        <StatusLine
          entry={entry}
          stems={stems}
          run={run}
          prep={prep}
          onViewResults={() => setResultsOpen(true)}
        />
        {/* 脚本 input() 记住的答案（ADR 0099）：只在这个脚本真有答案时出现，其余行一个像素不变 */}
        {hasAnswers && (
          <IconButton
            iconSize="sm"
            label={translate('scriptInput.manageAria', { ns: 'dialogs', script: entry.script })}
            tip={translate('scriptInput.manageTip', { ns: 'dialogs' })}
            data-script-answers={entry.script}
            onClick={() => useScriptInputStore.getState().openManager(entry.script)}
            className="text-ink-3 group-hover:text-ink focus-visible:text-ink"
          >
            <CornerDownLeft size={ICON_SIZE.sm} />
          </IconButton>
        )}
        <IconButton
          iconSize="sm"
          label={
            busy
              ? sc('cancelAria', { script: entry.script })
              : viaPanel
                ? sc('prepareAria', { script: entry.script })
                : sc(entry.registered ? 'rerunAria' : 'runAria', { script: entry.script })
          }
          tip={
            busy
              ? sc(run?.cancelRequested ? 'cancelling' : 'cancel')
              : viaPanel
                ? sc('prepareAria', { script: entry.script })
                : sc(entry.registered ? 'rerun' : 'run')
          }
          disabled={!!run?.cancelRequested}
          data-script-run={entry.script}
          onClick={onRunOrCancel}
          className={cn(!busy && 'text-ink-3 group-hover:text-ink focus-visible:text-ink')}
        >
          {busy ? <Square size={ICON_SIZE.sm} /> : <Play size={ICON_SIZE.sm} />}
        </IconButton>
        <RowMenu state={menu} label={sc('rowActions', { script: entry.script })} width={200} data-script-menu={entry.script}>
          <MenuItem
            icon={busy ? Square : Play}
            disabled={!!run?.cancelRequested}
            onSelect={onRunOrCancel}
          >
            {busy ? sc('cancel') : sc(entry.registered ? 'rerun' : 'run')}
          </MenuItem>
          {hasAnswers && (
            <MenuItem
              icon={CornerDownLeft}
              onSelect={() => useScriptInputStore.getState().openManager(entry.script)}
            >
              {translate('scriptInput.manageTip', { ns: 'dialogs' })}
            </MenuItem>
          )}
          <MenuSeparator />
          {path && (
            <MenuItem icon={Copy} data-script-copy-path onSelect={copyPath}>
              {sc('copyPath')}
            </MenuItem>
          )}
        </RowMenu>
      </div>

      {repairCard && <ScriptDependencyRepair script={entry.script} run={run} />}
      <ScriptPreparation script={entry.script} run={run} />
      <GateReopen run={run} />
      <FailureRecovery script={entry.script} run={run} />
      <MissingInputRecovery run={run} />

      {run && run.descriptors.length > 0 && (
        <ProbeResultsDialog
          script={entry.script}
          descriptors={run.descriptors}
          dropped={run.droppedFigures}
          open={resultsOpen}
          onOpenChange={setResultsOpen}
        />
      )}
    </li>
  )
}

/**
 * 脚本缺包时的「一键装上」（ADR 0019）：与画布上那张修复卡片是**同一张**（同一个 store、同一次授权、
 * 同一个后端 offer——`probe._error_from_worker` 挂的就是 `deprepair.offer()`）。新脚本的图还没上画布时，
 * 右栏的卡片不会出现，这里是新用户走到安装的唯一入口（2026-09-28 实测：只有「选择渲染环境」「复制诊断」）。
 * 装好后 `depRepairStore` 把这一行的运行重跑一遍（`rerunScriptAfterRepair`）。
 *
 * 修复状态是全局一份：别的脚本（或画布上那张卡）正在装 / 刚装完时，这一行不显示卡片，免得同一份进度
 * 出现在好几行上。
 */
function ScriptDependencyRepair({ script, run }: { script: string; run: ScriptRunState | undefined }) {
  const found = rowRepairOffer(script, run, useRepairOwner())
  if (!found) return null
  return (
    // 与下面的恢复说明同一列缩进：它是这一行的延续，不是另一块区域
    <SecondRow data-script-dependency-repair>
      <div className="min-w-0 flex-1">
        <DependencyRepairCard
          offer={found.offer}
          module={found.module}
          script={found.offer.script || script}
          fromScriptRow
        />
      </div>
    </SecondRow>
  )
}

/**
 * 跑前缺环境直接弹 `DependencyPrepareDialog`（用户 2026-10-03）：授权、进度、取消与重试都只在框里。
 * 「稍后」之后这一行保留再打开的入口；切项目后载荷随作业停放，切回仍能打开同一份进度。
 */
function ScriptPreparation({ script, run }: { script: string; run: ScriptRunState | undefined }) {
  useTranslation('errors')
  const progress = useDepRepairStore((s) => s.progress)
  const jointScript = useDepRepairStore((s) => s.jointScript)
  const jointOffer = useDepRepairStore((s) => s.jointOffer)
  const mine = jointScript === script
  // 载荷：这一行那次运行留下的；切项目再切回后运行记录已清空，就用随作业停放 / 放回的那份（进度、取消、重试不需要再点运行）
  const offer = (run?.phase === 'needs_preparation' ? run.error?.dependency_preparation : undefined) ?? (mine && progress?.flow === 'joint' && progress.state !== 'done' ? jointOffer : null)
  if (!offer) return null
  return (
    <SecondRow data-script-preparation>
      <Button
        variant="secondary"
        size="sm"
        data-script-preparation-fix
        onClick={() => useEnvStore.getState().requestDependencyPreparation(offer)}
      >
        {translate('engine.oneClickRepair', { ns: 'errors' })}
      </Button>
    </SecondRow>
  )
}

/** 修复状态里「这张卡属于谁」的那几样（全局一份）；列表与每一行用同一份判据 `rowRepairOffer` */
interface RepairOwner {
  owner: string
  heldFor: string
  held: ScriptRepairOffer | null
}

function useRepairOwner(): RepairOwner {
  const owner = useDepRepairStore((s) => s.request?.script ?? s.progress?.script ?? '')
  const heldFor = useDepRepairStore((s) => s.request?.script ?? s.scriptOffer?.script ?? '')
  const held = useDepRepairStore((s) => s.scriptOffer)
  return { owner, heldFor, held }
}

/**
 * 这一行此刻挂不挂修复卡、挂哪份 offer。列表（组上那段解释说不说）与行（卡片、恢复说明）共用这一处判据——
 * 两处各判一遍的话，总有一种状态下卡片与那段解释同时出现或同时消失。
 */
function rowRepairOffer(
  script: string,
  run: ScriptRunState | undefined,
  { owner, heldFor, held }: RepairOwner,
): ScriptRepairOffer | null {
  // 从这一行发起、随作业收放的那份 offer（#729）：A → B → A 之后 `scriptRunStore` 已被清空，这一行自己的
  // 运行里没有 offer 了，靠它把进度 / 取消 / 重试挂回这一行。属于别的脚本的不认
  const fresh = run?.phase === 'missing_dependency' ? run.error?.dependency_repair : undefined
  const mine = heldFor === script ? held : null
  const offer = fresh ?? mine?.offer
  const module = fresh ? String(run?.error?.params?.module ?? fresh.import_name ?? '') : (mine?.module ?? '')
  if (!offer || !module) return null
  if (owner && owner !== script) return null
  return { offer, module }
}

/**
 * 起会话之前那两道门（U03 运行目录 / U04 依赖准备）的「再打开」：试运行撞上门时 `scriptRunStore` 已经把
 * 载荷交给 `envStore` 弹了框；用户点了「稍后」，这一行不能停在一句话上无路可走——与画布错误块同一颗按钮
 * （`DependencyPrepareButton` / `WorkdirChooseButton`），载荷就是这一行那次运行留下的。作答之后由作答的
 * 那一方重跑这一行（`scriptRunStore.rerunGated`）。
 */
function GateReopen({ run }: { run: ScriptRunState | undefined }) {
  // 依赖门的再打开入口在 `ScriptPreparation`，这里是运行目录门。
  if (run?.phase === 'needs_workdir' && run.error?.confirmation) {
    return (
      <SecondRow data-script-workdir-choose>
        <WorkdirChooseButton confirmation={run.error.confirmation} />
      </SecondRow>
    )
  }
  return null
}

/**
 * 行首的状态点（6px，坐在 16px 列里）：实心 = 已关联；空心 = 还没跑过；
 * accent = 正在跑（点是静止的，「在动」由旁边那句话的 shimmer 说——2026-10-07 设计审计 §10.3：
 * 此前是一颗转圈 / 呼吸点，与 shimmer 两处同时在动）；红 = 这次失败。
 * 纯装饰——状态本身由旁边那句话与可达名说出。
 */
function StatusDot({
  entry,
  run,
  prep,
}: {
  entry: ScriptInventoryEntry
  run: ScriptRunState | undefined
  prep?: PrepEntry
}) {
  const phase = run?.phase ?? 'idle'
  const prepPhase = prep?.report?.phase
  const running =
    phase === 'starting_runtime' ||
    phase === 'running' ||
    prepPhase === 'running' ||
    prepPhase === 'awaiting_runtime_input' ||
    prepPhase === 'preparing_environment'
  // 停在门上不是失败（缺的是一个决定），不标红
  const failed = !running && !!run?.error && !isGatePhase(phase)
  return (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center" aria-hidden data-script-dot={running ? 'running' : undefined}>
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          running
            ? 'bg-accent'
            : failed
              ? 'bg-danger'
              : entry.registered
                ? 'bg-ink-2'
                : 'border border-ink-faint',
        )}
      />
    </span>
  )
}

/**
 * 状态一句话（元数据档，靠右、单行截断）：不暴露内部术语，错误按稳定 code
 * 翻成当前语言。发现了图时那句话本身就是「查看捕获结果」的入口。
 */
function StatusLine({
  entry,
  stems,
  run,
  prep,
  onViewResults,
}: {
  entry: ScriptInventoryEntry
  stems: string[]
  run: ScriptRunState | undefined
  prep?: PrepEntry
  onViewResults: () => void
}) {
  useTranslation('workspace')
  const phase = run?.phase ?? 'idle'
  // 准备会话（T09）在这一行上：只翻译它的 phase（`prepRowKey`），点一下回到面板；旧试运行的状态机此刻不在跑
  const prepKey = !isBusyPhase(phase) ? prepRowKey(prep) : null

  let body: React.ReactNode = null
  let title: string | undefined
  if (prepKey && prep) {
    body = (
      <button
        data-script-prep-status={prep.report?.phase ?? 'checking'}
        onClick={() => {
          useProjectPreparationStore.setState({ focus: prep.key })
          useUiStore.getState().setPreparationOpen(true)
        }}
        className="max-w-full truncate rounded-xs text-ink-2 underline-offset-2 outline-none hover:text-ink hover:underline focus-visible:focus-ring"
      >
        {translate(`prep.row.${prepKey.key}`, { ns: 'workspace', ...prepKey.values })}
      </button>
    )
  } else if (phase === 'starting_runtime' || phase === 'running') {
    // 「在动」的唯一记号：shimmer 扫过这句话（加载四种写法之一）
    body = <span className="text-shimmer" data-script-running>{sc(phase === 'running' ? 'running' : 'starting')}</span>
  } else if (phase === 'captured_one' || phase === 'captured_many') {
    body = (
      // 可见的是结果本身（「已发现 3 张图」），动作名「查看捕获结果」给读屏与气泡
      <button
        onClick={onViewResults}
        className="max-w-full truncate rounded-xs text-ink-2 underline-offset-2 outline-none hover:text-ink hover:underline focus-visible:focus-ring"
        title={sc('viewResults')}
      >
        {sc('captured', { count: run!.descriptors.length })}
        <span className="sr-only">，{sc('viewResults')}</span>
      </button>
    )
  } else if (phase === 'cancelled') {
    body = sc('cancelledNote')
  } else if (run?.error) {
    const text = formatMessage(backendCodeMsg(run.error.code, run.error.params, run.error.message))
    title = text
    // 门上的那句是「还差一个决定」，不是错误：不用危险色
    body = <span className={isGatePhase(phase) ? 'text-ink-2' : 'text-danger'}>{text}</span>
  } else if (entry.registered) {
    body = sc('linkedCount', { count: stems.length })
  } else if (entry.reason === 'dynamic_stems' || entry.reason === 'unparseable') {
    body = sc('runtimeNamesNote')
  } else {
    body = sc('notRunNote')
  }

  // aria-live 挂在常驻容器上（内容只随相位变化）：loading / 完成 / 失败
  // 各播报一次，绝不逐帧刷
  return (
    <span
      aria-live="polite"
      title={title}
      className="flex min-w-0 max-w-[55%] shrink items-center truncate type-meta"
    >
      {body}
    </span>
  )
}

/**
 * 「找不到数据」的出路（ADR 0106）：对话框被「稍后」关掉之后，从这一行再打开。
 */
function MissingInputRecovery({ run }: { run: ScriptRunState | undefined }) {
  useTranslation('workspace')
  const offer = run?.error?.missing_input
  if (!offer || isBusyPhase(run!.phase)) return null
  return (
    <SecondRow data-script-missing-input>
      <Button
        variant="secondary"
        size="sm"
        data-testid="script-missing-input-open"
        onClick={() => useEnvStore.getState().requestMissingInput(offer)}
      >
        {translate('engine.missingInputOpen', { ns: 'errors' })}
      </Button>
    </SecondRow>
  )
}

/**
 * safe 失败的恢复路径（总纲 §四）：原因解释、「选择渲染环境」的真实入口（就地打开渲染环境对话框）与「复制诊断」，
 * 全部收在默认折叠的「详情」里（2026-09-29），行上默认只多一个折叠标题。**不渲染任何 native 按钮**——PR 2 未落地，
 * 只有文案里的一句「后续版本还将支持」（不许出现可点但无功能的入口）。
 */
function FailureRecovery({ script, run }: { script: string; run: ScriptRunState | undefined }) {
  useTranslation('workspace')
  const [copied, setCopied] = useState(false)
  // 缺包且有修复 offer：修复卡就是下一步（这一行，或同一个包的另一行上那一张），不再叠恢复入口
  const covered = run?.phase === 'missing_dependency' && !!run.error?.dependency_repair
  if (!needsNative(run) || covered) return null
  const error = run!.error

  const copyDiagnostics = async () => {
    const text = [
      `script: ${script}`,
      `code: ${error?.code ?? ''}`,
      error?.message ?? '',
      error?.traceback ?? '',
    ]
      .filter(Boolean)
      .join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* 剪贴板不可用（无权限）：按钮保持原样，用户可从诊断详情手工复制 */
    }
  }

  return (
    // 缩进到文件名那一列（状态点列 + 间距），不套框：它是这一行的第二行，不是另一张卡。默认只露一个
    // 「详情」（2026-09-29 用户：不许堆说明）：原因解释、两个出口、诊断都在里面
    <Details className="pb-1.5 pl-8 pr-2" data-script-recovery>
      <Summary className="type-meta">{sc('recoveryDetails')}</Summary>
      <div className="mt-1.5 flex flex-col gap-1.5">
        <p className="type-caption">{sc('recoveryBody')}</p>
        {/* 脚本要命令行参数（T03）：出口就在这里——一项一个 token 填进去，再试一次。其余失败形状不出现 */}
        {error?.code === 'script_needs_arguments' && (
          <div className="flex flex-col gap-1.5" data-script-argv-recovery>
            <ScriptArgvEditor script={script} />
            <div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void useScriptRunStore.getState().run(script)}
              >
                <Play size={ICON_SIZE.sm} />
                {sc('rerunWithArgs')}
              </Button>
            </div>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            variant="secondary"
            size="sm"
            // 就地打开渲染环境对话框（EngineEnvironmentDialog），不深链设置页：
            // 卡片在设置里住在「诊断」页、环境正常时还折叠着，跳过去用户找不到
            onClick={() => useUiStore.getState().setEngineEnvOpen(true)}
          >
            <Settings size={ICON_SIZE.sm} />
            {sc('openEnvSettings')}
          </Button>
          <Button variant="secondary" size="sm" onClick={() => void copyDiagnostics()}>
            <Copy size={ICON_SIZE.sm} />
            {sc(copied ? 'copied' : 'copyDiagnostics')}
          </Button>
        </div>
        {/* 那一次的诊断（T04）：已经在折叠的「详情」里，只出按钮；没有引用（老后端）就不出现 */}
        {run?.diagnostic && (
          <TaskDiagnostic
            key={run.diagnostic.ref}
            kind="script_run"
            refId={run.diagnostic.ref}
            folded={false}
          />
        )}
        {error?.traceback && (
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-mono text-xs leading-snug text-ink-2">
            {error.traceback}
          </pre>
        )}
      </div>
    </Details>
  )
}

/**
 * 捕获结果弹层：一次运行发现的**每一张**图都在这里（多 Figure 绝不只显示
 * 第一张——负向反证 #4 的看护对象），各自可添加到画布。Dialog 自带
 * focus trap 与 Esc 关闭。
 */
export function ProbeResultsDialog({
  script,
  descriptors,
  dropped,
  open,
  onOpenChange,
  onAdded,
}: {
  script: string
  descriptors: CapturedFigureDescriptor[]
  dropped: number
  open: boolean
  onOpenChange: (v: boolean) => void
  /** 加进画布之后，带上**刚新建的那个面板**（准备面板据此观察它的首次编辑渲染，T09；不能按素材 id 回找——文档里可能已有同素材的旧实例） */
  onAdded?: (d: CapturedFigureDescriptor, panel: PanelObject) => void
}) {
  useTranslation('workspace')
  const setStatus = useUiStore((s) => s.setStatus)
  // 描述符属于这个对话框出现时的项目：之后切了项目（对话框还没来得及卸载）就不许把 A 的图加进 B 的版面
  const guard = useRef<ProjectEpochGuard | null>(null)
  useEffect(() => {
    if (open) guard.current = captureProjectEpoch()
  }, [open, descriptors])
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={sc('resultsTitle', { script })}
      description={sc('captured', { count: descriptors.length })}
      size="md"
    >
      <ul className="flex flex-col divide-y divide-border" aria-label={sc('resultsListAria')}>
        {descriptors.map((d) => (
          <li key={d.asset_id} className="flex items-center gap-2 py-1">
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={d.stem}>
              {d.stem}
            </span>
            <span className="shrink-0 type-meta tabular-nums">
              {translate('measure.cmSize', {
                w: formatCm(d.size_mm[0]),
                h: formatCm(d.size_mm[1]),
              })}
            </span>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                if (guard.current && !guard.current.still()) return
                const added = addRuntimePanelToCanvas(d)
                setStatus(msg('registry.addedToCanvas', { stem: d.stem }, 'dialogs'), 'done')
                onAdded?.(d, added)
              }}
            >
              {sc('addToCanvas')}
            </Button>
          </li>
        ))}
      </ul>
      {dropped > 0 && (
        <p className="mt-1.5 flex items-start gap-1 type-meta">
          <Ban size={ICON_SIZE.xs} className="mt-0.5 shrink-0" />
          {sc('dropped', { count: dropped })}
        </p>
      )}
    </Dialog>
  )
}
