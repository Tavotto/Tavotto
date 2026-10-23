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
  Sparkles,
  Unplug,
  X,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { WorkdirChooseButton } from '@/components/WorkdirRow'
import {
  backendCodeMsg,
  fetchDependencyOffer,
  isNativeTerminal,
  type GuiDialogPayload,
  type PreparationAttempt,
  type PreparationReport,
  type ProjectScan,
  type WorkdirConfirmation,
  type WorkdirMode,
} from '@/lib/api'
import { formatMessage, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { envNotUsedSentence, visibleNotUsed } from '@/lib/envRejectionText'
import { markAutoShown, wasAutoShown } from '@/lib/guideCardSeen'
import { preparationPanelEnabled } from '@/lib/preparationFlag'
import { originFactsOf, originViewOfCard } from '@/lib/importOrigin'
import { dialogKinds, finishedWithFigures, nativeFallbackNote, prepRowKey, prepView, targetName, withAiUnavailable, type PrepPrimary, type PrepTone, type PrepView } from '@/lib/preparationText'
import { dependenciesLine, issueLine, roleLabel, scanCard, scanLine, type ScanCardKind } from '@/lib/projectScanText'
import { missingRequirements, readTokens, type ScriptArgsSchema } from '@/lib/scriptArgsForm'
import { cn } from '@/lib/utils'
import { useCanvasToolbarVisible } from '@/components/CanvasToolbar'
import { refreshProjectNow } from '@/store/liveSync'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { convertOriginalUrl, convertPreviewUrl } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { useDepRepairStore } from '@/store/depRepairStore'
import { useEnvStore } from '@/store/envStore'
import { useExitNoticeStore } from '@/store/exitNoticeStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useProjectScanStore } from '@/store/projectScanStore'
import { usePrepSurface } from '@/hooks/usePrepSurface'
import {
  argsSchemaOf,
  draftDiffers,
  reusedArgCount,
  scriptTarget,
  useProjectPreparationStore,
  type EditRender,
  type PrepEntry,
} from '@/store/projectPreparationStore'
import { exactPanelRender, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { enterEdit, noteAddedPanelFor, type EnterEditHost } from '@/store/prepEnterEdit'
import { findFigurePanel } from '@/store/documentStore'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { useAiFixStore } from '@/store/aiFixStore'
import { useConvertStore } from '@/store/convertStore'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useForeignScriptStore } from '@/store/foreignScriptStore'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { useScriptInputStore } from '@/store/scriptInputStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useUiStore } from '@/store/uiStore'
import { ProbeResultsDialog } from './left/ScriptLibrary'
import { OriginReasonCounts } from './OriginDetails'
import { ScriptArgsForm } from './ScriptArgsForm'
import { ArgvTokens, PasteCommand } from './ScriptArgvEditor'
import { isSecret, suggestionText, useScriptInputAnswer, type ScriptInputAnswer } from './ScriptInputForm'
import { TaskDiagnostic } from './TaskDiagnostic'
import { SendReportButton } from './SendReportButton'
import { CopyButton } from './settings/CopyButton'
import { Button, IconButton } from './ui/Button'
import { Details, Summary } from './ui/Details'
import { TextInput } from './ui/Input'
import { Menu, MenuItem } from './ui/Menu'
import { ProgressBar } from './ui/ProgressBar'
import { Radio } from './ui/Radio'

const pt = (key: string, values?: Record<string, unknown>) =>
  translate(`prep.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 展示面的名字（`scriptInputStore.claimPresentation`）：卡片展开且正展示那一问时，原对话框让开 */
const SURFACE = 'prep-card'

/** 把这个条目的会话持有者递给 `prepEnterEdit`（项目代际 + 记入编辑记录） */
const editHost = (entryKey: string): EnterEditHost => ({
  epoch: () => useProjectPreparationStore.getState().epoch,
  note: (assetId, render) => useProjectPreparationStore.getState().noteEditing(entryKey, assetId, render),
})

/**
 * 加进画布的图首次编辑渲染好了没有（这是前端自己观察到的事实，不是后端判据）：只认入口动作记下的那个面板、
 * 那把渲染键上的**非 stale 精确 manifest**（`exactPanelRender`）。按文件 id 扫会把 `markStale()` 留下的旧渲染、
 * 或同一文件别的 override 变体当成这一次。
 */
function useEditReady(renders: Record<string, EditRender>): boolean {
  return useRenderStore((s) =>
    Object.entries(renders).some(([assetId, r]) => {
      const found = findFigurePanel(assetId, r.panelId)
      return !!found && renderKeyOf(found.panel) === r.renderKey && exactPanelRender(s, found.panel) !== null
    }),
  )
}

/**
 * 准备引导卡（T13b，取代 T02 的顶部检查条与 T09 的准备面板；设计稿 v2）：画布工作面板右下的**一张**浮动卡，
 * 不占布局（页面不下移）。所有「准备并打开」的入口——导入即扫描发现了绘图脚本、素材库脚本行 ▶、接入中心逐行
 * 「试运行并连接」——都落在这张卡上。
 *
 * ```text
 * 准备 · 运行 · 编辑                        —  ×
 * 发现绘图脚本 spectrum.py
 * 详情 ▾                       稍后  [打开并运行]
 * ```
 *
 * * **一句话 + 一个黑色主按钮**（用户硬性要求「卡片一句话读懂」）：标题与主按钮只在 `lib/preparationText.ts`（会话）/
 *   `lib/projectScanText.ts`（扫描）；路径、用的是哪一套、运行目录、参数、错误原文、诊断都在「详情」里。
 * * **三种呈现**（`uiStore.guideCard`）：展开 / 角标 / 收起。「稍后」「—」「放到后台」缩成角标；角标或卡上的 × 才收起。
 *   缩成角标时只有脚本发问才自动展开（跑完、出错只改角标）；收起时等作答的 input 由原对话框接着问。
 * * **每个项目只自动弹一次**（`lib/guideCardSeen.ts`，本机按项目记）：扫描发现待准备的绘图脚本时弹；扫描中、没看全只出角标。
 *   **弹出时不建会话**——建会话就是检查（会起候选解释器），扫描阶段零执行；用户点「打开并运行」才建（检查完接着认领后端的 run_when_ready）。
 * * **按钮就是真实动作**：会话的动作来自报告（运行 / 安装 / 重新检查 / 停止），运行目录在卡里直接选（推荐项预选，确认后只
 *   重新检查、不运行），进入编辑用这次捕获的图；卡片不复制任何一个动作的实现。
 * * **进入编辑之后卡片自动收起**，工作区自己的「渲染完成」说结果。
 * * 本地开关关掉（`lib/preparationFlag`）时，「打开并运行」回到旧的同步试运行（`scriptRunStore.run`），其余照旧。
 */
export function PreparationCard() {
  useTranslation('workspace')
  const mode = useUiStore((s) => s.guideCard)
  const focus = useProjectPreparationStore((s) => s.focus)
  const entry = useProjectPreparationStore((s) => (s.focus ? s.entries[s.focus] : undefined))
  const scan = useProjectScanStore((s) => s.scan)
  const slow = useProjectScanStore((s) => s.slow)
  const forced = useProjectScanStore((s) => s.forced)
  const { hidden } = usePrepSurface()
  const lost = useProjectScanStore((s) => s.pollLost)
  const foreign = useForeignScriptStore((s) => s.offer !== null)
  const toolbarUp = useCanvasToolbarVisible()
  // 草稿变了要重新渲染（参数改了 → 「继续」）
  const drafts = useScriptArgvStore((s) => s.drafts)
  const editReady = useEditReady(entry?.editRenders ?? {})
  // 壳确认引擎退出了：断线那一句不再说「正在重连」（没人在重连），见 PrepContext.engineExited
  const engineExited = useExitNoticeStore((s) => s.engineExit !== null)
  const scanned = !entry && !hidden ? scanCard(scan, { slow, forced, lost }) : null

  let view: PrepView | null = null
  if (entry && !hidden) {
    const script = 'id' in entry.target ? null : entry.target.script
    const schema = argsSchemaOf(entry.report)
    const tokens = script ? (drafts[script]?.tokens ?? []) : []
    // 单一入口：表单关着时 missingRequirements 自己只认读得准的必填项，并照旧拦必选子命令
    const missingArgs = !schema
      ? null
      : tokens.length === 0 && reusedArgCount(entry) > 0
        ? 0
        : missingRequirements(schema, readTokens(schema, tokens), tokens).count
    view = prepView(entry, {
      argsChanged: draftDiffers(entry.target),
      editReady,
      missingArgs,
      engineExited,
    })
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

  // R / MATLAB 入口卡出现时让开别的引导卡：一次只有一张一句话卡片
  if (foreign || hidden || mode === 'closed' || (!view && !scanned)) return null
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
          // 以条目为 key：切换聚焦条目时整张卡体卸载重建，条目局部状态（本地失败、结果对话框、运行目录选择…）不带到另一个脚本
          <SessionCard key={entry.key} entry={entry} view={view} />
        )
      ) : scan && scanned ? (
        mode === 'pill' ? (
          <ScanPill scan={scan} card={scanned} />
        ) : (
          <ScanCard key={scan.scan_id} scan={scan} card={scanned} />
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
  /** 标题与这一步下面那颗显眼的次按钮「✦ 使用你的 AI 来修复」（失败卡；不是横条，不占整行以外的位置） */
  aiFix?: ReactNode
  /** 覆盖「×」的去向（默认收起整张引导卡）；R / MATLAB 入口卡的 × 是“不提议了”，并且没有“缩成角标” */
  onClose?: () => void
}

/** 卡片外壳：步骤 · 工具（— ⋯ ×）/ 图标 + 标题 / 这一步 / 详情 / 详情 · 稍后 · 主按钮 */
function Shell({ label, step, icon, title, attrs, slot, details, detailsLabel, more, ghost, primary, aiFix, onClose }: ShellProps) {
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
          {!onClose && (
            <IconButton
              iconSize="sm"
              label={pt('card.minimize')}
              data-prep-minimize
              className="text-ink-3"
              onClick={() => ui.setGuideCard('pill')}
            >
              <Minus size={ICON_SIZE.sm} />
            </IconButton>
          )}
          {more}
          <IconButton
            iconSize="sm"
            label={pt('card.close')}
            data-prep-close
            className="text-ink-3"
            onClick={() => (onClose ? onClose() : ui.setGuideCard('closed'))}
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
        {aiFix}
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

/** 不定态进度：唯一实现是 `ui/ProgressBar`（pct=null）；没有真数字就不画百分比 */
const Progress = () => <ProgressBar pct={null} label={pt('progress')} data-prep-progress />

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
  const [picked, setPick] = useState<string | null>(null)
  // 重新检查之后目标清单可能变了：用户点过的那个仍在清单里才算数，否则回到推荐 / 第一个（不拿旧文件名去开始准备）
  const pick =
    picked !== null && plots.some((x) => x.script === picked)
      ? picked
      : (scan.default_target ?? plots[0]?.script ?? null)
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
    // 「开始准备」就是明确的「打开并运行」：检查完、后端报告里有 run_when_ready 就接着认领（授权合同由 act 回显）
    void useProjectPreparationStore.getState().open(scriptTarget(script, { reuse: true }), { run: true })
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
            {pt('btn.openRun')}
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
  'origin_confirm',
  'origin_unknown',
  'gui_dialog',
  'restarted',
  'rejected',
  'failed',
  'no_figure',
  'no_figure_raster',
  'completed',
  'completed_unlinked',
  'completed_partial',
  'cancelled',
])

/**
 * 「停止运行」：用户不感知运行层。这次运行此刻有桌面拥有的进程（`launch_id` = 报告里当前尝试的计划号、会话没结束）就结束它——
 * 后端同一个端点会连准备计划一起取消；否则（safe 阶段 / 还没起进程）认领准备会话的 `cancel` 动作。
 */
function useStopRun(entry: PrepEntry): () => void {
  const sessions = useNativeSessionStore((s) => s.sessions)
  return () => {
    const plan = entry.report?.provider.attempt_id
    const owned = plan
      ? Object.values(sessions).find((s) => s.owner === 'desktop' && s.launch_id === plan && !isNativeTerminal(s.state))
      : undefined
    if (owned) void useProjectPreparationStore.getState().stopOwned(entry.key, owned.session_id)
    else void useProjectPreparationStore.getState().act(entry.key, 'cancel')
  }
}

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
    case 'confirm_origins':
      return pt('btn.confirmOrigins')
    case 'open_environment':
      return pt('btn.openSettings')
    case 'pick_environment':
      return pt('btn.pickPython')
    case 'open_registry':
      return pt('btn.openRegistry')
    case 'open_assets':
      return pt('btn.openAssets')
    case 'background':
      return pt('btn.background')
    case 'dismiss':
      return pt('btn.dismiss')
    case 'convert':
      return pt('convert.btn.start')
    case 'ai_fix':
      return pt('btn.aiFix')
    case 'feedback':
      return pt('btn.send')
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
      case 'adopt': {
        // 成功：envStore 的环境变了 → 会话只读地重新检查（store 订阅），下一步仍由报告给出
        // 挂起期间可能切了项目：A 的采用失败文案不许落到 B 的卡片上
        const guard = captureProjectEpoch()
        const err = await useEnvStore.getState().adoptCandidate(p.candidate, targetName(entry))
        return guard.still() ? err : null
      }
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
        // 进入编辑的实现只有一份（`store/prepEnterEdit`）：与「打开并运行」完成后的自动进入共用
        await enterEdit(editHost(entry.key), figures[0])
        return null
      }
      case 'reopen':
        // 沿用上次成功参数的条目重新打开时仍沿用（目标构造点只有 scriptTarget，别在这里另拼）；草稿里有参数时用户给的赢
        await store.open(
          'id' in entry.target
            ? entry.target
            : scriptTarget(entry.target.script, { reuse: entry.target.reuse_last_success === true }),
          // 「继续」不是新的一次「打开并运行」：回到已跑完的同配置会话时复用结果，不重跑（registry-center-preparation e2e）
          { run: entry.wantsRun, rerun: false },
        )
        return null
      case 'confirm_origins': {
        // 名字不在会话报告里（只有计数与原因码）：点的时候取一份联合计划载荷，框里读 `origins[]`。取回来时已换了项目就整个丢弃
        const guard = captureProjectEpoch()
        try {
          const { offer } = await fetchDependencyOffer(report?.target.script ?? targetName(entry))
          if (!guard.still()) return null
          if (!offer) return pt('origin.unavailable')
          useDepRepairStore.getState().openOrigin(offer)
          return null
        } catch (e) {
          if (!guard.still()) return null
          return e instanceof Error && e.message ? e.message : pt('origin.unavailable')
        }
      }
      case 'open_environment':
        ui.setEngineEnvOpen(true)
        return null
      case 'pick_environment':
        ui.openEngineEnvPicker()
        return null
      case 'open_registry':
        useProjectReadinessStore.getState().openCenter({ source: 'panel' })
        return null
      case 'open_assets':
        // 脚本自己写出的图片文件，素材库本来就列（不新造导入入口）；卡片收起让位给素材库
        // 素材库挂载时不重载 `/api/panels`、项目 watcher 又要等轮询+防抖：刚写出的图不主动刷新就要等几秒才出现，
        // 按钮看起来像坏了。走素材库工具栏刷新按钮用的同一条统一刷新（不 await：切标签不等它，失败也不挡路）
        // 统一刷新自己持有扫描到强制清单结束的忙碌态，旧清单的 finally 不能提前撤下它。
        void refreshProjectNow()
          .catch(() => {})
          .finally(() => {
            void useRuntimeAssetStore.getState().loadAssets()
          })
        ui.setLeftTab('assets')
        ui.setGuideCard('closed')
        return null
      case 'background':
        ui.setGuideCard('pill')
        return null
      case 'dismiss':
        ui.setGuideCard('closed')
        return null
      case 'convert':
        // 不 await：生成要几十秒，进度与结果由卡片读 convertStore；失败也落在 store 里
        void useConvertStore.getState().start(p.script, p.library, p.path, p.language)
        return null
      case 'ai_fix':
        // 没连 AI：去设置的 AI 页连，连好回来自动接着修；已连：开始修复（aiFixStore 管，ADR 0148）。不 await：修复是后台任务
        if (report) {
          void useAiFixStore.getState().request(entry.key, {
            session_id: report.session_id,
            script: report.target.script ?? '',
            attemptId: report.provider.attempt_id,
          })
        }
        return null
      case 'feedback':
        // 打开发送问题反馈对话框（本机备包，逐次确认；源码复选框默认不勾——ADR 0118）
        useDiagSendStore.getState().setOpen(true)
        return null
    }
  }
}

/** 对照视图：左原图、右新版（SVG 预览）；版式数据（宽高比差）只当提示，不替用户判 */
function ConvertCompare({ entry }: { entry: NonNullable<ReturnType<typeof useConvertStore.getState>['entries'][string]> }) {
  const st = entry.status
  const cmp = st?.comparison
  const orig = cmp?.original?.path
  return (
    <div data-convert-compare className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        <figure className="m-0 flex min-w-0 flex-col gap-1">
          <div className="flex h-32 items-center justify-center overflow-hidden rounded-xs border border-border bg-surface-2">
            {orig ? <img data-convert-original src={convertOriginalUrl(orig)} alt={pt('convert.original')} className="max-h-full max-w-full object-contain" /> : null}
          </div>
          <figcaption className="text-sm text-ink-3">{pt('convert.original')}</figcaption>
        </figure>
        <figure className="m-0 flex min-w-0 flex-col gap-1">
          <div className="flex h-32 items-center justify-center overflow-hidden rounded-xs border border-border bg-surface-2">
            {st?.has_preview && entry.session ? (
              <img data-convert-new src={convertPreviewUrl(entry.session)} alt={pt('convert.redrawn')} className="max-h-full max-w-full object-contain" />
            ) : (
              <span className="px-2 text-center text-sm text-ink-3">{pt('convert.noPreview')}</span>
            )}
          </div>
          <figcaption className="text-sm text-ink-3">{pt('convert.redrawn')}</figcaption>
        </figure>
      </div>
      {cmp?.aspect_delta != null && cmp.aspect_delta > 0.02 ? (
        <p data-convert-aspect className="text-sm text-ink-3">
          {pt('convert.aspectDiffers', { pct: Math.round(cmp.aspect_delta * 100) })}
        </p>
      ) : null}
      {/* R / MATLAB 来源从不被运行：没有原图可对照就如实说“未对照验证”，让用户并排看一眼再采用 */}
      {st?.source_language && st.source_language !== 'python' && cmp?.verification === 'unknown' ? (
        <p data-convert-unverified className="text-sm text-ink-2">
          {pt('convert.unverified', { product: PRODUCT_NAME })}
        </p>
      ) : null}
      {st?.installed && st.installed.length > 0 ? (
        <p data-convert-installed className="text-sm text-ink-3">
          {pt('convert.installed', { names: listJoin(st.installed) })}
        </p>
      ) : null}
    </div>
  )
}

/** 依赖卡片里的环境可读名：`conda: myenv` / `python: myenv`（后端只给种类 + 目录名，没有路径） */
function depsEnvLabel(env: { kind: string; name: string }): string {
  if (env.kind === 'managed') return pt('convert.deps.env.managed', { product: PRODUCT_NAME })
  const prefix = env.kind === 'conda' ? 'conda' : env.kind === 'project_venv' ? 'venv' : 'python'
  return `${prefix}: ${env.name}`
}

/** 生成可编辑版本进行中 / 有结果时卡片的样子：一句话 + 一个主按钮（+ 一颗次按钮）。采用 = 后端新建兄弟脚本，再走「打开并运行」 */
function convertFlow(
  c: NonNullable<ReturnType<typeof useConvertStore.getState>['entries'][string]>,
  p: Extract<PrepPrimary, { kind: 'convert' }>,
  entry: PrepEntry | undefined,
): { title: string; slot?: ReactNode; primary?: ReactNode; ghost?: ReactNode } {
  const store = useConvertStore.getState()
  const retry = () => void store.start(p.script, p.library, p.path, p.language)
  switch (c.phase) {
    case 'running':
      if (c.stopping) {
        // 停止被拒 / 只转交给别的进程：结局未定，等对账——只有一句话、没有按钮（候选可能马上 ready）
        return { title: pt('convert.deps.stopping'), slot: <Progress /> }
      }
      return {
        title: pt('convert.running'),
        slot: <Progress />,
        ghost: (
          <Button variant="ghost" size="md" data-convert-stop className="text-ink-2" onClick={() => void store.stop(p.script)}>
            {pt('btn.stop')}
          </Button>
        ),
      }
    case 'deps': {
      // 新脚本缺 Python 包、要用户先决定：一句话 + 一个主按钮（+「不装」）。三种卡：问一次 / 名单外点名 / 系统 Python
      const card = c.card
      if (!card) return { title: pt('convert.fail.unknown') }
      const env = depsEnvLabel(card.env)
      const decline = (
        <Button variant="ghost" size="md" data-convert-decline className="text-ink-2" onClick={() => void store.discard(p.script)}>
          {pt('convert.deps.btn.decline')}
        </Button>
      )
      if (card.kind === 'system') {
        return {
          title: pt(card.env.kind === 'builtin' ? 'convert.deps.systemBuiltin' : 'convert.deps.system', { product: PRODUCT_NAME }),
          primary: (
            <Button
              variant="primary"
              size="md"
              data-convert-choose-env
              data-prep-primary="convert_choose_env"
              onClick={() => store.chooseEnv(p.script)}
            >
              {pt('convert.deps.btn.chooseEnv')}
            </Button>
          ),
          ghost: decline,
        }
      }
      const uncommon = card.kind === 'uncommon'
      const title = uncommon
        ? pt('convert.deps.uncommon', { names: listJoin(card.outside_list.length ? card.outside_list : card.packages) })
        : pt(card.action === 'install_managed' ? 'convert.deps.askOnceManaged' : 'convert.deps.askOnce', {
            env,
            product: PRODUCT_NAME,
          })
      return {
        title,
        primary: (
          <Button
            variant="primary"
            size="md"
            data-convert-deps-install={card.kind}
            data-prep-primary="convert_deps_install"
            onClick={() => void store.resolveDeps(p.script)}
          >
            {pt(uncommon ? 'convert.deps.btn.installOnce' : 'convert.deps.btn.installAlways')}
          </Button>
        ),
        ghost: decline,
      }
    }
    case 'resuming':
      if (c.stopping) {
        // 停止可能来得太晚：等后端回话再决定清卡还是显示「依赖已装好」，期间只有一句话、没有可点的按钮
        return { title: pt('convert.deps.stopping'), slot: <Progress /> }
      }
      return {
        title: c.replanning
          ? pt('convert.deps.replanning')
          : c.card?.packages.length
            ? pt('convert.deps.resuming', { names: listJoin(c.card.packages) })
            : pt('convert.deps.continuing'),
        slot: <Progress />,
        ghost: (
          <Button variant="ghost" size="md" data-convert-stop className="text-ink-2" onClick={() => void store.stop(p.script)}>
            {pt('btn.stop')}
          </Button>
        ),
      }
    case 'installed':
      // 停止来得太晚：依赖已经装好（撤不回），候选保留；不自动往下跑，由用户点「继续」
      return {
        title: pt('convert.deps.installedLate'),
        primary: (
          <Button
            variant="primary"
            size="md"
            data-convert-continue
            data-prep-primary="convert_continue"
            onClick={() => void store.continueAfterInstall(p.script)}
          >
            {pt('convert.deps.btn.continue')}
          </Button>
        ),
        ghost: (
          <Button variant="ghost" size="md" data-convert-discard className="text-ink-2" onClick={() => void store.discard(p.script)}>
            {pt('convert.btn.discard')}
          </Button>
        ),
      }
    case 'ready':
    case 'adopting':
      return {
        title: pt(c.stoppedLate ? 'convert.stoppedLate' : 'convert.ready'),
        slot: <ConvertCompare entry={c} />,
        primary: (
          <Button
            variant="primary"
            size="md"
            data-convert-adopt
            data-prep-primary="convert_adopt"
            loading={c.phase === 'adopting'}
            disabled={c.phase === 'adopting'}
            onClick={async () => {
              const made = await store.adopt(p.script)
              if (!made) return
              // 采用规则（ADR 0139 PR1 同款）：新脚本另起面板，原位图面板不动——用既有的「打开并运行」接进画布
              void useProjectPreparationStore.getState().open(scriptTarget(made), { run: true, rerun: false })
              store.clear(p.script)
            }}
          >
            {pt('convert.btn.adopt')}
          </Button>
        ),
        ghost: (
          <Button variant="ghost" size="md" data-convert-discard className="text-ink-2" disabled={c.phase === 'adopting'} onClick={() => void store.discard(p.script)}>
            {pt('convert.btn.discard')}
          </Button>
        ),
      }
    case 'failed': {
      // Agent 停下说明“Python 里没有可靠对应”：再试一次是同样的结论，主按钮是“知道了”（收起），不是重试
      const needsInput = c.reason === 'needs_input'
      return {
        title:
          c.error ??
          (needsInput && c.detail
            ? pt('convert.fail.needs_input_reason', { reason: c.detail })
            : pt(`convert.fail.${c.reason}`, { defaultValue: pt('convert.fail.unknown') })),
        primary: needsInput ? (
          <Button
            variant="primary"
            size="md"
            data-convert-dismiss
            data-prep-primary="convert_dismiss"
            onClick={() => {
              store.clear(p.script)
              useForeignScriptStore.getState().clear()
            }}
          >
            {pt('btn.dismiss')}
          </Button>
        ) : (
          <Button variant="primary" size="md" data-convert-retry data-prep-primary="convert_retry" onClick={retry}>
            {pt('convert.btn.retry')}
          </Button>
        ),
        ghost: needsInput ? undefined : <SendReportButton variant="ghost" size="md" />,
      }
    }
    case 'adopted':
      return { title: pt('convert.adopted') }
  }
  void entry
}

/**
 * R / MATLAB 脚本的入口卡（ADR 0143 修订）：项目里 / 拖入 / 打开的是 `.R` / `.r` / `.m` 时，一句话 + 一个主按钮。
 * 点「生成可编辑版本」后同一张卡接着走既有的生成 / 对照 / 采用流水（`convertFlow`）；原脚本不动。
 * 它不是准备会话的一部分（Tavotto 不运行 R / MATLAB，没有报告可读），所以单独摆在画布右下，并在它出现时让开别的引导卡。
 */
export function ForeignScriptCard() {
  useTranslation('workspace')
  const offer = useForeignScriptStore((s) => s.offer)
  const convert = useConvertStore((s) => (offer ? s.entries[offer.script] : undefined))
  const toolbarUp = useCanvasToolbarVisible()
  const { hidden } = usePrepSurface()
  // 采用完成：提议到此为止（否则卡片会退回“生成可编辑版本”再提议一遍）
  useEffect(() => {
    if (convert?.phase === 'adopted') useForeignScriptStore.getState().clear()
  }, [convert?.phase])
  /**
   * 续跑中点 ×：提议不能先清（卡片是 store 条目唯一的展示处，先卸载了，后端回 `deps_installed` 时「继续」无处可显）。
   * 只记一个「用户要关」，等 store 条目因对账而消失（候选已删）这一处才清提议；回到「继续」等卡则撤销这个意图。
   */
  const [closing, setClosing] = useState(false)
  useEffect(() => {
    if (!closing) return
    if (!convert) {
      useForeignScriptStore.getState().clear()
      setClosing(false)
    } else if (!convert.stopping) setClosing(false)
  }, [closing, convert])
  if (!offer || hidden) return null
  const language = offer.language
  const p: Extract<PrepPrimary, { kind: 'convert' }> = { kind: 'convert', script: offer.script, library: null, path: null, language }
  const flow = convert ? convertFlow(convert, p, undefined) : null
  const title = flow?.title ?? pt(`convert.foreign.${language}`, { product: PRODUCT_NAME })
  return (
    <div
      data-guide-anchor
      className={cn(
        'pointer-events-none absolute right-3 z-drawer flex max-h-[calc(100%-1.5rem)] w-[400px] max-w-[calc(100%-1.5rem)] flex-col items-end',
        toolbarUp ? 'bottom-16' : 'bottom-3',
      )}
    >
      <Shell
        label={pt('aria', { script: offer.script })}
        step={1}
        icon={flow ? undefined : <FileCodeCorner size={ICON_SIZE.md} className="shrink-0 text-ink-3" />}
        title={title}
        attrs={{ 'data-foreign-card': language, 'data-convert-phase': convert?.phase ?? 'offer' }}
        slot={flow?.slot}
        ghost={flow?.ghost}
        primary={
          flow?.primary ?? (
            <Button
              variant="primary"
              size="md"
              data-foreign-start
              data-prep-primary="convert"
              onClick={() => void useConvertStore.getState().start(offer.script, null, null, language)}
            >
              {pt('convert.btn.start')}
            </Button>
          )
        }
        onClose={() => {
          // 「暂时连不上」（failed + lost）后端可能仍在装 / 校验：和续跑中同一条停止 + 对账路径，不只清提议
          if (
            convert?.phase === 'resuming' ||
            (convert?.phase === 'running' && convert.session) ||
            (convert?.phase === 'failed' && convert.lost && convert.session)
          ) {
            // 对账期间不清提议：由上面的 effect 在条目消失时清（见 closing）
            if (!convert.stopping) {
              setClosing(true)
              void useConvertStore.getState().stop(offer.script)
            }
            return
          }
          if (convert?.phase === 'running' || convert?.phase === 'deps' || convert?.phase === 'installed')
            void useConvertStore.getState().stop(offer.script)
          useForeignScriptStore.getState().clear()
        }}
      />
    </div>
  )
}

function SessionCard({ entry, view: baseView }: { entry: PrepEntry; view: PrepView }) {
  useTranslation(['workspace', 'errors'])
  const report = entry.report
  // 点过「✦ 使用你的 AI 来修复」：AI 暂时修不了（`ai_unavailable`）就落成反馈卡；只对点的那一次尝试有效，重新运行后换回原卡
  const fix = useAiFixStore((s) => s.entries[entry.key])
  const view =
    fix?.phase === 'unavailable' && fix.ref.attemptId === (report?.provider.attempt_id ?? null)
      ? withAiUnavailable(baseView)
      : baseView
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
  const stopRun = useStopRun(entry)
  const pending = entry.pending !== null
  // 回答框、「回答」、⋯ 共用同一份作答状态（框里的字只在这里，不进 store）
  const answer = useScriptInputAnswer()

  const p = view.primary
  const disabled = view.primaryDisabled && !(p?.kind === 'workdir' && workdir !== null)
  const title =
    localError ??
    // 跑完没出 Matplotlib 图、但脚本自己用位图库写了图片：这句话独立成一组键（`prep.rasterHint.*`）
    (view.state === 'no_figure_raster'
      ? // 没有落盘文件（动态路径 / 只 show / 文件还没出现）：不说「画成图片」，说「画的、不能直接编辑」（ADR 0143 修订 2026-10-23）
        pt(report?.no_figure_hint?.path ? 'rasterHint.line' : 'rasterHint.lineNoFile', view.sentence.values)
      : pt(`line.${view.sentence.key}`, view.sentence.values))
  const originView = localError ? null : originViewOfCard(view.state)
  const attrs = {
    'data-prep-state': localError ? 'action_failed' : view.state,
    ...(originView ? { 'data-origin-view': originView } : {}),
    'data-prep-phase': report?.phase ?? '',
    'data-prep-session': report?.session_id ?? '',
    'data-prep-connection': entry.connection,
  }
  const scriptTargetName = 'id' in entry.target ? null : entry.target.script

  // 反馈卡的主按钮：复用设置页同一个入口（开关关着时不渲染）；其余主按钮是下面那颗普通 Button
  const feedbackPrimary = p?.kind === 'feedback' ? <SendReportButton variant="primary" size="md" label={pt('btn.send')} /> : null
  const fixBusy = fix?.phase === 'starting'
  const fixAction = async () => {
    setLocalError(null)
    const err = await runPrimary({ kind: 'ai_fix' }, workdir)
    if (err) setLocalError(err)
  }
  const primary = p && p.kind !== 'feedback' && (
    <Button
      variant="primary"
      size="md"
      data-prep-primary={primaryId(p)}
      loading={pending || (p.kind === 'ai_fix' && fixBusy)}
      disabled={pending || disabled}
      onClick={async () => {
        setLocalError(null)
        const err = await runPrimary(p, workdir)
        if (err) setLocalError(err)
      }}
    >
      {p.kind === 'ai_fix' && <Sparkles size={ICON_SIZE.sm} />}
      {primaryLabel(p, workdir)}
    </Button>
  )
  // 失败了、而且没有任何可执行的修复动作（未知 / 无法自动处理）：发送问题反馈就是这张卡的主按钮
  // （开关关着时 SendReportButton 不渲染）。有主按钮的失败卡把它放进折叠详情（TaskDiagnostic 里那一枚）。
  const failedNoAction =
    !p && report?.outcome.kind === 'failed' && report.outcome.reason !== 'dependency_preparation'
  const fallbackPrimary = failedNoAction ? <SendReportButton variant="primary" size="md" /> : null
  // 显眼的次按钮（维护者裁决：每次失败都出）：蓝色描边 + 图标集的 Sparkles（不拿 ✦ 字符当图标，iconography 门禁），颜色全走语义 token；主按钮仍只有一个
  const aiFix = view.aiFix ? (
    <Button
      variant="ghost"
      size="md"
      data-prep-ai-fix
      className="self-start text-accent inset-ring inset-ring-accent hover:bg-accent-subtle"
      loading={fixBusy}
      disabled={pending}
      onClick={fixAction}
    >
      <Sparkles size={ICON_SIZE.sm} />
      {pt('btn.aiFix')}
    </Button>
  ) : null
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
        onClick={stopRun}
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
  // 上一次尝试在沙盒里落空、脚本目录里找得到（#1254）：不论卡片是哪种失败 / 没出图的状态，都多一个「选择运行目录…」入口
  // （同一个确认框；提示句按 decided / source 选，逻辑在 WorkdirChooseButton 一处）
  const hintReq = report?.requirements.find((r) => r.kind === 'workdir_hint')
  if (hintReq?.kind === 'workdir_hint' && report?.outcome.kind !== 'pending') {
    slot.push(<WorkdirChooseButton key="workdir-hint" confirmation={hintReq.payload} />)
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
  const convertScript = p?.kind === 'convert' ? p.script : null
  const convert = useConvertStore((s) => (convertScript ? s.entries[convertScript] : undefined))
  const convertUi = convertScript && p?.kind === 'convert' && convert ? convertFlow(convert, p, entry) : null
  if (convertUi) {
    return (
      <Shell
        label={pt('aria', { script })}
        step={view.step}
        icon={toneIcon(view.tone, view.state)}
        title={convertUi.title}
        attrs={{ ...attrs, 'data-convert-phase': convert!.phase }}
        slot={convertUi.slot}
        details={details}
        ghost={convertUi.ghost}
        primary={convertUi.primary}
      />
    )
  }
  const secondary =
    view.secondary && (
      <Button
        variant="ghost"
        size="md"
        data-prep-alt={primaryId(view.secondary)}
        className="text-ink-2"
        onClick={async () => {
          const err = await runPrimary(view.secondary!, workdir)
          if (err) setLocalError(err)
        }}
      >
        {primaryLabel(view.secondary, workdir)}
      </Button>
    )
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
        ghost={secondary || ghost}
        aiFix={aiFix}
        primary={ownsInput ? <AnswerButton answer={answer} /> : (feedbackPrimary ?? primary ?? fallbackPrimary)}
      />
      {report && (report.captured ?? []).length > 1 && (
        <ProbeResultsDialog
          script={script}
          descriptors={report.captured ?? []}
          dropped={0}
          open={resultsOpen}
          onAdded={(d, added) => noteAddedPanelFor(editHost(entry.key), d.asset_id, added)}
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
  const stopRun = useStopRun(entry)
  const stop =
    view.ghost === 'stop' ? (
      <button
        type="button"
        data-prep-pill-stop
        className="shrink-0 rounded-full px-1.5 text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
        onClick={stopRun}
      >
        {pt('btn.stop')}
      </button>
    ) : undefined
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
        ) : (
          stop
        )
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
  // 沿用上次成功的参数（#982）：只说个数（值不回前端），旁边一个文字钮「不沿用」——重新按无参数检查，不替用户运行
  const reused = reusedArgCount(entry)
  const args =
    reused > 0
      ? pt('detail.argsReused', { count: reused })
      : argv.length === 0
        ? pt('detail.argsNone')
        : 'argv_sensitive' in target && target.argv_sensitive
          ? pt('detail.argsHidden', { count: argv.length })
          : argv.map((t) => (/[\s"']/.test(t) || t === '' ? JSON.stringify(t) : t)).join(' ')
  return (
    <div className="flex items-baseline gap-3" data-prep-ready-line>
      <p className="min-w-0 flex-1 truncate text-sm text-ink-2">
        {mode && (
          <>
            {pt(WORKDIR_LABEL[mode as WorkdirMode] ?? 'workdir.project_root')}
            {' · '}
          </>
        )}
        <span className={reused > 0 ? undefined : 'font-mono'} data-prep-args-summary>
          {args}
        </span>
      </p>
      {reused > 0 && !('id' in target) && (
        <button
          type="button"
          data-prep-reuse-clear
          disabled={entry.pending !== null}
          className="shrink-0 rounded-xs text-sm text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring disabled:opacity-40"
          onClick={() => void useProjectPreparationStore.getState().open(scriptTarget(target.script), { run: false })}
        >
          {pt('btn.clearReuse')}
        </button>
      )}
    </div>
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
/** 详情里的弹窗提示：怎么改（把路径 / 答案写进脚本）+ 在哪几行、是哪个调用。标题那一句话已经说了结论，这里只放细节 */
function GuiDialogHint({ payload }: { payload: GuiDialogPayload }) {
  const { file, ask } = dialogKinds(payload)
  return (
    <div className="flex flex-col gap-1.5" data-prep-dialog>
      {file && <p data-prep-dialog-how="file">{pt('dialog.howFile')}</p>}
      {ask && <p data-prep-dialog-how="prompt">{pt('dialog.howAsk')}</p>}
      {payload.calls.map((c, i) => (
        <Row
          key={`${i}:${c.line}:${c.api}`}
          k={pt('dialog.where', { line: c.line })}
          v={<span className="font-mono">{c.api}</span>}
          attr="dialog-call"
        />
      ))}
      {payload.truncated && <p data-prep-dialog-more>{pt('dialog.more')}</p>}
    </div>
  )
}

const HISTORY_PROFILES = ['safe', 'native']
const HISTORY_OUTCOMES = ['pending', 'succeeded', 'failed', 'blocked', 'cancelled']
const HISTORY_DECLINED = [
  'project_reopened',
  'superseded',
  'not_desktop_initiated',
  'fallback_disabled',
  'policy_safe_only',
  'not_blocked',
  'cancelled',
  'plan_stale',
  'already_fell_back',
  'native_to_safe',
  'no_new_root_cause',
  'not_runnable',
  'interpreter_changed',
  'plan_unavailable',
  'launch_failed',
]

/** 闭集码翻成人话；后端以后新增的码（旧前端不认识）原样显示，不丢也不猜 */
const closedText = (set: string[], group: string, code: string): string =>
  set.includes(code) ? pt(`detail.history.${group}.${code}`) : code

/**
 * 运行记录（ADR 0127）：这个会话里每次真实执行尝试——用的是安全运行还是原样运行、怎么收场、本可换却没换的理由。
 * 只在详情里（折叠）；全是后端的闭集码，没有路径 / 参数。只有一次且没有「没换」的理由时不显示（没有可说的）。
 */
function RunHistory({ history }: { history: PreparationAttempt[] }) {
  useTranslation('workspace')
  if (history.length < 2 && !history.some((a) => a.declined)) return null
  return (
    <div className="flex flex-col gap-1" data-prep-history>
      <span className="text-ink-3">{pt('detail.history.title')}</span>
      {history.map((a, i) => (
        <Row
          key={a.attempt_id}
          k={`${pt('detail.history.attempt', { n: i + 1 })} · ${closedText(HISTORY_PROFILES, 'profile', a.profile)}`}
          v={a.declined ? closedText(HISTORY_DECLINED, 'declined', a.declined) : closedText(HISTORY_OUTCOMES, 'outcome', a.outcome)}
          attr="history"
        />
      ))}
    </div>
  )
}

function SessionDetails({ entry, view }: { entry: PrepEntry; view: PrepView }) {
  useTranslation(['workspace', 'errors'])
  const report = entry.report
  const store = useProjectPreparationStore.getState()
  const mode = useEnvStore((s) => s.env?.project?.workdir?.mode ?? null)
  const wdReq = report?.requirements.find((r) => r.kind === 'workdir_choice')
  const dialogReq = report?.requirements.find((r) => r.kind === 'gui_dialog')
  const dialog = dialogReq?.kind === 'gui_dialog' ? dialogReq.payload : null
  const prepare = report?.actions.find((a) => a.kind === 'prepare_dependencies')
  const error = report?.result?.error
  const attempt = report?.provider.attempt_id
  const dep = report?.provider.dependency
  const originFacts = originFactsOf(report)
  const fallbackNote = nativeFallbackNote(report ?? null)
  const env = report?.environment
  const overlayLabel = env?.overlay?.label ?? ''
  const notUsed = visibleNotUsed(env?.not_used)
  const primaryAction = view.primary?.kind === 'action' ? view.primary.action : null
  const recheck = primaryAction !== 'recheck' && report?.actions.some((a) => a.kind === 'recheck')
  const target = entry.target
  const argc = 'id' in target ? 0 : (target.argv?.length ?? 0)
  const failed = report?.outcome.kind === 'failed' && report.outcome.reason !== 'dependency_preparation'
  // 错误原文：traceback 的最后一行（脚本自己的异常那一句）优先，没有 traceback（敏感运行 / 老后端）用后端的主文案；
  // 默认只露这一句加最后一帧，完整 traceback 在下面的折叠里可展开 / 复制（P05a）
  const traceback = error?.traceback ?? ''
  const tbLines = traceback.split('\n').map((l) => l.trim()).filter(Boolean)
  const original = error ? String(tbLines[tbLines.length - 1] ?? error.message ?? '') : ''
  const frame = tbLines.filter((l) => l.startsWith('File ')).pop()
  const errParams = error?.params
  const parseKind = failed && error?.code === 'script_needs_arguments' ? errParams?.parse_kind : undefined
  const prompt = failed && (error?.code === 'script_needs_input' || error?.code === 'script_input_timeout') ? errParams?.prompt : undefined
  return (
    <>
      {dialog && <GuiDialogHint payload={dialog} />}
      {failed && original && (
        <pre
          data-prep-error={error?.code ?? ''}
          className="whitespace-pre-wrap break-words rounded-md bg-danger-subtle px-2.5 py-2 font-mono text-sm text-danger"
        >
          {original}
          {frame ? `\n  ${frame}` : ''}
        </pre>
      )}
      {failed && traceback && tbLines.length > 1 && (
        <Details data-prep-traceback>
          <Summary className="type-meta">{pt('detail.errorFull')}</Summary>
          <div className="mt-1.5 flex flex-col gap-1.5">
            <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-2 px-2.5 py-2 font-mono text-xs text-ink-2">
              {traceback}
            </pre>
            {error?.traceback_truncated && <p className="type-caption">{pt('detail.errorTruncated')}</p>}
            <div>
              <CopyButton text={traceback} />
            </div>
          </div>
        </Details>
      )}
      {parseKind && (
        <Row
          k={pt('detail.parseKind')}
          v={
            ['missing_required', 'invalid_value', 'unknown'].includes(parseKind)
              ? pt(`detail.parseKinds.${parseKind}`)
              : parseKind
          }
          attr="parse-kind"
        />
      )}
      {prompt && <Row k={pt('detail.prompt')} v={<span className="font-mono">{prompt}</span>} attr="input-prompt" />}
      {/* 那一次的诊断（T04）：失败尝试的冻结快照，不含路径和参数 */}
      {attempt && failed && <TaskDiagnostic key={attempt} kind="preparation" refId={attempt} folded={false} />}
      {dep && report?.outcome.reason === 'dependency_preparation' && (
        <TaskDiagnostic key={dep.plan_id} kind="dependency" refId={dep.plan_id} folded={false} />
      )}
      {view.state === 'no_figure' && <p data-prep-nofigure-why>{pt('detail.noFigureWhy')}</p>}
      {view.state === 'no_figure_raster' && (
        <p data-prep-nofigure-raster-why>
          {pt(report?.no_figure_hint?.path ? 'rasterHint.why' : 'rasterHint.whyNoFile')}
        </p>
      )}
      {(report?.failed_figures ?? []).length > 0 && report?.phase === 'partial' && (
        <p data-prep-failed-figures>
          {pt('detail.failedFigures', {
            names: listJoin((report.failed_figures ?? []).map((f) => f.stem)),
            codes: listJoin([...new Set((report.failed_figures ?? []).map((f) => f.code).filter((c): c is string => !!c))]),
          })}
        </p>
      )}
      {(report?.unlinked_stems ?? []).length > 0 && finishedWithFigures(report) && (
        <p data-prep-unlinked>{pt('detail.unlinked', { names: listJoin(report.unlinked_stems ?? []) })}</p>
      )}
      {(report?.attempt_history?.length ?? 0) > 0 && <RunHistory history={report?.attempt_history ?? []} />}
      {report?.target.script && <Row k={pt('detail.script')} v={<span className="font-mono">{report.target.script}</span>} attr="script" />}
      {env?.kind && (
        <Row
          k={pt('detail.usedBy')}
          v={
            pt(`env.${env.kind}`) +
            (overlayLabel ? ` + ${overlayLabel}` : '') +
            (env.switched && env.replaced ? ` ${pt('env.switched')}` : '')
          }
          attr="environment"
        />
      )}
      {notUsed.length > 0 && (
        <div className="flex flex-col gap-1" data-prep-env-not-used>
          <span className="text-ink-3">{pt('detail.notUsedTitle')}</span>
          {notUsed.map((r) => (
            <p key={`${r.reason}|${r.label}|${r.root_version ?? ''}`} data-prep-env-not-used-reason={r.reason}>
              {envNotUsedSentence(r)}
            </p>
          ))}
          <Button
            variant="secondary"
            size="sm"
            className="self-start"
            data-prep-open-environment
            onClick={() => useUiStore.getState().setEngineEnvOpen(true)}
          >
            {translate('scripts.openEnvSettings', { ns: 'workspace' })}
          </Button>
        </div>
      )}
      {mode && !wdReq && <Row k={pt('detail.workdir')} v={pt(WORKDIR_LABEL[mode as WorkdirMode] ?? 'workdir.project_root')} attr="workdir" />}
      {report?.target.kind === 'script' && (
        <Row
          k={pt('detail.args')}
          v={reusedArgCount(entry) > 0 ? pt('detail.argsReused', { count: reusedArgCount(entry) }) : argc ? pt('detail.argsCount', { count: argc }) : pt('detail.argsNone')}
          attr="args"
        />
      )}
      {wdReq && <p>{pt('workdir.writes')}</p>}
      {wdReq?.kind === 'workdir_choice' && (wdReq.payload?.conflicts.length ?? 0) > 0 && (
        <p>{pt('workdir.conflicts', { files: (wdReq.payload?.conflicts ?? []).join(', ') })}</p>
      )}
      {originFacts && <OriginReasonCounts facts={originFacts} />}
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
      {fallbackNote && (
        <p data-prep-native-fallback>
          {pt(fallbackNote.noWriteback ? 'detail.nativeFallbackNoWriteback' : 'detail.nativeFallback')}
        </p>
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
        ) : head.native ? null : (
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

