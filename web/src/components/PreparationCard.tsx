import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChartLine,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  CircleCheck,
  CircleQuestionMark,
  Ellipsis,
  FileCodeCorner,
  ImageOff,
  KeyRound,
  LoaderCircle,
  Minus,
  RotateCcw,
  Unplug,
  X,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  backendCodeMsg,
  type PreparationReport,
  type ProjectScan,
  type WorkdirConfirmation,
  type WorkdirMode,
} from '@/lib/api'
import { formatMessage, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { markAutoShown, wasAutoShown } from '@/lib/guideCardSeen'
import { preparationPanelEnabled } from '@/lib/preparationFlag'
import { prepRowKey, prepView, targetName, type PrepPrimary, type PrepTone, type PrepView } from '@/lib/preparationText'
import { dependenciesLine, issueLine, roleLabel, scanCard, scanLine, type ScanCardKind } from '@/lib/projectScanText'
import { missingRequired, readTokens, type ScriptArgsSchema } from '@/lib/scriptArgsForm'
import { cn } from '@/lib/utils'
import { useCanvasToolbarVisible } from '@/components/CanvasToolbar'
import { addRuntimePanelToCanvas, openFastEdit } from '@/store/workspace'
import { refreshProjectNow } from '@/store/liveSync'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useEnvStore } from '@/store/envStore'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useProjectScanStore } from '@/store/projectScanStore'
import { useProjectStore } from '@/store/projectStore'
import {
  draftDiffers,
  scriptTarget,
  useProjectPreparationStore,
  type PrepEntry,
} from '@/store/projectPreparationStore'
import { useRenderStore } from '@/store/renderStore'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { ProbeResultsDialog } from './left/ScriptLibrary'
import { ScriptArgsForm } from './ScriptArgsForm'
import { ArgvTokens, PasteCommand } from './ScriptArgvEditor'
import { isSecret, suggestionText, useScriptInputAnswer, type ScriptInputAnswer } from './ScriptInputForm'
import { TaskDiagnostic } from './TaskDiagnostic'
import { Button, IconButton } from './ui/Button'
import { Details, Summary } from './ui/Details'
import { TextInput } from './ui/Input'
import { Menu, MenuItem } from './ui/Menu'
import { Radio } from './ui/Radio'

