import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Braces, Play, Plus, RefreshCw } from 'lucide-react'
import {
  backendCodeMsg,
  backendErrorText,
  fetchRegistry,
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
import { PENDING_STATUSES, pendingCount, reasonText, statusLabel } from '@/lib/readinessText'
import { cn } from '@/lib/utils'
import { formatMessage, msg, t as translate } from '@/i18n'
import { listJoin } from '@/i18n/format'
import { addPanel, addRuntimePanel } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { refreshAssetsAndSync } from '@/store/liveSync'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from './ui/Button'
import { Dialog } from './ui/Dialog'
import { EmptyState } from './ui/EmptyState'
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
      width={640}
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
      await writeRegistryEntry({ script, entry: entryOf(view, script), stems: [panel.stem] })
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
          icon={AlertTriangle}
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
      <div className="flex items-start justify-between gap-3">
        <SummaryStrip report={report} />
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          disabled={busy !== null || !report.project.can_rescan}
          onClick={() => void scan()}
        >
          <RefreshCw size={13} className={cn(busy === 'scan' && 'animate-spin')} />
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
                <h3 className="mb-1 text-xs font-medium text-ink-2">
                  {rd(`group.${key}`, { n: panels.length })}
                </h3>
                <ul className="flex flex-col gap-1.5">
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
          <dt className="text-xs text-ink-3">{rd(`summary.${c.key}`)}</dt>
          <dd className="font-mono text-xs text-ink">{c.value}</dd>
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
    <ul className="flex flex-col gap-1 rounded-md border border-border bg-surface-2 p-2">
      {notes.map((n) => (
        <li key={n} className="flex items-start gap-1.5 text-xs leading-relaxed text-ink-2">
          <AlertTriangle size={12} className="mt-0.5 shrink-0 text-ink-3" />
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

/** 状态角标：颜色**不是唯一表达**，文字本身就是状态名 */
const BADGE_TONE: Record<ReadinessStatus, string> = {
  editable: 'bg-accent-subtle text-accent',
  auto_linkable: 'bg-surface-2 text-ink-2',
  needs_probe: 'bg-surface-2 text-ink-2',
  conflict: 'bg-danger-subtle text-danger',
  source_missing: 'bg-danger-subtle text-danger',
  layout_only: 'bg-surface-2 text-ink-3',
}

function StatusBadge({ status }: { status: ReadinessStatus }) {
  useTranslation('workspace')
  return (
    <span
      className={cn(
        'shrink-0 rounded-sm px-1 text-xs leading-4',
        BADGE_TONE[status],
      )}
    >
      {statusLabel(status)}
    </span>
  )
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
  // 高亮是**静态描边**不是动画——reduced-motion 下不需要另写一份。
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

  return (
    <li
      ref={ref}
      tabIndex={-1}
      data-panel-row={panel.id}
      className={cn(
        'rounded-md border p-2 outline-none',
        highlight ? 'border-accent bg-accent-subtle' : 'border-border',
        'focus-visible:focus-ring',
      )}
    >
      <div className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate text-xs text-ink" title={panel.id}>
          {fileName(panel.id)}
        </span>
        <StatusBadge status={panel.status} />
      </div>

      <p className="mt-0.5 text-xs leading-relaxed text-ink-2">{reasonText(panel)}</p>

      {panel.status === 'needs_probe' && (
        <p className="mt-0.5 text-xs leading-relaxed text-ink-3">{rd('probeWarning')}</p>
      )}

      <RowActions
        panel={panel}
        allScripts={allScripts}
        writable={writable}
        disabled={disabled}
        busyKey={busy}
        hasAsset={!!asset}
        onAdd={() => {
          if (!asset) return
          addPanel(asset)
          useProjectReadinessStore.getState().closeCenter()
        }}
        onProbe={onProbe}
        onLink={onLink}
        onRescan={onRescan}
      />

      {/* 这一行上刚跑过的那个脚本的结果：已绑定的优先，其次是候选里跑过的
          那一个。一行只显示一条——两条并排的话，用户分不出哪条对应刚才那次点击 */}
      <ProbeNoteView
        note={[panel.script, ...panel.candidates]
          .map((s) => (s ? probed[s] : undefined))
          .find(Boolean)}
      />

      {/* `editable` 的「改为其它源脚本」收在这里：它已经好了，改绑是少数动作，
          摆在第一层会盖过"它已经好了"这句话。其余状态的关联控件在 RowActions 里 */}
      <TechnicalDetails panel={panel}>
        {panel.status === 'editable' && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <SourcePicker
              panel={panel}
              allScripts={allScripts}
              writable={writable}
              disabled={disabled}
              onLink={onLink}
              linkLabel="relink"
            />
          </div>
        )}
      </TechnicalDetails>
    </li>
  )
}

/**
 * 每个状态的下一步动作。
 *
 * 排列的规矩只有一条：**先摆这个状态下最可能对的那一个**。
 * `conflict` 是唯一没有"最可能对"的——候选逐个列出来，机器一个都不选。
 */
function RowActions({
  panel,
  allScripts,
  writable,
  disabled,
  busyKey,
  hasAsset,
  onAdd,
  onProbe,
  onLink,
  onRescan,
}: {
  panel: ReadinessPanel
  allScripts: string[]
  writable: boolean
  disabled: boolean
  busyKey: string | null
  hasAsset: boolean
  onAdd: () => void
  onProbe: (script: string) => void
  onLink: (script: string) => void
  onRescan: () => void
}) {
  useTranslation('dialogs')
  const running = (script: string) => busyKey === script

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
      {panel.status === 'editable' && (
        <>
          {/* 素材清单里没有它时**不渲染这个按钮**：就绪度扫描与素材遍历之间
              新出现 / 刚被删掉的那一档，点下去只会是一条错误 */}
          {hasAsset && (
            <Button variant="outline" size="sm" disabled={disabled} onClick={onAdd}>
              <Plus size={13} />
              {rd('addToCanvas')}
            </Button>
          )}
          {panel.script && (
            <Button
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => onProbe(panel.script as string)}
            >
              <Play size={13} className={cn(running(panel.script) && 'animate-pulse')} />
              {rd(running(panel.script) ? 'running' : 'reprobe')}
            </Button>
          )}
        </>
      )}

      {panel.status === 'auto_linkable' && (
        <Button variant="outline" size="sm" disabled={disabled} onClick={onRescan}>
          <RefreshCw size={13} className={cn(busyKey === 'scan' && 'animate-spin')} />
          {rd('autoLink')}
        </Button>
      )}

      {panel.status === 'needs_probe' && (
        <ProbePicker
          candidates={panel.candidates}
          disabled={disabled}
          busyKey={busyKey}
          onProbe={onProbe}
        />
      )}

      {/* 冲突：候选**逐个列出来**，一个都不替用户选。文件名更像"新版本"的
          那一个也不许赢——那是猜，而猜错的代价是用户此后每次编辑都改错脚本 */}
      {panel.status === 'conflict' &&
        panel.candidates.map((script) => (
          <Button
            key={script}
            variant="outline"
            size="sm"
            disabled={disabled || !panel.can_manual_link}
            onClick={() => onLink(script)}
          >
            {rd('useScript', { script })}
          </Button>
        ))}

      {panel.status === 'source_missing' && (
        <Button variant="outline" size="sm" disabled={disabled} onClick={onRescan}>
          <RefreshCw size={13} className={cn(busyKey === 'scan' && 'animate-spin')} />
          {rd('rescan')}
        </Button>
      )}

      {/* 手工关联：四个还没连上的状态都给，`editable` 收进技术详情（改绑是
          少数动作，摆在第一层会盖过"它已经好了"这句话） */}
      {panel.status !== 'editable' && panel.status !== 'conflict' && (
        <SourcePicker
          panel={panel}
          allScripts={allScripts}
          writable={writable}
          disabled={disabled}
          onLink={onLink}
        />
      )}
    </div>
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
          className="min-w-40 flex-1 font-mono"
          value={script}
          onChange={setPicked}
          ariaLabel={rd('probePickAria')}
          options={candidates.map((c) => ({ value: c, label: c }))}
        />
      )}
      <Button
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => onProbe(script)}
      >
        <Play size={13} className={cn(busyKey === script && 'animate-pulse')} />
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

