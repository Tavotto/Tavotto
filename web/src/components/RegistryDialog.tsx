import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Braces,
  CircleCheck,
  CircleDashed,
  CircleMinus,
  Ellipsis,
  Play,
  Plus,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react'
import { Details, Summary } from '@/components/ui/Details'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  backendCodeMsg,
  backendErrorText,
  fetchRegistry,
  panelSrc,
  probeScript,
  scanRegistry,
  writeRegistryEntry,
  type CapturedFigureDescriptor,
  type ReadinessPanel,
  type ReadinessReport,
  type ReadinessStatus,
  type RegistryView,
  type ScriptInventoryEntry,
} from '@/lib/api'
import {
  PENDING_STATUSES,
  allEditable,
  pendingCount,
  statusLabel,
} from '@/lib/readinessText'
import { cn } from '@/lib/utils'
import { formatMessage, msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { addPanel, addRuntimePanel } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { refreshAssetsAndSync } from '@/store/liveSync'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { EmptyState } from './ui/EmptyState'
import { Menu, MenuItem, MenuSub } from './ui/Menu'
import { Select } from './ui/Select'
import { TextInput } from './ui/Input'

/**
 * 项目接入状态（Prompt 08）。
 *
 * 它回答的是普通用户真正会问的那句话——**「这张图我能不能改？不能的话我该做
 * 什么？」**——主语是那一张图，不是脚本、不是 stem、不是那份记录文件。
 *
 * 事实来自后端一次计算（`GET /api/project/readiness`），界面**只翻译不判断**：
 * 六个状态与十个 reason code 是闭集，这里不许按 `script` 有没有值再猜一遍
 * （改造前三个界面各判一遍，同一张图得到三种答案）。
 *
 * 三条既有的执行路径原样复用，本组件一条都不重写：
 *
 * * 重新扫描 → `POST /api/registry/scan`
 * * 试运行   → `POST /api/registry/probe`（**只由用户点出来**，绝不自动跑）
 * * 手工关联 → `PUT /api/registry`
 *
 * 每次成功之后走**统一刷新**（`refreshAssetsAndSync`），不手拼状态：就绪度、
 * 素材清单、画布上面板的派生元数据由那一条路径一并更新。
 *
 * ### 呈现（2026-09-11 Visual Consolidation Session 4）
 *
 * 一张图一行：`[缩略图] 文件名 / ✓ 状态 · 脚本名   [主动作] ⋯`。一行只有**一个**
 * 主动作（这个状态下最可能对的那一个）；重新试运行、改绑脚本这类低频动作收进
 * 行尾的 ⋯ 菜单——三张可编辑的图各摆四颗按钮，看起来像一页运维面板，而它们其实
 * 都已经好了。顶部全部就绪时只说一句「N 张图已就绪」。
 *
 * 文件名与导出名保留为 `RegistryDialog`：`uiStore.registryOpen` 是这个对话框
 * 唯一的开关，项目菜单与设置页都在用它。再造一个同义标志等于给同一件事两个
 * 出处，而"标题改了"是文案的事，不是身份的事。
 */

/** 本对话框的文案在 dialogs:readiness.* 下 */
const rd = (key: string, values?: Record<string, unknown>) =>
  translate(`readiness.${key}`, { ns: 'dialogs', ...(values ?? {}) })
/** 状态标签 / 一句话原因与素材面板共用 workspace:readiness.*（见 lib/readinessText） */

export function RegistryDialog() {
  useTranslation('dialogs')
  const open = useUiStore((s) => s.registryOpen)
  if (!open) return null
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v) useProjectReadinessStore.getState().closeCenter()
      }}
      title={rd('title')}
      description={rd('subtitle')}
      size="lg"
      anchor="readiness"
    >
      <ReadinessBody />
    </Dialog>
  )
}

/** 一次试运行的界面记录：主文案按 code 翻成当前语言，traceback 只进诊断详情 */
interface ProbeNote {
  text: string
  traceback?: string
  /** 成功时：本次捕获的描述符（「添加到画布」把它们放成 runtime 面板） */
  descriptors?: CapturedFigureDescriptor[]
}