const pt = (key: string, values?: Record<string, unknown>) =>
  translate(`prep.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 展示面的名字（`scriptInputStore.claimPresentation`）：卡片展开且正展示那一问时，原对话框让开 */
const SURFACE = 'prep-card'

/** 加进画布的图首次编辑渲染好了没有：渲染态里那张图有了精确 manifest（这是前端自己观察到的事实，不是后端判据） */
function useEditReady(ids: string[]): boolean {
  return useRenderStore((s) =>
    ids.some((id) => Object.values(s.byKey).some((r) => r.fileId === id && r.status === 'ready' && r.manifest !== null)),
  )
}

/** 报告里后端给的参数 schema（`script_arguments` 待办的载荷）；没有 argparse 证据就没有 */
const argsSchemaOf = (report: PreparationReport | null): ScriptArgsSchema | null => {
  const req = report?.requirements.find((r) => r.kind === 'script_arguments')
  return req && req.kind === 'script_arguments' ? req.payload.schema : null
}

/**
 * 准备引导卡（T13b，取代 T02 的顶部检查条与 T09 的准备面板；设计稿 v2）：画布工作面板右下的**一张**浮动卡，
 * 不占布局（页面不下移）。所有「准备并打开」的入口——导入即扫描发现了绘图脚本、素材库脚本行 ▶、接入中心逐行
 * 「试运行并连接」——都落在这张卡上。
 *
 * ```text
 * 准备 · 运行 · 编辑                        —  ×
 * 发现绘图脚本 spectrum.py
 * 详情 ▾                       稍后  [开始准备]
 * ```
 *
 * * **一句话 + 一个黑色主按钮**（用户硬性要求「卡片一句话读懂」）：标题与主按钮只在 `lib/preparationText.ts`（会话）/
 *   `lib/projectScanText.ts`（扫描）；路径、用的是哪一套、运行目录、参数、错误原文、诊断都在「详情」里。
 * * **三种呈现**（`uiStore.guideCard`）：展开 / 角标 / 收起。「稍后」「—」「放到后台」缩成角标；角标或卡上的 × 才收起。
 *   缩成角标时只有脚本发问才自动展开（跑完、出错只改角标）；收起时等作答的 input 由原对话框接着问。
 * * **每个项目只自动弹一次**（`lib/guideCardSeen.ts`，本机按项目记）：扫描发现待准备的绘图脚本时弹；扫描中、没看全只出角标。
 *   **弹出时不建会话**——建会话就是检查（会起候选解释器），扫描阶段零执行；用户点「开始准备」才建。
 * * **按钮就是真实动作**：会话的动作来自报告（运行 / 安装 / 重新检查 / 停止），运行目录在卡里直接选（推荐项预选，确认后只
 *   重新检查、不运行），进入编辑用这次捕获的图；卡片不复制任何一个动作的实现。
 * * **进入编辑之后卡片自动收起**，工作区自己的「渲染完成」说结果。
 * * 本地开关关掉（`lib/preparationFlag`）时，「开始准备」回到旧的同步试运行（`scriptRunStore.run`），其余照旧。
 */
export function PreparationCard() {
  useTranslation('workspace')
  const mode = useUiStore((s) => s.guideCard)
  const focus = useProjectPreparationStore((s) => s.focus)
  const entry = useProjectPreparationStore((s) => (s.focus ? s.entries[s.focus] : undefined))
  const scan = useProjectScanStore((s) => s.scan)
  const slow = useProjectScanStore((s) => s.slow)
  const forced = useProjectScanStore((s) => s.forced)
  const tutorialProject = useProjectStore((s) => s.project?.tutorial === true)
  const onboardingActive = useOnboardingStore((s) => s.status === 'active')
  const toolbarUp = useCanvasToolbarVisible()
  // 草稿变了要重新渲染（参数改了 → 「继续」）
  const drafts = useScriptArgvStore((s) => s.drafts)
  const editReady = useEditReady(entry?.editing ?? [])
  const hidden = tutorialProject || onboardingActive
  const scanned = !entry && !hidden ? scanCard(scan, { slow, forced }) : null

  let view: PrepView | null = null
  if (entry && !hidden) {
    const script = 'id' in entry.target ? null : entry.target.script
    const schema = argsSchemaOf(entry.report)
    const tokens = script ? (drafts[script]?.tokens ?? []) : []
    const missingArgs =
      schema && schema.form_enabled ? missingRequired(schema, readTokens(schema, tokens)).length : null
    view = prepView(entry, { argsChanged: draftDiffers(entry.target), editReady, missingArgs })
  }

  // 每个项目自动弹一次：只看扫描（不建会话）；扫描中 / 没看全只出角标；什么都没有就收起
  const scanKind = scanned?.kind ?? null
  useEffect(() => {
    if (hidden || focus || !scan) return
    const ui = useUiStore.getState()
    if (!scanKind) {
      if (ui.guideCard !== 'closed' && !forced) ui.setGuideCard('closed')
      return
    }
    if (wasAutoShown(scan.project_id)) return
    if (scanKind === 'discover' || scanKind === 'choose') {
      ui.setGuideCard('card')
      markAutoShown(scan.project_id)
    } else if ((scanKind === 'scanning' || scanKind === 'stuck') && ui.guideCard === 'closed') {
      ui.setGuideCard('pill')
    }
  }, [hidden, focus, scan, scanKind, forced])

  // 角标状态下脚本发问：卡片自动展开（收起时不动——原对话框接着问）
  const asking = view?.input === true
  useEffect(() => {
    if (asking && useUiStore.getState().guideCard === 'pill') useUiStore.getState().setGuideCard('card')
  }, [asking])

  if (hidden || mode === 'closed' || (!view && !scanned)) return null
  return (
    <div
      data-guide-anchor
      className={cn(
        'pointer-events-none absolute right-3 z-drawer flex max-h-[calc(100%-1.5rem)] w-[400px] max-w-[calc(100%-1.5rem)] flex-col items-end',
        toolbarUp ? 'bottom-16' : 'bottom-3',
      )}
    >
      {entry && view ? (
        mode === 'pill' ? (
          <SessionPill entry={entry} view={view} />
        ) : (
          <SessionCard entry={entry} view={view} />
        )
      ) : scan && scanned ? (
        mode === 'pill' ? (
          <ScanPill scan={scan} card={scanned} />
        ) : (
          <ScanCard scan={scan} card={scanned} />
        )
      ) : null}
    </div>
  )
}

/* ------------------------------------------------------------------ 外壳 */

const STEPS = ['prepare', 'run', 'edit'] as const

const TONE_ICON: Record<Exclude<PrepTone, null>, ReactNode> = {
  busy: <LoaderCircle size={ICON_SIZE.md} className="shrink-0 animate-spin text-accent motion-reduce:animate-none" />,
  ok: <CircleCheck size={ICON_SIZE.md} className="shrink-0 text-ok" />,
  bad: <CircleAlert size={ICON_SIZE.md} className="shrink-0 text-danger" />,
  mute: <CircleQuestionMark size={ICON_SIZE.md} className="shrink-0 text-ink-3" />,
}

function toneIcon(tone: PrepTone, state: string): ReactNode {
  if (!tone) return null
  // 几个状态有自己更具体的记号（设计稿 v2）
  if (state === 'no_figure' || state === 'no_figure_raster') return <ImageOff size={ICON_SIZE.md} className="shrink-0 text-ink-3" />
  if (state === 'offline') return <Unplug size={ICON_SIZE.md} className="shrink-0 text-ink-3" />
  if (state === 'restarted') return <RotateCcw size={ICON_SIZE.md} className="shrink-0 text-ink-3" />
  if (state === 'input_secret') return <KeyRound size={ICON_SIZE.md} className="shrink-0 text-ink-3" />
  return TONE_ICON[tone]
}

interface ShellProps {
  label: string
  step: 0 | 1 | 2
  icon?: ReactNode
  title: string
  attrs: Record<string, string>
  /** 标题下面这一步要做的事（运行目录 / 参数 / 回答框…），不是收起的详情 */
  slot?: ReactNode
  details?: ReactNode
  /** 「详情」那颗换个名字（回答问题时是「全部输出」） */
  detailsLabel?: string
  more?: ReactNode
  ghost?: ReactNode
  primary?: ReactNode
}

/** 卡片外壳：步骤 · 工具（— ⋯ ×）/ 图标 + 标题 / 这一步 / 详情 / 详情 · 稍后 · 主按钮 */
function Shell({ label, step, icon, title, attrs, slot, details, detailsLabel, more, ghost, primary }: ShellProps) {
  const [open, setOpen] = useState(false)
  const ui = useUiStore.getState()
  return (
    <section
      {...attrs}
      data-prep-card
      aria-label={label}
      className="pointer-events-auto flex min-h-0 w-full animate-pop-in flex-col gap-3 rounded-panel bg-surface px-5 pb-4 pt-3.5 text-base text-ink shadow-dialog"
    >
      <div className="flex h-6 shrink-0 items-center gap-2">
        <ol className="flex items-center gap-1.5 text-sm" aria-label={pt('card.steps')}>
          {STEPS.map((s, i) => (
            <li
              key={s}
              aria-current={i === step ? 'step' : undefined}
              className={cn(
                'flex items-center gap-1.5',
                i === step ? 'font-medium text-ink' : i < step ? 'text-ink-3' : 'text-ink-faint',
              )}
            >
              {i > 0 && <span aria-hidden className="h-[3px] w-[3px] rounded-full bg-ink-faint" />}
              {pt(`card.step.${s}`)}
            </li>
          ))}
        </ol>
        <div className="-mr-2 ml-auto flex items-center">
          <IconButton
            iconSize="sm"
            label={pt('card.minimize')}
            data-prep-minimize
            className="text-ink-3"
            onClick={() => ui.setGuideCard('pill')}
          >
            <Minus size={ICON_SIZE.sm} />
          </IconButton>
          {more}
          <IconButton
            iconSize="sm"
            label={pt('card.close')}
            data-prep-close
            className="text-ink-3"
            onClick={() => ui.setGuideCard('closed')}
          >
            <X size={ICON_SIZE.sm} />
          </IconButton>
        </div>
      </div>
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto" data-prep-body>
        <div className="flex items-start gap-2.5">
          {icon}
          <p className="type-title min-w-0 flex-1 break-words font-medium text-ink" data-prep-line aria-live="polite">
            {title}
          </p>
        </div>
        {slot}
        {details && open && (
          <div
            data-prep-details
            className="flex flex-col gap-1.5 border-t border-border pt-2.5 text-sm text-ink-2"
          >
            {details}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {details && (
          <button
            type="button"
            data-prep-details-toggle
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-0.5 rounded-sm text-base text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
          >
            {detailsLabel ?? pt('card.details')}
            {open ? <ChevronUp size={ICON_SIZE.sm} /> : <ChevronDown size={ICON_SIZE.sm} />}
          </button>
        )}
        <span className="flex-1" />
        {ghost}
        {primary}
      </div>
    </section>
  )
}

/** 角标：一行，点开回到卡片；× 才彻底收起 */
function Pill({
  icon,
  children,
  action,
  attrs,
  label,
}: {
  icon: ReactNode
  children: ReactNode
  action?: ReactNode
  attrs: Record<string, string>
  label: string
}) {
  const ui = useUiStore.getState()
  return (
    <div
      {...attrs}
      data-prep-pill
      className="pointer-events-auto flex h-9 max-w-full animate-pop-in items-center gap-2 rounded-full bg-surface pl-1.5 pr-1.5 text-base text-ink shadow-dialog"
    >
      <button
        type="button"
        data-prep-pill-open
        aria-label={label}
        onClick={() => ui.setGuideCard('card')}
        className="flex min-w-0 items-center gap-2 rounded-full py-1 pl-2 pr-1 outline-none hover:text-ink-2 focus-visible:focus-ring"
      >
        {icon}
        <span className="min-w-0 truncate">{children}</span>
      </button>
      {action}
      <IconButton
        iconSize="sm"
        label={pt('card.close')}
        data-prep-pill-close
        className="rounded-full text-ink-3"
        onClick={() => ui.setGuideCard('closed')}
      >
        <X size={ICON_SIZE.sm} />
      </IconButton>
    </div>
  )
}

const Progress = () => (
  <div className="relative h-1 overflow-hidden rounded-full bg-surface-active" data-prep-progress aria-hidden>
    <span className="absolute inset-y-0 left-0 w-1/3 animate-sweep rounded-full bg-ink motion-reduce:animate-none" />
  </div>
)

const Later = ({ onClick }: { onClick?: () => void }) => (
  <Button
    variant="ghost"
    size="md"
    data-prep-later
    className="text-ink-2"
    onClick={onClick ?? (() => useUiStore.getState().setGuideCard('pill'))}
  >
    {pt('btn.later')}
  </Button>
)

/** 详情里的一行：左边是名字，右边是值 */
const Row = ({ k, v, attr }: { k: string; v: ReactNode; attr?: string }) => (
  <div className="flex items-baseline justify-between gap-3" data-prep-row={attr}>
    <span className="shrink-0 text-ink-3">{k}</span>
    <span className="min-w-0 truncate text-right text-ink-2">{v}</span>
  </div>
)

/* ------------------------------------------------------------------ 扫描（还没有会话） */

function ScanCard({ scan, card }: { scan: ProjectScan; card: { kind: ScanCardKind; key: string; values: Record<string, unknown> } }) {
  useTranslation('workspace')
  const plots = (scan.targets ?? []).filter((x) => x.role !== 'auxiliary')
  const [pick, setPick] = useState<string | null>(scan.default_target ?? plots[0]?.script ?? null)
  const store = useProjectScanStore.getState()
  const title = scanLine(card)
  const attrs = { 'data-prep-state': card.kind, 'data-scan-state': scan.state, 'data-scan-phase': scan.phase }
  const start = (script: string) => {
    if (!preparationPanelEnabled()) {
      // 本地开关关闭：回到旧的同步试运行（素材库那台状态机），卡片让开
      useUiStore.getState().setGuideCard('closed')
      void useScriptRunStore.getState().run(script)
      return
    }
    void useProjectPreparationStore.getState().open(scriptTarget(script))
  }
  const deps = dependenciesLine(scan)
  const details = (
    <>
      {plots.slice(0, 6).map((x) => (
        <Row key={x.script} k={roleLabel(x.role)} v={<span className="font-mono">{x.script}</span>} attr={`target:${x.script}`} />
      ))}
      {deps && <p data-scan-deps>{deps}</p>}
      {scan.issues.map((i) => (
        <p key={`${i.code}:${i.path ?? ''}`} data-scan-issue={i.code}>
          {issueLine(i)}
        </p>
      ))}
      <div className="flex gap-3.5 text-ink-3">
        <button
          type="button"
          data-scan-rescan
          className="rounded-xs outline-none hover:text-ink-2 focus-visible:focus-ring"
          onClick={() => void store.start({ force: true, reason: 'manual' })}
        >
          {pt('btn.recheck')}
        </button>
        <button
          type="button"
          data-scan-open-assets
          className="rounded-xs outline-none hover:text-ink-2 focus-visible:focus-ring"
          onClick={() => useUiStore.getState().setLeftTab('assets')}
        >
          {pt('btn.openAssets')}
        </button>
      </div>
    </>
  )
  if (card.kind === 'discover' || card.kind === 'choose') {
    const target = card.kind === 'discover' ? (scan.default_target ?? null) : pick
    return (
      <Shell
        label={pt('aria', { script: target ?? '' })}
        step={0}
        title={title}
        attrs={attrs}
        slot={
          card.kind === 'choose' ? (
            <fieldset className="flex flex-col gap-1.5" data-scan-choose>
              <legend className="sr-only">{title}</legend>
              {plots.slice(0, 6).map((x) => (
                <label
                  key={x.script}
                  data-scan-target={x.script}
                  className={cn(
                    'flex items-center gap-2.5 rounded-md px-3 py-2',
                    pick === x.script ? 'bg-selected' : 'hover:bg-surface-hover',
                  )}
                >
                  <Radio name="scan-target" checked={pick === x.script} onChange={() => setPick(x.script)} />
                  <span className="min-w-0 truncate font-mono text-sm">{x.script}</span>
                </label>
              ))}
            </fieldset>
          ) : undefined
        }
        details={details}
        ghost={<Later />}
        primary={
          <Button
            variant="primary"
            size="md"
            data-prep-primary="start"
            disabled={!target}
            onClick={() => {
              if (target) start(target)
            }}
          >
            {pt('btn.start')}
          </Button>
        }
      />
    )
  }
  return (
    <Shell
      label={title}
      step={0}
      icon={card.kind === 'scanning' ? TONE_ICON.busy : card.kind === 'stuck' ? TONE_ICON.mute : undefined}
      title={title}
      attrs={attrs}
      slot={card.kind === 'scanning' ? <Progress /> : undefined}
      details={card.kind === 'scanning' ? undefined : details}
      ghost={
        card.kind === 'scanning' ? (
          <Button variant="ghost" size="md" data-scan-cancel className="text-ink-2" onClick={() => void store.cancel()}>
            {pt('btn.cancelScan')}
          </Button>
        ) : undefined
      }
      primary={
        card.kind === 'stuck' ? (
          <Button
            variant="primary"
            size="md"
            data-prep-primary="rescan"
            onClick={() => void store.start({ force: true, reason: 'manual' })}
          >
            {pt('btn.recheck')}
          </Button>
        ) : undefined
      }
    />
  )
}

function ScanPill({ scan, card }: { scan: ProjectScan; card: { kind: ScanCardKind; key: string; values: Record<string, unknown> } }) {
  useTranslation('workspace')
  const attrs = { 'data-prep-state': card.kind, 'data-scan-state': scan.state }
  const label = scanLine(card)
  if (card.kind === 'discover' || card.kind === 'choose') {
    return (
      <Pill attrs={attrs} label={label} icon={<FileCodeCorner size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />}>
        {card.kind === 'discover' ? (
          <>
            <b className="font-medium">{scan.default_target}</b> {pt('pill.pending')}
          </>
        ) : (
          pt('pill.pendingMany', { count: card.values.count })
        )}
      </Pill>
    )
  }
  return (
    <Pill
      attrs={attrs}
      label={label}
      icon={card.kind === 'scanning' ? <LoaderCircle size={ICON_SIZE.sm} className="shrink-0 animate-spin text-accent motion-reduce:animate-none" /> : <CircleQuestionMark size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />}
    >
      {label}
    </Pill>
  )
}

/* ------------------------------------------------------------------ 会话 */

/**
 * 这些卡上参数以折叠摆着（「其他 N 个参数」「原样参数 / 粘贴命令」）：可以运行之前改参数、出错 / 没出图 / 画好之后换参数再跑。
 * 改了就是参数卡（「继续」= 按新参数重新检查）——参数块按同一个 key 留在原位，输入框不重建、焦点不丢
 */
const ARGS_FOLDED = new Set([
  'ready',
  'restarted',
  'rejected',
  'failed',
  'no_figure',
  'no_figure_raster',
  'completed',
  'completed_unlinked',
  'cancelled',
])

const primaryId = (p: PrepPrimary): string => (p.kind === 'action' ? p.action : p.kind)

function primaryLabel(p: PrepPrimary, workdir: WorkdirMode | null): string {
  switch (p.kind) {
    case 'action':
      return pt(`btn.${p.label}`)
    case 'adopt':
      return pt('btn.useIt')
    case 'workdir':
      return workdir ? pt(`btn.workdir.${workdir}`) : pt('btn.workdir.none')
    case 'missing_input':
      return pt('btn.pointAtData')
    case 'enter_edit':
      return p.count > 1 ? pt('btn.viewFigures', { count: p.count }) : pt('btn.enterEdit')
    case 'reopen':
      return pt(p.label === 'continue' ? 'btn.continue' : 'btn.retry')
    case 'open_environment':
      return pt('btn.openSettings')
    case 'open_registry':
      return pt('btn.openRegistry')
    case 'open_assets':
      return pt('btn.openAssets')
    case 'background':
      return pt('btn.background')
    case 'dismiss':
      return pt('btn.dismiss')
  }
}

/** 主按钮做的事（与 T09 面板同一组真实动作；卡片只换了呈现） */
function useRunPrimary(entry: PrepEntry, onMany: () => void) {
  return async (p: PrepPrimary, workdir: WorkdirMode | null): Promise<string | null> => {
    const store = useProjectPreparationStore.getState()
    const ui = useUiStore.getState()
    const report = entry.report
    switch (p.kind) {
      case 'action':
        await store.act(entry.key, p.action)
        return null
      case 'adopt':
        // 成功：envStore 的环境变了 → 会话只读地重新检查（store 订阅），下一步仍由报告给出
        return useEnvStore.getState().adoptCandidate(p.candidate, targetName(entry))
      case 'workdir':
        // 卡里选的就是答案：同一次 PATCH（项目级、记住），答完会话只读地重新检查（store 订阅 envStore），**不运行**
        if (!workdir) return null
        return useEnvStore.getState().setWorkdirMode(workdir, { confirmed: true })
      case 'missing_input':
        useEnvStore.getState().requestMissingInput(p.payload, entry.pj)
        return null
      case 'enter_edit': {
        const figures = report?.captured ?? []
        if (figures.length !== 1) {
          onMany() // 多张图：每一张都列出来、各自加进画布（绝不只取第一张）
          return null
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
        // 进入编辑之后卡片自动收起，不留角标（工作区自己的「渲染完成」说结果）
        ui.setGuideCard('closed')
        return null
      }
      case 'reopen':
        await store.open('id' in entry.target ? entry.target : scriptTarget(entry.target.script))
        return null
      case 'open_environment':
        ui.setEngineEnvOpen(true)
        return null
      case 'open_registry':
        useProjectReadinessStore.getState().openCenter({ source: 'panel' })
        return null
      case 'open_assets':
        // 脚本自己写出的图片文件，素材库本来就列（不新造导入入口）；卡片收起让位给素材库
        // 素材库挂载时不重载 `/api/panels`、项目 watcher 又要等轮询+防抖：刚写出的图不主动刷新就要等几秒才出现，
        // 按钮看起来像坏了。走素材库工具栏刷新按钮用的同一条统一刷新（不 await：切标签不等它，失败也不挡路）
        void refreshProjectNow().catch(() => {})
        void useRuntimeAssetStore.getState().loadAssets()
        ui.setLeftTab('assets')
        ui.setGuideCard('closed')
        return null
      case 'background':
        ui.setGuideCard('pill')
        return null
      case 'dismiss':
        ui.setGuideCard('closed')
        return null
    }
  }
}

function SessionCard({ entry, view }: { entry: PrepEntry; view: PrepView }) {
  useTranslation(['workspace', 'errors'])
  const report = entry.report
  const script = targetName(entry)
  const [localError, setLocalError] = useState<string | null>(null)
  const [resultsOpen, setResultsOpen] = useState(false)
  const workdirReq = report?.requirements.find((r) => r.kind === 'workdir_choice')
  const workdirPayload = workdirReq?.kind === 'workdir_choice' ? workdirReq.payload : null
  const [workdir, setWorkdir] = useState<WorkdirMode | null>(workdirPayload?.recommended ?? null)
  // 每一份新载荷从它自己的推荐项起步：歧义时 null（不预选）
  useEffect(() => setWorkdir(workdirPayload?.recommended ?? null), [workdirPayload])
  // 报告换了（新修订 / 新观察）就把上一次本地动作的失败收起
  useEffect(() => setLocalError(null), [report?.config_revision, report?.observation_seq])
  const runPrimary = useRunPrimary(entry, () => setResultsOpen(true))
  const store = useProjectPreparationStore.getState()
  const pending = entry.pending !== null
  // 回答框、「回答」、⋯ 共用同一份作答状态（框里的字只在这里，不进 store）
  const answer = useScriptInputAnswer()

  const p = view.primary
  const disabled = view.primaryDisabled && !(p?.kind === 'workdir' && workdir !== null)
  const title =
    localError ??
    // 跑完没出 Matplotlib 图、但脚本自己用位图库写了图片：这句话独立成一组键（`prep.rasterHint.*`）
    (view.state === 'no_figure_raster'
      ? pt('rasterHint.line', view.sentence.values)
      : pt(`line.${view.sentence.key}`, view.sentence.values))
  const attrs = {
    'data-prep-state': localError ? 'action_failed' : view.state,
    'data-prep-phase': report?.phase ?? '',
    'data-prep-session': report?.session_id ?? '',
    'data-prep-connection': entry.connection,
  }
  const scriptTargetName = 'id' in entry.target ? null : entry.target.script

  const primary = p && (
    <Button
      variant="primary"
      size="md"
      data-prep-primary={primaryId(p)}
      loading={pending}
      disabled={pending || disabled}
      onClick={async () => {
        setLocalError(null)
        const err = await runPrimary(p, workdir)
        if (err) setLocalError(err)
      }}
    >
      {primaryLabel(p, workdir)}
    </Button>
  )
  const ghost =
    view.ghost === 'later' ? (
      <Later />
    ) : view.ghost === 'stop' ? (
      <Button
        variant="ghost"
        size="md"
        data-prep-stop
        className="text-ink-2"
        disabled={pending}
        onClick={() => void store.act(entry.key, 'cancel')}
      >
        {pt('btn.stop')}
      </Button>
    ) : null

  // 「这一步」：参数块按 key 摆在固定位置——在 ready 与参数卡之间切换时输入框不重建、焦点不丢
  const slot: ReactNode[] = []
  if (view.slot === 'progress') slot.push(<Progress key="progress" />)
  if (view.slot === 'workdir' && workdirPayload) {
    slot.push(<WorkdirChoice key="workdir" payload={workdirPayload} choice={workdir} onChoose={setWorkdir} />)
  }
  if (view.slot === 'install') slot.push(<InstallLine key="install" report={report} />)
  if (view.slot === 'ready') slot.push(<ReadyLine key="ready" entry={entry} />)
  if (view.slot === 'figures') slot.push(<Figures key="figures" report={report} />)
  // 回答控件只属于「队首就是本报告那一问」的卡片：别的脚本的问由原对话框展示，这里的按钮绝不能答到它头上
  const ownsInput = view.input && report?.runtime_input != null && answer.head?.id === report.runtime_input.id
  if (view.input && report?.runtime_input) {
    slot.push(<EmbeddedInput key="input" requestId={report.runtime_input.id} answer={answer} />)
  }
  const argsHere = scriptTargetName !== null && (view.argsOpen || ARGS_FOLDED.has(view.state))
  if (argsHere) {
    slot.push(<ArgsBlock key="args" script={scriptTargetName} schema={argsSchemaOf(report)} expanded={view.argsOpen} />)
  }

  const details = ownsInput ? <InputOutput /> : <SessionDetails entry={entry} view={view} />
  return (
    <>
      <Shell
        label={pt('aria', { script })}
        step={view.step}
        icon={toneIcon(view.tone, view.sentence.key === 'inputSecret' ? 'input_secret' : view.state)}
        title={title}
        attrs={attrs}
        slot={slot.length ? slot : undefined}
        details={details}
        detailsLabel={ownsInput ? pt('card.allOutput') : undefined}
        more={ownsInput ? <InputMore answer={answer} /> : undefined}
        ghost={ghost}
        primary={ownsInput ? <AnswerButton answer={answer} /> : primary}
      />
      {report && (report.captured ?? []).length > 1 && (
        <ProbeResultsDialog
          script={script}
          descriptors={report.captured ?? []}
          dropped={0}
          open={resultsOpen}
          onOpenChange={(v) => {
            setResultsOpen(v)
            if (!v) useUiStore.getState().setGuideCard('closed')
          }}
        />
      )}
    </>
  )
}

function SessionPill({ entry, view }: { entry: PrepEntry; view: PrepView }) {
  useTranslation('workspace')
  const row = prepRowKey(entry)
  const script = targetName(entry)
  const runPrimary = useRunPrimary(entry, () => useUiStore.getState().setGuideCard('card'))
  const attrs = { 'data-prep-state': view.state, 'data-prep-connection': entry.connection }
  const text = row ? translate(`prep.row.${row.key}`, { ns: 'workspace', ...row.values }) : ''
  const icon = (() => {
    switch (view.tone) {
      case 'busy':
        return <LoaderCircle size={ICON_SIZE.sm} className="shrink-0 animate-spin text-accent motion-reduce:animate-none" />
      case 'ok':
        return <CircleCheck size={ICON_SIZE.sm} className="shrink-0 text-ok" />
      case 'bad':
        return <CircleAlert size={ICON_SIZE.sm} className="shrink-0 text-danger" />
      default:
        return <FileCodeCorner size={ICON_SIZE.sm} className="shrink-0 text-ink-3" />
    }
  })()
  const enter = view.primary?.kind === 'enter_edit' ? view.primary : null
  return (
    <Pill
      attrs={attrs}
      label={pt('aria', { script })}
      icon={icon}
      action={
        enter ? (
          <button
            type="button"
            data-prep-pill-action
            className="shrink-0 rounded-full px-1.5 text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
            onClick={() => void runPrimary(enter, null)}
          >
            {primaryLabel(enter, null)}
          </button>
        ) : undefined
      }
    >
      <b className="font-medium">{script}</b> {text}
    </Pill>
  )
}

/* ------------------------------------------------------------------ 各步的内容 */

const WORKDIR_LABEL: Record<WorkdirMode, string> = {
  project_root: 'workdir.project_root',
  project: 'workdir.project',
  sandbox: 'workdir.sandbox',
}

/** 运行目录在卡里直接选：推荐项预选；「换一个」才展开其余；歧义时全部摆出来、不预选 */
function WorkdirChoice({
  payload,
  choice,
  onChoose,
}: {
  payload: WorkdirConfirmation
  choice: WorkdirMode | null
  onChoose: (m: WorkdirMode) => void
}) {
  useTranslation('workspace')
  const [all, setAll] = useState(payload.recommended === null)
  const options = payload.options ?? []
  const shown = all ? options : options.filter((o) => o.mode === payload.recommended)
  return (
    <fieldset className="flex flex-col gap-1.5" data-workdir-choice>
      <legend className="sr-only">{pt('line.workdir')}</legend>
      {shown.map((opt) => {
        const selected = choice === opt.mode
        return (
          <label
            key={opt.mode}
            data-workdir-option={opt.mode}
            className={cn(
              'flex items-start gap-2.5 rounded-md px-3 py-2 shadow-[inset_0_0_0_1px_var(--color-border)]',
              selected && 'shadow-[inset_0_0_0_1.5px_var(--color-sel)]',
            )}
          >
            <Radio name="prep-workdir" className="mt-0.5" checked={selected} onChange={() => onChoose(opt.mode)} />
            <span className="min-w-0 flex-1">
              <span className="block font-medium text-ink">
                {pt(WORKDIR_LABEL[opt.mode])}
                {opt.recommended && (
                  <span className="ml-1.5 rounded-full bg-surface-active px-1.5 text-sm font-normal text-ink-2">
                    {pt('workdir.recommended')}
                  </span>
                )}
              </span>
              {/* 找得到的文件是用户自己的数据名，不翻译 */}
              <span className="block text-sm text-ink-3">
                {opt.found.length ? pt('workdir.found', { files: opt.found.join(', ') }) : pt('workdir.foundNone')}
              </span>
            </span>
          </label>
        )
      })}
      {!all && options.length > 1 && (
        <button
          type="button"
          data-workdir-more
          className="flex items-center gap-1 self-start rounded-xs text-sm text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
          onClick={() => setAll(true)}
        >
          {pt('workdir.other')}
          <ChevronDown size={ICON_SIZE.sm} />
        </button>
      )}
    </fieldset>
  )
}

/** 要装的东西一行：装什么 · 装到哪（包名、来源细节在详情） */
function InstallLine({ report }: { report: PreparationReport | null }) {
  useTranslation('workspace')
  const impact = report?.actions.find((a) => a.kind === 'prepare_dependencies')?.impact
  if (!impact) return null
  const installs = impact.installs ?? []
  const what = installs.length
    ? installs.length > 3
      ? pt('install.many', { list: installs.slice(0, 3).join(', '), count: installs.length })
      : installs.join(', ')
    : pt('install.runtime')
  return (
    <p className="text-sm text-ink-2" data-prep-install>
      {what} · {pt(impact.modifies_user_environment ? 'install.toUser' : 'install.toManaged')}
    </p>
  )
}

/** 可以运行时：在哪个目录、带什么参数（参数值只在本机草稿里，敏感的不显示） */
function ReadyLine({ entry }: { entry: PrepEntry }) {
  useTranslation(['workspace', 'errors'])
  const mode = useEnvStore((s) => s.env?.project?.workdir?.mode ?? null)
  const target = entry.target
  const argv = 'id' in target ? [] : (target.argv ?? [])
  const args =
    argv.length === 0
      ? pt('detail.argsNone')
      : 'argv_sensitive' in target && target.argv_sensitive
        ? pt('detail.argsHidden', { count: argv.length })
        : argv.map((t) => (/[\s"']/.test(t) || t === '' ? JSON.stringify(t) : t)).join(' ')
  return (
    <p className="truncate text-sm text-ink-2" data-prep-ready-line>
      {mode && (
        <>
          {pt(WORKDIR_LABEL[mode as WorkdirMode] ?? 'workdir.project_root')}
          {' · '}
        </>
      )}
      <span className="font-mono">{args}</span>
    </p>
  )
}

function Figures({ report }: { report: PreparationReport | null }) {
  const figures = report?.captured ?? []
  if (!figures.length) return null
  return (
    <div className="flex flex-wrap gap-1.5" data-prep-figures>
      {figures.slice(0, 3).map((d) => (
        <span
          key={d.asset_id}
          className="inline-flex h-7 items-center gap-1.5 rounded-md bg-surface-2 px-2.5 font-mono text-sm text-ink"
        >
          <ChartLine size={ICON_SIZE.sm} className="text-ink-3" />
          {d.stem}
        </span>
      ))}
      {figures.length > 3 && <span className="self-center text-sm text-ink-3">+{figures.length - 3}</span>}
    </div>
  )
}

/**
 * 参数：必填项直接摆出来（脚本写了 help 就随字段显示）；其余参数、原样 token 列表与粘贴命令收在折叠里。
 * 参数 schema 是后端报告给的；没有 schema 时只有「原样参数 / 粘贴命令」（需要参数时默认展开）。
 */
function ArgsBlock({ script, schema, expanded }: { script: string; schema: ScriptArgsSchema | null; expanded: boolean }) {
  useTranslation('workspace')
  const visible = schema?.arguments.filter((a) => !a.hidden) ?? []
  const required = visible.filter((a) => a.required === true)
  const optional = visible.length - required.length
  return (
    <div className="flex flex-col gap-1.5" data-prep-args>
      {schema && expanded && required.length > 0 && <ScriptArgsForm script={script} schema={schema} fields="required" />}
      {schema && optional > 0 && (
        <Details data-testid={`argv-optional-${script}`}>
          <Summary className="type-body h-6 gap-1 text-ink-2">
            {pt('card.otherArgs', { count: optional })}
          </Summary>
          <div className="pt-1.5">
            <ScriptArgsForm script={script} schema={schema} fields="optional" />
          </div>
        </Details>
      )}
      <Details data-testid={`argv-raw-${script}`} open={expanded && !schema ? true : undefined}>
        <Summary className="type-body h-6 gap-1 text-ink-2">
          {pt('card.rawArgs')}
        </Summary>
        <div className="flex flex-col gap-1 pt-1.5">
          <ArgvTokens script={script} />
          <PasteCommand script={script} />
        </div>
      </Details>
    </div>
  )
}

/** 默认收起的「详情」：脚本、用的是哪一套、运行目录、参数；要装什么；结果的三件事；错误原文与那一次的诊断 */
function SessionDetails({ entry, view }: { entry: PrepEntry; view: PrepView }) {
  useTranslation(['workspace', 'errors'])
  const report = entry.report
  const store = useProjectPreparationStore.getState()
  const mode = useEnvStore((s) => s.env?.project?.workdir?.mode ?? null)
  const wdReq = report?.requirements.find((r) => r.kind === 'workdir_choice')
  const prepare = report?.actions.find((a) => a.kind === 'prepare_dependencies')
  const error = report?.result?.error as
    | { code?: string; message?: string; params?: Record<string, unknown>; traceback?: string }
    | null
    | undefined
  const attempt = report?.provider.attempt_id
  const dep = report?.provider.dependency
  const env = report?.environment
  const primaryAction = view.primary?.kind === 'action' ? view.primary.action : null
  const recheck = primaryAction !== 'recheck' && report?.actions.some((a) => a.kind === 'recheck')
  const target = entry.target
  const argc = 'id' in target ? 0 : (target.argv?.length ?? 0)
  const failed = report?.outcome.kind === 'failed' && report.outcome.reason !== 'dependency_preparation'
  // 错误原文：脚本自己的那一行（`params.error`）优先，其次是后端的主文案；traceback 只取最后一帧那一行
  const original = error ? String(error.params?.error ?? error.message ?? '') : ''
  const frame = (error?.traceback ?? '')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('File '))
    .pop()
  return (
    <>
      {failed && original && (
        <pre
          data-prep-error={error?.code ?? ''}
          className="whitespace-pre-wrap break-words rounded-md bg-danger-subtle px-2.5 py-2 font-mono text-sm text-danger"
        >
          {original}
          {frame ? `\n  ${frame}` : ''}
        </pre>
      )}
      {/* 那一次的诊断（T04）：失败尝试的冻结快照，不含路径和参数 */}
      {attempt && failed && <TaskDiagnostic key={attempt} kind="preparation" refId={attempt} folded={false} />}
      {dep && report?.outcome.reason === 'dependency_preparation' && (
        <TaskDiagnostic key={dep.plan_id} kind="dependency" refId={dep.plan_id} folded={false} />
      )}
      {view.state === 'no_figure' && <p data-prep-nofigure-why>{pt('detail.noFigureWhy')}</p>}
      {view.state === 'no_figure_raster' && <p data-prep-nofigure-raster-why>{pt('rasterHint.why')}</p>}
      {(report?.unlinked_stems ?? []).length > 0 && report?.phase === 'completed' && (
        <p data-prep-unlinked>{pt('detail.unlinked', { names: listJoin(report.unlinked_stems ?? []) })}</p>
      )}
      {report?.target.script && <Row k={pt('detail.script')} v={<span className="font-mono">{report.target.script}</span>} attr="script" />}
      {env?.kind && (
        <Row
          k={pt('detail.usedBy')}
          v={pt(`env.${env.kind}`) + (env.switched && env.replaced ? ` ${pt('env.switched')}` : '')}
          attr="environment"
        />
      )}
      {mode && !wdReq && <Row k={pt('detail.workdir')} v={pt(WORKDIR_LABEL[mode as WorkdirMode] ?? 'workdir.project_root')} attr="workdir" />}
      {report?.target.kind === 'script' && (
        <Row k={pt('detail.args')} v={argc ? pt('detail.argsCount', { count: argc }) : pt('detail.argsNone')} attr="args" />
      )}
      {wdReq && <p>{pt('workdir.writes')}</p>}
      {wdReq?.kind === 'workdir_choice' && (wdReq.payload?.conflicts.length ?? 0) > 0 && (
        <p>{pt('workdir.conflicts', { files: (wdReq.payload?.conflicts ?? []).join(', ') })}</p>
      )}
      {prepare && (prepare.impact.installs?.length ?? 0) > 0 && (
        <Row k={pt('detail.installs')} v={(prepare.impact.installs ?? []).join(', ')} attr="installs" />
      )}
      {prepare && (
        <Row
          k={pt('detail.installTo')}
          v={pt(prepare.impact.modifies_user_environment ? 'install.toUserLong' : 'install.managedPlace')}
          attr="install-to"
        />
      )}
      {prepare?.impact.private_python?.origin === 'download' && (
        <p>{pt('install.download', { mb: Math.max(1, Math.round(prepare.impact.private_python.download_bytes / 1048576)) })}</p>
      )}
      {report?.facts && <Facts report={report} />}
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
      {recheck && (
        <div className="flex gap-3.5 text-ink-3">
          <button
            type="button"
            data-prep-secondary="recheck"
            disabled={entry.pending !== null}
            className="rounded-xs outline-none hover:text-ink-2 focus-visible:focus-ring disabled:opacity-40"
            onClick={() => void store.act(entry.key, 'recheck')}
          >
            {pt('btn.recheck')}
          </button>
        </div>
      )}
    </>
  )
}

/** 执行结束 / 捕获到图：两件事分开说（`null` = 不适用，不说） */
function Facts({ report }: { report: PreparationReport }) {
  const { execution_finished: finished, figure_captured: captured } = report.facts
  if (finished === null && captured === null) return null
  return (
    <ul className="flex flex-col gap-0.5" data-prep-facts>
      {finished !== null && (
        <li data-fact="execution_finished" data-value={String(finished)}>
          {pt(finished ? 'fact.finished' : 'fact.notFinished')}
        </li>
      )}
      {captured !== null && (
        <li data-fact="figure_captured" data-value={String(captured)}>
          {pt(captured ? 'fact.captured' : 'fact.notCaptured')}
        </li>
      )}
    </ul>
  )
}

/* ------------------------------------------------------------------ 运行中回答问题 */

/** 只留最近几行输出：提示所在的那一行加粗（菜单通常就在最后几行） */
const TAIL_LINES = 4

/**
 * 正在等的那一问：卡片展示期间认领展示面，原对话框让开；卸载（缩成角标 / 收起 / 问题答完 / 换了一问）就放手。
 * 只在队首就是报告里的那一问时才认领——同一请求只有一个展示面，别的脚本的问照旧由原对话框展示。
 */
function EmbeddedInput({ requestId, answer }: { requestId: string; answer: ScriptInputAnswer }) {
  useTranslation('dialogs')
  const { head, value, setValue, busy, error } = answer
  const mine = head?.id === requestId
  useEffect(() => {
    if (!mine) return
    useScriptInputStore.getState().claimPresentation(SURFACE)
    return () => useScriptInputStore.getState().releasePresentation(SURFACE)
  }, [mine])
  if (!mine || !head) return null
  const secret = isSecret(head)
  // 输出片段常以提示本身结尾（input() 把提示写进 stdout）：去掉那一截，提示只在下面加粗说一次
  let out = (head.stdout_tail ?? '').replace(/\s+$/, '')
  const prompt = (head.prompt ?? '').trim()
  if (prompt && out.endsWith(prompt)) out = out.slice(0, -prompt.length).replace(/\s+$/, '')
  const lines = out ? out.split('\n') : []
  const tail = lines.slice(-TAIL_LINES).join('\n')
  const suggestion = !secret && head.suggestion ? head.suggestion : null
  const si = (key: string, v?: Record<string, unknown>) => translate(`scriptInput.${key}`, { ns: 'dialogs', ...v })
  return (
    <form
      className="flex flex-col gap-2"
      data-prep-input
      onSubmit={(e) => {
        e.preventDefault()
        answer.submit()
      }}
    >
      {!secret && (head.stdout_tail || head.prompt) && (
        <pre
          data-script-input-output=""
          className="whitespace-pre-wrap break-words rounded-md bg-surface-2 px-3 py-2.5 font-mono text-sm text-ink-2"
        >
          {tail || null}
          {head.prompt && (
            <>
              {tail ? '\n' : null}
              <b className="font-medium text-ink" data-script-input-prompt="">
                {head.prompt}
              </b>
            </>
          )}
        </pre>
      )}
      <TextInput
        autoFocus
        align="left"
        aria-label={secret ? pt('card.secretAria') : head.prompt || pt('card.answerAria')}
        type={secret ? 'password' : 'text'}
        autoComplete={secret ? 'off' : undefined}
        spellCheck={secret ? false : undefined}
        value={value}
        disabled={busy}
        data-script-input-answer=""
        onChange={(e) => setValue(e.target.value)}
      />
      <p className="text-sm text-ink-3" data-script-input-note>
        {suggestion !== null ? (
          <span data-script-input-suggestion="">{suggestionText(head, suggestion)}</span>
        ) : secret ? (
          pt('card.secretNote')
        ) : (
          pt('card.rememberNote')
        )}
      </p>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {si('failed', { error })}
        </p>
      )}
    </form>
  )
}

/** 「全部输出」：脚本到目前为止的输出（卡片主区只留最后几行） */
function InputOutput() {
  const head = useScriptInputStore((s) => s.queue[0] ?? null)
  if (!head?.stdout_tail || isSecret(head)) return <p className="text-ink-3">{pt('card.noOutput')}</p>
  return (
    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-sm text-ink-2" data-prep-full-output>
      {head.stdout_tail}
    </pre>
  )
}

/** ⋯：结束输入（EOF）/ 停止脚本——不常用的两个出口 */
function InputMore({ answer }: { answer: ScriptInputAnswer }) {
  return (
    <Menu
      align="end"
      trigger={
        <IconButton iconSize="sm" label={pt('card.more')} data-prep-more className="text-ink-3">
          <Ellipsis size={ICON_SIZE.sm} />
        </IconButton>
      }
    >
      <MenuItem onSelect={answer.eof} disabled={answer.busy} data-prep-eof>
        {pt('card.endInput')}
      </MenuItem>
      <MenuItem onSelect={answer.stop} disabled={answer.busy} danger data-prep-stop-script>
        {pt('card.stopScript')}
      </MenuItem>
    </Menu>
  )
}

function AnswerButton({ answer }: { answer: ScriptInputAnswer }) {
  if (!answer.head) return null
  return (
    <Button
      variant="primary"
      size="md"
      loading={answer.busy}
      data-prep-primary="answer"
      data-script-input-submit=""
      onClick={answer.submit}
    >
      {pt('btn.answer')}
    </Button>
  )
}