/**
 * 手工选择源脚本。**只读项目上不渲染**——给出一个按了才发现存不下的按钮，
 * 比没有这个按钮更糟（那时用户会以为是自己操作错了）。
 */
function SourcePicker({
  panel,
  allScripts,
  writable,
  disabled,
  onLink,
  linkLabel = 'link',
}: {
  panel: ReadinessPanel
  allScripts: string[]
  writable: boolean
  disabled: boolean
  onLink: (script: string) => void
  /** 按钮文案：还没连上是「连接」，已经连上（改绑）是「改为这个脚本」 */
  linkLabel?: 'link' | 'relink'
}) {
  useTranslation('dialogs')
  const options = useMemo(
    () => sourceOptions(panel, allScripts),
    [panel, allScripts],
  )
  const [picked, setPicked] = useState('')
  const script = options.includes(picked) ? picked : ''

  if (!writable || !panel.can_manual_link || options.length === 0) return null
  return (
    <>
      <Select
        className="min-w-40 flex-1 font-mono"
        value={script}
        onChange={setPicked}
        placeholder={rd(linkLabel === 'relink' ? 'relinkPlaceholder' : 'pickSourcePlaceholder')}
        ariaLabel={rd('pickSourceAria', { name: fileName(panel.id) })}
        options={options.map((s) => ({ value: s, label: s }))}
      />
      <Button size="sm" disabled={disabled || !script} onClick={() => onLink(script)}>
        {rd(linkLabel)}
      </Button>
    </>
  )
}