function ReadinessBody() {
  useTranslation('dialogs')
  const report = useProjectReadinessStore((s) => s.report)
  const loading = useProjectReadinessStore((s) => s.loading)
  const loadError = useProjectReadinessStore((s) => s.error)
  const focusId = useProjectReadinessStore((s) => s.focusId)

  /** 注册表全视图：只为「入口函数」与高级段（全部脚本）服务，不参与状态判定 */
  const [view, setView] = useState<RegistryView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [probed, setProbed] = useState<Record<string, ProbeNote>>({})

  const reloadView = async () => {
    try {
      setView(await fetchRegistry())
    } catch (e) {
      // 高级段取不回来**不算这个对话框失败**：主体那份事实来自另一个端点，
      // 它在的话每一行照常显示与操作
      setView(null)
      void e
    }
  }

  useEffect(() => {
    void useProjectReadinessStore.getState().load()
    void reloadView()
  }, [])

  /**
   * 一次会改动磁盘的动作。成功之后**只走统一刷新那一条路径**——就绪度、
   * 素材清单、画布上面板的派生元数据都在它后面，这里不手拼任何状态。
   */
  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key)
    setError(null)
    try {
      await fn()
      await reloadView()
      // `force`：用户刚写过盘，绝不能复用一个**写之前**就发出的在途请求
      await refreshAssetsAndSync({ force: true })
    } catch (e) {
      setError(backendErrorText(e))
    } finally {
      setBusy(null)
    }
  }

  const scan = () =>
    run('scan', async () => {
      const res = await scanRegistry()
      const n = res.changes.added_scripts.length
      useUiStore
        .getState()
        .setStatus(
          n
            ? msg('readiness.linkedCount', { count: n }, 'dialogs')
            : msg('readiness.nothingNew', undefined, 'dialogs'),
        )
    })

  const probe = (script: string) =>
    run(script, async () => {
      const res = await probeScript(script)
      if (res.error) {
        // 主文案先按稳定 code 翻成当前语言（后端中文原文只是回退）；
        // traceback 不进主文案，收在「诊断详情」里。
        const text = formatMessage(
          backendCodeMsg(res.error.code, res.error.params, res.error.message),
        )
        setProbed((p) => ({ ...p, [script]: { text, traceback: res.error?.traceback } }))
        throw new Error(rd('probeFailed', { script, error: text }))
      }
      const parts = [rd('probeLinked', { stems: listJoin(res.stems) })]
      if (res.dropped_figures) parts.push(rd('probeDropped', { count: res.dropped_figures }))
      setProbed((p) => ({
        ...p,
        [script]: { text: parts.join(' '), descriptors: res.descriptors },
      }))
    })

  const link = (panel: ReadinessPanel, script: string) =>
    run(`link:${panel.id}`, async () => {
      // **并进去，不是换掉**：这个脚本很可能已经认领了别的图（一个脚本产出
      // 多张是常态）。整条替换的话，用户点一下「接到这个脚本」就让同一个
      // 脚本的其它图全部失去编辑入口——而他只是想接上眼前这一张。
      // `cost` / `notes` 一个字都不传：不提 = 保留磁盘上原来那个值。
      await writeRegistryEntry({
        script,
        entry: entryOf(view, script),
        stems: [panel.stem],
        append: true,
      })
      useUiStore
        .getState()
        .setStatus(msg('readiness.linked', { name: fileName(panel.id) }, 'dialogs'))
    })

  /** 项目里全部脚本，供「手工选择脚本」用；取不回来时只剩候选 */
  const allScripts = useMemo(() => (view?.all_scripts ?? []).map((s) => s.script), [view])

  if (!report) {
    if (loadError) {
      return (
        <EmptyState
          icon={TriangleAlert}
          title={rd('loadFailed')}
          hint={loadError}
          action={{
            label: rd('retry'),
            onClick: () => void useProjectReadinessStore.getState().load({ force: true }),
          }}
        />
      )
    }
    return <p className="py-6 text-center text-xs text-ink-3">{rd('loading')}</p>
  }

  const groups: { key: 'pending' | 'editable' | 'layout_only'; panels: ReadinessPanel[] }[] = [
    {
      key: 'pending',
      panels: report.panels.filter((p) => PENDING_STATUSES.includes(p.status)),
    },
    { key: 'editable', panels: report.panels.filter((p) => p.status === 'editable') },
    { key: 'layout_only', panels: report.panels.filter((p) => p.status === 'layout_only') },
  ]

  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-h-7 items-center justify-between gap-3">
        <SummaryStrip report={report} />
        <Button
          variant="secondary"
          size="sm"
          className="shrink-0"
          disabled={busy !== null || !report.project.can_rescan}
          onClick={() => void scan()}
        >
          <RefreshCw size={ICON_SIZE.sm} className={cn(busy === 'scan' && 'animate-spin')} />
          {rd('rescan')}
        </Button>
      </div>

      <ProjectNotices report={report} staleError={loadError} refreshing={loading} />

      {error && (
        <p role="alert" className="text-xs leading-relaxed text-danger">
          {error}
        </p>
      )}

      {report.summary.total === 0 ? (
        <EmptyState icon={Braces} title={rd('emptyTitle')} hint={rd('emptyHint')} />
      ) : (
        groups.map(
          ({ key, panels }) =>
            panels.length > 0 && (
              <section key={key}>
                <h3 className="type-section mb-0.5 tabular-nums">
                  {rd(`group.${key}`, { n: panels.length })}
                </h3>
                <ul className="flex flex-col divide-y divide-border">
                  {panels.map((p) => (
                    <PanelRow
                      key={p.id}
                      panel={p}
                      allScripts={allScripts}
                      writable={report.project.writable}
                      busy={busy}
                      focused={focusId === p.id}
                      probed={probed}
                      onProbe={(script) => void probe(script)}
                      onLink={(script) => void link(p, script)}
                      onRescan={() => void scan()}
                    />
                  ))}
                </ul>
              </section>
            ),
        )
      )}

      {view && view.all_scripts.length > 0 && (
        <AllScriptsSection
          scripts={view.all_scripts}
          knownStems={[...new Set(report.panels.map((p) => p.stem))].sort()}
          busy={busy}
          probed={probed}
          writable={report.project.writable}
          onProbe={(script) => void probe(script)}
          onWriteStems={(script, stems) =>
            void run(script, async () => {
              await writeRegistryEntry({ script, entry: entryOf(view, script), stems })
            })
          }
          source={view.source}
        />
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  顶部：总计 / 可编辑 / 待连接 / 仅排版                                        */
/* -------------------------------------------------------------------------- */

function SummaryStrip({ report }: { report: ReadinessReport }) {
  useTranslation('dialogs')
  const s = report.summary
  // 与横幅**同一个加法**（`lib/readinessText.ts`）：两处各展开写一遍的话，
  // 将来多一个状态时总有一处会漏掉，而用户看到的是两个界面报出不同的数
  const pending = pendingCount(s)
  // 全都能编辑时说一句话，不摆四个格子（审计 T10：正常项目也像故障排查页）。
  // 判据与横幅共用一份（`lib/readinessText.allEditable`）。
  if (allEditable(s)) {
    return (
      <p className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-ink">
        <CircleCheck size={ICON_SIZE.sm} className="shrink-0 text-ok" aria-hidden />
        <span className="tabular-nums">{rd('allEditable', { count: s.total })}</span>
      </p>
    )
  }
  const cells: { key: string; value: number }[] = [
    { key: 'total', value: s.total },
    { key: 'editable', value: s.editable },
    { key: 'pending', value: pending },
    { key: 'layoutOnly', value: s.layout_only },
  ]
  return (
    <dl className="flex min-w-0 flex-wrap gap-x-4 gap-y-1">
      {cells.map((c) => (
        <div key={c.key} className="flex items-baseline gap-1">
          <dt className="type-meta">{rd(`summary.${c.key}`)}</dt>
          <dd className="text-xs tabular-nums text-ink">{c.value}</dd>
        </div>
      ))}
    </dl>
  )
}

/**
 * 项目级的说明。三件事各自独立，**不合并成一句「有问题」**：
 * 只读、记录文件读不回来、这一轮没扫成——用户能做的事完全不同。
 */
function ProjectNotices({
  report,
  staleError,
  refreshing,
}: {
  report: ReadinessReport
  staleError: string | null
  refreshing: boolean
}) {
  useTranslation('dialogs')
  const notes: string[] = []
  if (!report.project.writable) notes.push(rd('projectReadOnly'))
  // 有多个脚本声称、却还没有对应图文件的名字：它们没有自己的一行（这个界面
  // 的主语是图），但**不能就此消失**——脚本一跑出文件就会变成一条冲突。
  // `conflicts` 为 `null` 时这一段整个跳过：那是"这一轮没扫"，不是"没有冲突"。
  const withPanels = new Set(report.panels.map((p) => p.stem))
  const orphans = (report.conflicts ?? []).filter(
    (c) => c.resolved_by === null && !withPanels.has(c.stem),
  )
  if (orphans.length)
    notes.push(rd('orphanConflicts', { stems: listJoin(orphans.map((c) => c.stem)) }))
  // `null` = 项目里根本没有那份记录（还没起草过）——那是正常的空项目，
  // 与「有、但读不回来」是两回事，**不许压成一档**
  if (report.project.registry_valid === false) notes.push(rd('registryInvalid'))
  if (!report.project.scan_ok) notes.push(rd('scanUnavailable'))
  // 后台刷新失败：显示的是上一次成功那份，说清楚它可能已经旧了
  if (staleError && !refreshing) notes.push(rd('staleReport'))
  if (!notes.length) return null
  return (
    <ul className="flex flex-col gap-1 rounded-sm bg-surface-2 px-2 py-1.5">
      {notes.map((n) => (
        <li key={n} className="type-caption flex items-start gap-1.5">
          <TriangleAlert size={ICON_SIZE.sm} className="mt-px shrink-0 text-warn" aria-hidden />
          {n}
        </li>
      ))}
    </ul>
  )
}

/* -------------------------------------------------------------------------- */
/*  一行 = 一张图                                                              */
/* -------------------------------------------------------------------------- */

const fileName = (id: string) => id.split('/').pop() ?? id

/**
 * 这个脚本的入口函数名。三个出处从「最贴近它此刻的样子」往下找：
 * 已登记的那份 → 这一轮扫出来的候选 → 脚本清单解析出的入口。
 *
 * **一个都取不到就返回 `undefined`**，让后端用它自己的默认——前端在这里
 * 写一个 `'main'` 等于给同一个默认值造第二个出处。
 *
 * **别只查前两个**：静态解不出产物的脚本（`needs_probe` 那一档）不会出现在
 * `candidates` 里，而它的入口很可能不叫 `main`——写错了的表现是登记成功、
 * 下次渲染却找不到入口。
 */
function entryOf(view: RegistryView | null, script: string): string | undefined {
  const registered = view?.scripts[script]
  if (registered?.entry) return registered.entry
  const candidate = view?.candidates.find((c) => c.script === script)
  if (candidate?.entry) return candidate.entry
  return view?.all_scripts.find((s) => s.script === script)?.entry_candidates[0]
}

/**
 * 状态记号：12px 的一个图标 + 状态名，坐在元数据那一行里。
 * 颜色**不是唯一表达**——文字本身就是状态名；图标只分三档：好了 / 还差一步 /
 * 出了岔子（冲突、脚本丢了），仅排版是「没有源脚本、也不需要」的一横。
 */
const STATUS_MARK: Record<
  ReadinessStatus,
  { icon: typeof CircleCheck; tone: string }
> = {
  editable: { icon: CircleCheck, tone: 'text-ok' },
  auto_linkable: { icon: CircleDashed, tone: 'text-ink-3' },
  needs_probe: { icon: CircleDashed, tone: 'text-ink-3' },
  conflict: { icon: TriangleAlert, tone: 'text-danger' },
  source_missing: { icon: TriangleAlert, tone: 'text-danger' },
  layout_only: { icon: CircleMinus, tone: 'text-ink-faint' },
}

function StatusMark({ status }: { status: ReadinessStatus }) {
  useTranslation('workspace')
  const { icon: Icon, tone } = STATUS_MARK[status]
  return (
    <span className="flex shrink-0 items-center gap-1">
      <Icon size={ICON_SIZE.xs} className={tone} aria-hidden />
      {statusLabel(status)}
    </span>
  )
}

/**
 * 图卡的缩略图（审计 T10：缺少缩略图）。
 *
 * 走**现有**的那条渲染路径（`panelSrc` → `/api/render`），不新起一条：素材面板、
 * 多 Figure 选择器、导出面板用的都是它，缓存也是同一份。素材清单里还没有这一项
 * （就绪度扫描与素材遍历之间新出现 / 刚被删掉的那一档）时画一个占位方块——
 * **不猜一个地址**，猜出来的是一条 404。
 */
function PanelThumb({ panel }: { panel: ReadinessPanel }) {
  const asset = useAssetStore((s) => s.byId[panel.id])
  const src = asset ? panelSrc(asset.id, asset.kind, 200, asset.mtime) : null
  // 64×48 的一小格：行里的识别记号，不是看图器
  const box = 'aspect-[4/3] w-16 shrink-0 rounded-xs border border-border bg-white'
  if (!src) return <span className={cn(box, 'bg-surface-2')} aria-hidden />
  return <img src={src} alt="" className={cn(box, 'object-contain')} />
}

function PanelRow({
  panel,
  allScripts,
  writable,
  busy,
  focused,
  probed,
  onProbe,
  onLink,
  onRescan,
}: {
  panel: ReadinessPanel
  allScripts: string[]
  writable: boolean
  busy: string | null
  focused: boolean
  probed: Record<string, ProbeNote>
  onProbe: (script: string) => void
  onLink: (script: string) => void
  onRescan: () => void
}) {
  useTranslation('dialogs')
  const ref = useRef<HTMLLIElement>(null)
  const [highlight, setHighlight] = useState(false)
  const asset = useAssetStore((s) => s.byId[panel.id])
  const disabled = busy !== null

  // 「为什么不能编辑？」进来的那一次：滚到它、把焦点放上去、短暂高亮。
  // 高亮是**静态底色**不是动画——reduced-motion 下不需要另写一份。
  useEffect(() => {
    if (!focused) return
    const el = ref.current
    el?.scrollIntoView({ block: 'center' })
    el?.focus({ preventScroll: true })
    setHighlight(true)
    // 聚焦标记当场清掉：留着的话下次打开对话框还会再高亮一次同一行
    useProjectReadinessStore.getState().clearFocus()
    const timer = window.setTimeout(() => setHighlight(false), 1800)
    return () => window.clearTimeout(timer)
  }, [focused])

  // 这一行上刚跑过的那个脚本的结果：已绑定的优先，其次是候选里跑过的
  // 那一个。一行只显示一条——两条并排的话，用户分不出哪条对应刚才那次点击
  const note = [panel.script, ...panel.candidates]
    .map((s) => (s ? probed[s] : undefined))
    .find(Boolean)

  return (
    <li
      ref={ref}
      tabIndex={-1}
      data-panel-row={panel.id}
      className={cn(
        '-mx-2 rounded-sm px-2 py-2 outline-none transition-colors duration-fast focus-visible:focus-ring',
        highlight && 'bg-selected',
      )}
    >
      <div className="flex items-center gap-3">
        <PanelThumb panel={panel} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs text-ink" title={panel.id}>
            {fileName(panel.id)}
          </p>
          <p className="type-meta mt-0.5 flex min-w-0 items-center gap-1">
            <StatusMark status={panel.status} />
            {panel.script && (
              <>
                <span aria-hidden>·</span>
                <span className="min-w-0 truncate font-mono" title={panel.script}>
                  {panel.script}
                </span>
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <PrimaryAction
            panel={panel}
            disabled={disabled}
            busyKey={busy}
            hasAsset={!!asset}
            onAdd={() => {
              if (!asset) return
              addPanel(asset)
              useProjectReadinessStore.getState().closeCenter()
            }}
            onProbe={onProbe}
            onRescan={onRescan}
          />
          <MoreMenu
            panel={panel}
            allScripts={allScripts}
            writable={writable}
            disabled={disabled}
            onProbe={onProbe}
            onLink={onLink}
          />
        </div>
      </div>

      {/* 冲突：候选**逐个列出来**，一个都不替用户选。文件名更像"新版本"的
          那一个也不许赢——那是猜，而猜错的代价是用户此后每次编辑都改错脚本。
          这是唯一一种「一行不止一个动作」的状态，摆在第二行、缩进到文件名列 */}
      {panel.status === 'conflict' && panel.candidates.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5 pl-19">
          {panel.candidates.map((script) => (
            <Button
              key={script}
              variant="secondary"
              size="sm"
              disabled={disabled || !panel.can_manual_link}
              onClick={() => onLink(script)}
            >
              {rd('useScript', { script })}
            </Button>
          ))}
        </div>
      )}

      {/* 每张图的原因句不再逐条列出（2026-09-11 设计包）：状态在记号与分组标题里，
          原因仍由预检横幅与素材卡（`reasonText`）在别处说。试运行的那句提醒例外——
          它说的是「点下去会发生什么」 */}
      {panel.status === 'needs_probe' && (
        <p className="type-caption mt-1 pl-19">{rd('probeWarning')}</p>
      )}

      {note && (
        <div className="pl-19">
          <ProbeNoteView note={note} />
        </div>
      )}
    </li>
  )
}

/**
 * 每个状态的**那一个**主动作。
 *
 * 排列的规矩只有一条：**摆这个状态下最可能对的那一个**。
 * `conflict` 是唯一没有"最可能对"的——候选在行的第二行逐个列出，机器一个都不选。
 */
function PrimaryAction({
  panel,
  disabled,
  busyKey,
  hasAsset,
  onAdd,
  onProbe,
  onRescan,
}: {
  panel: ReadinessPanel
  disabled: boolean
  busyKey: string | null
  hasAsset: boolean
  onAdd: () => void
  onProbe: (script: string) => void
  onRescan: () => void
}) {
  useTranslation('dialogs')

  // 已经能编辑的那张只留一个动作：把它放上画布（审计 T10——「仅待连接项
  // 展示下一步」）。素材清单里没有它时**不渲染这个按钮**：就绪度扫描与素材
  // 遍历之间新出现 / 刚被删掉的那一档，点下去只会是一条错误
  if (panel.status === 'editable') {
    if (!hasAsset) return null
    return (
      <Button variant="secondary" size="sm" disabled={disabled} onClick={onAdd}>
        <Plus size={ICON_SIZE.sm} />
        {rd('addToCanvas')}
      </Button>
    )
  }

  if (panel.status === 'auto_linkable' || panel.status === 'source_missing') {
    return (
      <Button variant="secondary" size="sm" disabled={disabled} onClick={onRescan}>
        <RefreshCw size={ICON_SIZE.sm} className={cn(busyKey === 'scan' && 'animate-spin')} />
        {rd(panel.status === 'auto_linkable' ? 'autoLink' : 'rescan')}
      </Button>
    )
  }

  if (panel.status === 'needs_probe') {
    return (
      <ProbePicker
        candidates={panel.candidates}
        disabled={disabled}
        busyKey={busyKey}
        onProbe={onProbe}
      />
    )
  }

  return null
}

/**
 * 行尾的 ⋯：低频动作都在这里——重新试运行（排障）、改绑 / 选择源脚本（手工关联）。
 * 一个都没有时不渲染：空菜单比没有菜单更糟。
 */
function MoreMenu({
  panel,
  allScripts,
  writable,
  disabled,
  onProbe,
  onLink,
}: {
  panel: ReadinessPanel
  allScripts: string[]
  writable: boolean
  disabled: boolean
  onProbe: (script: string) => void
  onLink: (script: string) => void
}) {
  useTranslation('dialogs')
  const options = useMemo(() => sourceOptions(panel, allScripts), [panel, allScripts])
  const canReprobe = panel.status === 'editable' && !!panel.script
  // 手工关联：**只读项目上不给**——给出一个按了才发现存不下的入口，比没有更糟
  // （那时用户会以为是自己操作错了）。冲突那一档的候选已经在行里逐个列出。
  const canPick =
    writable && panel.can_manual_link && panel.status !== 'conflict' && options.length > 0
  if (!canReprobe && !canPick) return null
  return (
    <Menu
      align="end"
      width={200}
      trigger={
        <IconButton
          iconSize="sm"
          label={rd('moreAria', { name: fileName(panel.id) })}
          disabled={disabled}
          className="text-ink-3 hover:text-ink"
        >
          <Ellipsis size={ICON_SIZE.sm} />
        </IconButton>
      }
    >
      {canReprobe && (
        <MenuItem icon={Play} onSelect={() => onProbe(panel.script as string)}>
          {rd('reprobe')}
        </MenuItem>
      )}
      {canPick && (
        <MenuSub label={rd(panel.status === 'editable' ? 'relinkPlaceholder' : 'pickSourcePlaceholder')}>
          {options.map((s) => (
            <MenuItem key={s} onSelect={() => onLink(s)}>
              <span className="font-mono">{s}</span>
            </MenuItem>
          ))}
        </MenuSub>
      )}
    </Menu>
  )
}

/**
 * `needs_probe` 的候选是**项目级**的（`candidate_scope: 'project'`）：静态解不出
 * 这些脚本的产物，说不出「这张图来自其中哪一个」。所以这里是「挑一个跑跑看」，
 * 不是「挑一个来源」——措辞与后端给的 scope 一致，不夸大。
 */
function ProbePicker({
  candidates,
  disabled,
  busyKey,
  onProbe,
}: {
  candidates: string[]
  disabled: boolean
  busyKey: string | null
  onProbe: (script: string) => void
}) {
  useTranslation('dialogs')
  const [picked, setPicked] = useState(candidates[0] ?? '')
  const script = candidates.includes(picked) ? picked : (candidates[0] ?? '')
  if (!script) return null
  return (
    <>
      {candidates.length > 1 && (
        <Select
          className="w-36 font-mono"
          value={script}
          onChange={setPicked}
          ariaLabel={rd('probePickAria')}
          options={candidates.map((c) => ({ value: c, label: c }))}
        />
      )}
      <Button
        variant="secondary"
        size="sm"
        disabled={disabled}
        onClick={() => onProbe(script)}
      >
        <Play size={ICON_SIZE.sm} className={cn(busyKey === script && 'animate-pulse')} />
        {rd(busyKey === script ? 'running' : 'probeAndLink')}
      </Button>
    </>
  )
}

/**
 * 这张图可以选哪些源脚本，以及按什么顺序摆。
 *
 * **抽成纯函数是为了它量得到**：选项住在 Radix 的弹层里，从触发器上根本看不见
 * ——用 DOM 去断言"当前脚本没被列出来"是一把量不了这一维的尺子，判据会恒真。
 *
 * 两条规矩：候选排前面（它们是这一轮扫描真的解出来的，最可能是对的），
 * **已经绑定的那一个不列**（选它等于什么都不做，而界面会显得像做了什么）。
 *
 * 第二条只对 `allScripts` 那一半生效——`panel.candidates` 里**结构性地**不会
 * 有已绑定的那个脚本（后端：`editable` 的候选恒为空，`source_missing` 的候选
 * 是 `[s for s in claims[stem] if s != script]`）。在那一半上再滤一次是一条
 * 杀不死的冗余保证：没有任何输入能让它生效，也就没有任何用例能打红它。
 */
export function sourceOptions(
  panel: Pick<ReadinessPanel, 'candidates' | 'script'>,
  allScripts: string[],
): string[] {
  const rest = allScripts
    .filter((s) => !panel.candidates.includes(s) && s !== panel.script)
    .sort()
  return [...panel.candidates, ...rest]
}

/* -------------------------------------------------------------------------- */
/*  高级段：项目里的全部脚本                                                    */
/* -------------------------------------------------------------------------- */

/**
 * 全部脚本（高级入口，默认收起）：项目里的每个 .py，包括静态识别不出产物的
 * ——show-only、动态命名、工具脚本。普通脚本不因静态分析返回 None 就从产品
 * 里消失；任意一条都可以「试运行」，按真实产出登记。
 */
function AllScriptsSection({
  scripts,
  knownStems,
  busy,
  probed,
  writable,
  onProbe,
  onWriteStems,
  source,
}: {
  scripts: ScriptInventoryEntry[]
  /** 项目里已经有图文件的那些图名，供手工映射直接挑 */
  knownStems: string[]
  busy: string | null
  probed: Record<string, ProbeNote>
  writable: boolean
  onProbe: (script: string) => void
  onWriteStems: (script: string, stems: string[]) => void
  source: string
}) {
  useTranslation('dialogs')
  return (
    <Details className="border-t border-border pt-2">
      <Summary className="type-section h-7 gap-1 rounded-sm px-1 hover:text-ink-2">
        {rd('allScriptsTitle', { n: scripts.length })}
      </Summary>
      <p className="type-caption px-1 pb-1">{rd('allScriptsHint')}</p>
      <ul className="max-h-52 overflow-y-auto">
        {scripts.map((s) => (
          <li key={s.script} className="flex flex-col gap-0.5 border-t border-border px-1 py-1.5">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={s.script}>
                {s.script}
              </span>
              <span className="type-meta shrink-0">
                {translate(`registry.reason_${s.reason}`, { ns: 'dialogs' })}
              </span>
              {s.can_probe && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="-my-1 text-ink-2 hover:text-ink"
                  disabled={busy !== null}
                  onClick={() => onProbe(s.script)}
                >
                  {rd(busy === s.script ? 'running' : s.registered ? 'reprobe' : 'probeAndLink')}
                </Button>
              )}
            </div>
            {s.static_stems.length > 0 && (
              <span className="type-meta truncate" title={listJoin(s.static_stems)}>
                {listJoin(s.static_stems)}
              </span>
            )}
            {writable && (
              <ManualStems
                script={s.script}
                known={knownStems}
                disabled={busy !== null}
                onWrite={(stems) => onWriteStems(s.script, stems)}
              />
            )}
            <ProbeNoteView note={probed[s.script]} />
          </li>
        ))}
      </ul>
      <p className="type-meta border-t border-border px-1 py-1.5">
        {rd('sourcePrefix')}
        <span className="font-mono">{source || rd('none')}</span>
      </p>
    </Details>
  )
}

/**
 * 手工写下这个脚本产出的图名（高级段里的兜底）。
 *
 * 静态解不出、试运行也跑不起来时，这是唯一还能把关系建起来的路。**刻意留在
 * 高级段**：它要求用户知道"图名"指的是文件名去掉扩展名那一段，而普通路径
 * 上不该有人需要知道这件事——那边给的是「给这张图选一个源脚本」。
 */
function ManualStems({
  script,
  known,
  disabled,
  onWrite,
}: {
  script: string
  /** 项目里已经存在的图名，供直接挑（审计 T10：不要只有一个手写逗号列表） */
  known: string[]
  disabled: boolean
  onWrite: (stems: string[]) => void
}) {
  useTranslation('dialogs')
  const [text, setText] = useState('')
  const stems = parseStems(text)
  const options = pickableStems(known, stems)
  return (
    <div className="flex flex-col gap-1">
      <label className="type-meta" htmlFor={`stems-${script}`}>
        {rd('manualLabel')}
      </label>
      <div className="flex items-center gap-1.5">
        <TextInput
          id={`stems-${script}`}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={rd('manualPlaceholder')}
          aria-label={rd('manualAria', { script })}
          className="h-7 min-w-0 flex-1 font-mono"
          spellCheck={false}
        />
        {/* 项目里已有的图名直接挑，挑完落进上面那个框（还能接着改）。
            **不取代手输**：这条兜底路径存在的理由正是那些「产物名要跑起来
            才知道」的脚本，它们的图名此刻还不在任何清单里。 */}
        {options.length > 0 && (
          <Select
            className="min-w-32 shrink-0 font-mono"
            value=""
            onChange={(v) => setText(stems.concat(v).join(', '))}
            placeholder={rd('manualPick')}
            ariaLabel={rd('manualPickAria', { script })}
            options={options.map((k) => ({ value: k, label: k }))}
          />
        )}
        <Button
          size="sm"
          variant="secondary"
          className="shrink-0"
          disabled={disabled || stems.length === 0}
          onClick={() => {
            onWrite(stems)
            setText('')
          }}
        >
          {rd('write')}
        </Button>
      </div>
    </div>
  )
}

/**
 * 还能从下拉里挑的图名：已经填进框里的那些不再出现（选了等于什么都没加）。
 *
 * **判据是「等于某个已填的名字」，不是「是那串文本的子串」。** 用户敲了
 * `Ok_v2` 之后 `Ok` 仍然该是可挑的——按子串判会把它一起藏掉，而那时用户
 * 找不到一个明明还在项目里的图名。
 *
 * 与 `sourceOptions` 同一个理由抽成纯函数：选项住在 Radix 的弹层里，从触发器
 * 上根本看不见，用 DOM 去断言「某一项还在不在」是一把量不了这一维的尺子，
 * 判据会恒真（这一条实测被变异证过：按子串判的版本在 DOM 判据下全绿）。
 */
export function pickableStems(known: readonly string[], chosen: readonly string[]): string[] {
  return known.filter((k) => !chosen.includes(k))
}

/**
 * 「一串图名」→ 图名列表。逗号（中英文）与空白都算分隔。
 *
 * 抽成导出的纯函数是为了它**量得到**：写进注册表的是这个结果，而它是这条
 * 兜底路径上唯一一处会把用户敲的字变成键的地方。
 */
export function parseStems(text: string): string[] {
  const out: string[] = []
  for (const raw of text.split(/[,，\s]+/)) {
    const s = raw.trim()
    // 去重：同一个名字写两遍会往注册表里写两条一模一样的键
    if (s && !out.includes(s)) out.push(s)
  }
  return out
}

/** 试运行结果的一致展示：主文案一行，traceback 收在「诊断详情」里 */
function ProbeNoteView({ note }: { note?: ProbeNote }) {
  useTranslation('dialogs')
  const setStatus = useUiStore((s) => s.setStatus)
  if (!note) return null
  return (
    <div className="type-caption mt-1">
      <p className="whitespace-pre-wrap">{note.text}</p>
      {/* 捕获成功的每张图可以直接作为 runtime 面板放上画布。没有磁盘产物的
          show-only 图从这里第一次真正进入产品。 */}
      {note.descriptors && note.descriptors.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1.5">
          {note.descriptors.map((d) => (
            <Button
              key={d.asset_id}
              variant="secondary"
              size="sm"
              onClick={() => {
                addRuntimePanel(d)
                setStatus(msg('registry.addedToCanvas', { stem: d.stem }, 'dialogs'))
                useProjectReadinessStore.getState().closeCenter()
              }}
            >
              {translate('registry.addToCanvas', { ns: 'dialogs', stem: d.stem })}
            </Button>
          ))}
        </div>
      )}
      {note.traceback && (
        <Details className="mt-0.5">
          <Summary className="type-meta h-5 w-fit">
            {translate('registry.probeTraceback', { ns: 'dialogs' })}
          </Summary>
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-mono text-xs leading-snug text-ink-3">
            {note.traceback}
          </pre>
        </Details>
      )}
    </div>
  )
}