/**
 * 技术详情：源脚本、入口函数、运行成本、候选脚本、reason code。
 *
 * **默认收起。** 这一段是给排障与高级用户的——普通用户不需要理解 stem、
 * entry 或者 reason code 才能把图连上。
 */
function TechnicalDetails({
  panel,
  children,
}: {
  panel: ReadinessPanel
  /** 收进这一段的动作（`editable` 的改绑就住在这里） */
  children?: React.ReactNode
}) {
  useTranslation('dialogs')
  const rows: { key: string; value: string }[] = []
  if (panel.script) rows.push({ key: 'script', value: panel.script })
  if (panel.details.entry) rows.push({ key: 'entry', value: panel.details.entry })
  if (panel.details.cost) rows.push({ key: 'cost', value: panel.details.cost })
  if (panel.candidates.length) {
    rows.push({ key: 'candidates', value: listJoin(panel.candidates) })
    if (panel.details.candidate_scope)
      rows.push({ key: 'scope', value: rd(`scope.${panel.details.candidate_scope}`) })
  }
  rows.push({ key: 'stem', value: panel.stem })
  rows.push({ key: 'reasonCode', value: panel.reason_code })

  return (
    <details className="mt-1">
      <summary className="cursor-pointer select-none text-xs text-ink-3 outline-none focus-visible:focus-ring">
        {rd('technicalDetails')}
      </summary>
      <dl className="mt-1 flex flex-col gap-0.5">
        {rows.map((r) => (
          <div key={r.key} className="flex items-baseline gap-2">
            <dt className="shrink-0 text-xs text-ink-3">{rd(`detail.${r.key}`)}</dt>
            <dd className="min-w-0 flex-1 truncate font-mono text-xs text-ink-2" title={r.value}>
              {r.value}
            </dd>
          </div>
        ))}
      </dl>
      {children}
    </details>
  )
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
  busy,
  probed,
  writable,
  onProbe,
  onWriteStems,
  source,
}: {
  scripts: ScriptInventoryEntry[]
  busy: string | null
  probed: Record<string, ProbeNote>
  writable: boolean
  onProbe: (script: string) => void
  onWriteStems: (script: string, stems: string[]) => void
  source: string
}) {
  useTranslation('dialogs')
  return (
    <details className="rounded-md border border-border">
      <summary className="cursor-pointer select-none px-2 py-1 text-xs font-medium text-ink-2 outline-none focus-visible:focus-ring">
        {rd('allScriptsTitle', { n: scripts.length })}
      </summary>
      <p className="px-2 pb-1 text-xs leading-relaxed text-ink-3">{rd('allScriptsHint')}</p>
      <ul className="max-h-52 overflow-y-auto">
        {scripts.map((s) => (
          <li key={s.script} className="flex flex-col gap-0.5 border-t border-border px-2 py-1">
            <div className="flex items-baseline gap-2">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={s.script}>
                {s.script}
              </span>
              <span className="shrink-0 rounded-sm bg-surface-2 px-1 text-xs leading-4 text-ink-3">
                {translate(`registry.reason_${s.reason}`, { ns: 'dialogs' })}
              </span>
              {s.can_probe && (
                <button
                  disabled={busy !== null}
                  onClick={() => onProbe(s.script)}
                  className={cn(
                    'shrink-0 text-xs text-ink-3 outline-none',
                    'hover:text-ink focus-visible:focus-ring disabled:opacity-40',
                  )}
                >
                  {rd(busy === s.script ? 'running' : s.registered ? 'reprobe' : 'probeAndLink')}
                </button>
              )}
            </div>
            {s.static_stems.length > 0 && (
              <span className="truncate text-xs text-ink-3" title={listJoin(s.static_stems)}>
                {listJoin(s.static_stems)}
              </span>
            )}
            {writable && (
              <ManualStems
                script={s.script}
                disabled={busy !== null}
                onWrite={(stems) => onWriteStems(s.script, stems)}
              />
            )}
            <ProbeNoteView note={probed[s.script]} />
          </li>
        ))}
      </ul>
      <p className="border-t border-border px-2 py-1 text-xs leading-relaxed text-ink-3">
        {rd('sourcePrefix')}
        <span className="font-mono">{source || rd('none')}</span>
      </p>
    </details>
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
  disabled,
  onWrite,
}: {
  script: string
  disabled: boolean
  onWrite: (stems: string[]) => void
}) {
  useTranslation('dialogs')
  const [text, setText] = useState('')
  const stems = text
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  return (
    <div className="flex items-center gap-1.5">
      <TextInput
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={rd('manualPlaceholder')}
        aria-label={rd('manualAria', { script })}
        className="h-7 min-w-0 flex-1 font-mono"
        spellCheck={false}
      />
      <Button
        size="sm"
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
  )
}

/** 试运行结果的一致展示：主文案一行，traceback 收在「诊断详情」里 */
function ProbeNoteView({ note }: { note?: ProbeNote }) {
  useTranslation('dialogs')
  const setStatus = useUiStore((s) => s.setStatus)
  if (!note) return null
  return (
    <div className="mt-1 text-xs text-ink-3">
      <p className="whitespace-pre-wrap">{note.text}</p>
      {/* 捕获成功的每张图可以直接作为 runtime 面板放上画布。没有磁盘产物的
          show-only 图从这里第一次真正进入产品。 */}
      {note.descriptors && note.descriptors.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1.5">
          {note.descriptors.map((d) => (
            <Button
              key={d.asset_id}
              variant="outline"
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
        <details className="mt-0.5">
          <summary className="cursor-pointer select-none">
            {translate('registry.probeTraceback', { ns: 'dialogs' })}
          </summary>
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-mono text-xs leading-snug">
            {note.traceback}
          </pre>
        </details>
      )}
    </div>
  )
}
