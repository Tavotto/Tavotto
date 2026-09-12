import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import {
  Braces,
  ImageOff,
  ListFilter,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  SearchX,
  TriangleAlert,
  X,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  backendErrorMsg,
  renderUrl,
  runtimePreviewUrl,
  type PanelInfo,
  type RuntimeAssetInfo,
} from '@/lib/api'
import { runtimeSiblingOf } from '@/lib/assetSibling'
import { formatCm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { addFigureToLayout, openFastEdit } from '@/store/workspace'
import { reasonText, statusLabel } from '@/lib/readinessText'
import {
  DEFAULT_ASSET_FILTERS,
  useAssetBrowseStore,
  type AssetFilters,
  type AssetSortKey,
  type AssetTypeFilter,
} from '@/store/assetBrowseStore'
import { folderLabel, useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { refreshProjectNow } from '@/store/liveSync'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { isBusyPhase, useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Dialog } from '../ui/Dialog'
import { Popover } from '../ui/Popover'
import { Row } from '../ui/Field'
import { SearchInput } from '../ui/SearchInput'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { ScriptLibrary } from './ScriptLibrary'

/** 面板文件名（带扩展名）：同 stem 的 PDF / PNG 靠它区分 */
const fileName = (id: string) => id.split('/').pop() ?? id
const formatOf = (p: PanelInfo) => (p.kind === 'pdf' ? 'PDF' : 'PNG')

type TypeFilter = AssetTypeFilter
type SortKey = AssetSortKey

/** 本组文案在 workspace:assets.* 下 */
const ab = (key: string, values?: Record<string, unknown>) =>
  translate(`assets.${key}`, { ns: 'workspace', ...(values ?? {}) })

const TYPE_VALUES: TypeFilter[] = ['all', 'pdf', 'raster', 'script', 'runtime']
const SORT_VALUES: SortKey[] = ['name', 'recent', 'used']

/** PDF 是格式名，不翻译；其余按 key 取当前语言 */
const typeLabel = (v: TypeFilter) =>
  v === 'pdf'
    ? 'PDF'
    : ab(
        v === 'all'
          ? 'typeAll'
          : v === 'raster'
            ? 'typeRaster'
            : v === 'runtime'
              ? 'typeRuntime'
              : 'typeScript',
      )
const sortLabel = (v: SortKey) =>
  ab(v === 'name' ? 'sortName' : v === 'recent' ? 'sortRecent' : 'sortUsed')

const typeOptions = () => TYPE_VALUES.map((value) => ({ value, label: typeLabel(value) }))
const sortOptions = () => SORT_VALUES.map((value) => ({ value, label: sortLabel(value) }))

type Filters = AssetFilters

const DEFAULT_FILTERS = DEFAULT_ASSET_FILTERS

/**
 * 「图」区的一张卡：磁盘文件（FileAsset）或运行时图（RuntimeFigureAsset）。
 * runtime 条目可带 `sibling`——同一脚本、同一 stem 的磁盘图（见 `runtimeSiblingOf`）。
 */
type LibraryItem =
  | { kind: 'file'; panel: PanelInfo }
  | { kind: 'runtime'; asset: RuntimeAssetInfo; sibling?: PanelInfo }

const itemId = (it: LibraryItem) => (it.kind === 'file' ? it.panel.id : it.asset.id)


export function AssetBrowser() {
  const panels = useAssetStore((s) => s.panels)
  const loading = useAssetStore((s) => s.loading)
  const loaded = useAssetStore((s) => s.loaded)
  const error = useAssetStore((s) => s.error)
  const figuresDir = useAssetStore((s) => s.figuresDir)
  const recentlyUsed = useAssetStore((s) => s.recentlyUsed)
  const runtimeAssets = useRuntimeAssetStore((s) => s.assets)
  const objects = useDocumentStore((s) => s.doc.objects)

  // 搜索词与筛选住在组件外（`store/assetBrowseStore`）：切左轨页签 / 收起抽屉
  // 会把这个组件整个卸掉，放在组件 state 里的输入就跟着没了
  const query = useAssetBrowseStore((s) => s.query)
  const setQuery = useAssetBrowseStore((s) => s.setQuery)
  // 「刷新项目」按钮自己的忙碌态：它等的是 POST /api/project/refresh 走完
  // （静态扫描 + 合并注册表），而 assetStore 的 `loading` 只覆盖后半段的
  //  /api/panels。只看后者的话，按钮在最慢的那一步上是**不转**的。
  const [refreshing, setRefreshing] = useState(false)
  const filters = useAssetBrowseStore((s) => s.filters)
  const setFilters = useAssetBrowseStore((s) => s.setFilters)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [zoomed, setZoomed] = useState<LibraryItem | null>(null)
  /** 后端刷新与素材重取合起来才是用户眼里的「正在刷新」 */
  const busy = refreshing || loading

  const gridRef = useRef<HTMLDivElement>(null)
  const [columns, setColumns] = useState(1)

  // runtime 素材清单（只读端点）：面板挂载时取一次，之后靠 SSE 与 probe
  // 成功的副作用刷新
  useEffect(() => {
    if (useRuntimeAssetStore.getState().assets === null) {
      void useRuntimeAssetStore.getState().loadAssets()
    }
  }, [])

  /** 当前文档里每个素材各用了几次 */
  const usage = useMemo(() => {
    const map = new Map<string, number>()
    for (const o of objects) {
      if (o.type === 'panel') map.set(o.fileId, (map.get(o.fileId) ?? 0) + 1)
    }
    return map
  }, [objects])

  const folders = useMemo(
    () => [...new Set(panels.map((p) => p.folder))].sort(),
    [panels],
  )

  const { source, type, sort, usedOnly } = filters
  const items = useMemo<LibraryItem[]>(() => {
    const q = query.trim().toLowerCase()
    let list = panels.filter((p) => {
      if (q && !p.id.toLowerCase().includes(q) && !p.name.toLowerCase().includes(q)) return false
      if (usedOnly && !usage.has(p.id)) return false
      if (source !== 'all' && p.folder !== source) return false
      if (type === 'pdf' && p.kind !== 'pdf') return false
      if (type === 'raster' && p.kind !== 'raster') return false
      if (type === 'script' && !p.script) return false
      if (type === 'runtime') return false
      return true
    })
    list = [...list]
    // 默认（按名称）排序把可参数化面板排在前面：它们才能进图内编辑，是这个
    // 面板里的一等公民。显式选了最近/次数排序时尊重用户的选择，不再分组。
    if (sort === 'name')
      list.sort(
        (a, b) =>
          Number(!!b.script) - Number(!!a.script) ||
          fileName(a.id).localeCompare(fileName(b.id)),
      )
    else if (sort === 'recent')
      list.sort((a, b) => (recentlyUsed[b.id] ?? 0) - (recentlyUsed[a.id] ?? 0))
    else list.sort((a, b) => (usage.get(b.id) ?? 0) - (usage.get(a.id) ?? 0))

    // 运行时图排在文件之后（它们没有 folder，被来源筛选与格式筛选排除）
    let rt = (runtimeAssets ?? []).filter((a) => {
      if (q && !a.stem.toLowerCase().includes(q) && !a.script.toLowerCase().includes(q))
        return false
      if (usedOnly && !usage.has(a.id)) return false
      if (source !== 'all') return false
      if (type !== 'all' && type !== 'runtime') return false
      return true
    })
    rt = [...rt]
    if (sort === 'recent')
      rt.sort((a, b) => (recentlyUsed[b.id] ?? 0) - (recentlyUsed[a.id] ?? 0))
    else if (sort === 'used')
      rt.sort((a, b) => (usage.get(b.id) ?? 0) - (usage.get(a.id) ?? 0))
    else rt.sort((a, b) => a.stem.localeCompare(b.stem))

    // 同源的 runtime 条目紧跟在它的磁盘图后面（关系一眼可见）；其余运行时图
    // 仍排在文件之后。同源判据只看**当前显示的**文件：磁盘图被筛掉时 runtime
    // 条目回到末尾，不会指着一张看不见的卡
    const out: LibraryItem[] = []
    const placed = new Set<string>()
    for (const panel of list) {
      out.push({ kind: 'file', panel })
      for (const asset of rt) {
        if (placed.has(asset.id) || runtimeSiblingOf(asset, [panel]) !== panel) continue
        out.push({ kind: 'runtime', asset, sibling: panel })
        placed.add(asset.id)
      }
    }
    for (const asset of rt) {
      if (!placed.has(asset.id)) out.push({ kind: 'runtime', asset })
    }
    return out
  }, [panels, runtimeAssets, query, source, type, sort, usedOnly, usage, recentlyUsed])

  // 列数按实测宽度算：抽屉最窄 280px 时也是双列（每张卡 ~125px，预览 3:2 约 83px 高）——
  // 这是素材库而不是看图器，同屏能扫到的张数比单张预览的尺寸重要；看细节有空格键放大。
  // 只有被挤到 <250px（覆盖式抽屉的极端情况）才退回单列
  useLayoutEffect(() => {
    const el = gridRef.current
    if (!el) return
    const measure = () => setColumns(el.clientWidth < 250 ? 1 : 2)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    if (activeId && !items.some((it) => itemId(it) === activeId)) setActiveId(null)
  }, [items, activeId])

  const focusCard = (id: string) => {
    setActiveId(id)
    gridRef.current
      ?.querySelector<HTMLElement>(`[data-card="${CSS.escape(id)}"]`)
      ?.focus({ preventScroll: false })
  }

  const move = (from: string, delta: number) => {
    const i = items.findIndex((it) => itemId(it) === from)
    const next = items[i + delta]
    if (next) focusCard(itemId(next))
  }

  const focusId = items.some((it) => itemId(it) === activeId)
    ? activeId
    : items[0]
      ? itemId(items[0])
      : undefined

  /** 非默认筛选条件 → 可移除的标签 */
  const chips: { key: keyof Filters; label: string }[] = []
  if (source !== 'all') chips.push({ key: 'source', label: folderLabel(source) })
  if (type !== 'all') chips.push({ key: 'type', label: typeLabel(type) })
  if (usedOnly) chips.push({ key: 'usedOnly', label: ab('usedChip') })
  if (sort !== 'name') chips.push({ key: 'sort', label: sortLabel(sort) })

  const clearChip = (key: keyof Filters) =>
    setFilters((f) => ({ ...f, [key]: DEFAULT_FILTERS[key] }))

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 工具栏：搜索框 + 三颗同权重的图标钮（只看可参数化 / 筛选 / 刷新）。
          它们都是 IconButton：28px、透明底、hover 才浮出，名字与气泡同一份 */}
      <div className="flex flex-col gap-1.5 px-3 pb-2">
        <div className="flex items-center gap-0.5">
          <SearchInput
            value={query}
            onValueChange={setQuery}
            placeholder={ab('search')}
            aria-label={ab('searchAria')}
            className="mr-1"
          />
          {/* 一键只看可参数化：等价于筛选弹层里的类型=可参数化，走同一份状态，
              生效时下方出现同一个可移除的筛选标签 */}
          <IconButton
            iconSize="sm"
            label={ab('scriptOnly')}
            active={type === 'script'}
            aria-pressed={type === 'script'}
            onClick={() =>
              setFilters((f) => ({ ...f, type: f.type === 'script' ? 'all' : 'script' }))
            }
          >
            <Braces size={ICON_SIZE.sm} className={type === 'script' ? undefined : 'text-ink-2'} />
          </IconButton>
          <FilterButton
            filters={filters}
            folders={folders}
            activeCount={chips.length}
            onChange={setFilters}
          />
          <IconButton
            iconSize="sm"
            label={ab('refresh')}
            tip={ab('refreshTip')}
            disabled={refreshing}
            onClick={() => {
              // 走**统一刷新**（后端一次完整的一轮），不是自己再扫一遍：
              // 「哪些文件是素材」「脚本怎么合进注册表」只有一份判据，
              // 事件与手动刷新共用它。
              setRefreshing(true)
              void refreshProjectNow()
                .catch((e: unknown) =>
                  useUiStore.getState().setStatus(backendErrorMsg(e), 'error'),
                )
                .finally(() => setRefreshing(false))
              // runtime 图清单是另一个资源（不在 /api/panels 里），顺带取一次
              void useRuntimeAssetStore.getState().loadAssets()
            }}
          >
            {/* 自旋的是这个图标本身：Button 自带的 loading 会再插一个
                LoaderCircle，28px 的图标按钮里挤两个图标就是布局跳变 */}
            <RefreshCw size={ICON_SIZE.sm} className={busy ? 'animate-spin text-ink-3' : 'text-ink-2'} />
          </IconButton>
        </div>

        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1" aria-label={ab('activeFilters')}>
            {chips.map((c) => (
              <button
                key={c.key}
                onClick={() => clearChip(c.key)}
                aria-label={ab('removeFilter', { label: c.label })}
                className={cn(
                  'flex h-6 items-center gap-1 rounded-sm bg-selected px-1.5 text-xs text-ink',
                  'outline-none transition-colors duration-fast hover:bg-surface-active focus-visible:focus-ring',
                )}
              >
                {c.label}
                <X size={ICON_SIZE.xs} className="text-ink-3" />
              </button>
            ))}
          </div>
        )}
      </div>

      {busy && loaded && (
        <p className="px-3 py-1 text-xs text-ink-3" aria-live="polite">
          {ab('refreshing')}
        </p>
      )}
      {error && loaded && (
        <p className="bg-danger-subtle px-3 py-1.5 text-xs text-danger" role="status">
          {ab('refreshFailed', { error })}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* ---- 图：FileAsset + RuntimeFigureAsset ---- */}
        <SectionHeading label={ab('sectionFigures')} count={items.length} />
        <div ref={gridRef} className="px-3 pb-2">
          {error && !loaded && (
            <EmptyState
              icon={TriangleAlert}
              title={ab('loadFailed')}
              hint={error}
              action={{
                label: ab('retry'),
                // 首次加载失败后的重试：**强制**另起一次，不复用可能同样
                // 失败的那个在途请求——用户点重试的原因正是"刚才没成"
                onClick: () => void useAssetStore.getState().load({ force: true }),
              }}
            />
          )}

          {!loaded && !error && <GridSkeleton columns={columns} />}

          {loaded && !error && items.length === 0 && (
            query || chips.length ? (
              <EmptyState icon={SearchX} title={ab('noMatch')} />
            ) : (
              <EmptyState
                icon={ImageOff}
                title={ab('emptyTitle')}
                hint={ab('emptyHint')}
              />
            )
          )}

          {items.length > 0 && (
            <ul
              role="listbox"
              aria-label={ab('listLabel')}
              className="grid gap-2"
              style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
            >
              {items.map((it) =>
                it.kind === 'file' ? (
                  <AssetCard
                    key={it.panel.id}
                    panel={it.panel}
                    used={usage.get(it.panel.id) ?? 0}
                    selected={activeId === it.panel.id}
                    tabbable={focusId === it.panel.id}
                    onSelect={() => setActiveId(it.panel.id)}
                    onOpen={() => openFastEdit(it.panel.id)}
                    onAdd={() => addFigureToLayout(it.panel.id)}
                    onZoom={() => setZoomed(it)}
                    onMove={(d) => move(it.panel.id, d)}
                    columns={columns}
                  />
                ) : (
                  <RuntimeAssetCard
                    key={it.asset.id}
                    asset={it.asset}
                    sibling={it.sibling}
                    used={usage.get(it.asset.id) ?? 0}
                    selected={activeId === it.asset.id}
                    tabbable={focusId === it.asset.id}
                    onSelect={() => setActiveId(it.asset.id)}
                    onAdd={() => addFigureToLayout(it.asset.id)}
                    onZoom={() => setZoomed(it)}
                    onMove={(d) => move(it.asset.id, d)}
                    columns={columns}
                  />
                ),
              )}
            </ul>
          )}
        </div>

        {/* ---- 脚本：普通入口的「运行并发现图」住在这里 ---- */}
        <SectionHeading label={ab('sectionScripts')} />
        <ScriptLibrary query={query} />
      </div>

      {/* 选中卡片的两个动作（真按钮）与接入说明。**都在 listbox 之外**：option
          里不许再嵌可 Tab 的控件（axe nested-interactive，serious），而键盘 /
          读屏用户必须到得了「编辑原图」与「添加到画布」这两个不同的动作 */}
      <SelectedAssetActions item={items.find((it) => itemId(it) === activeId)} />
      <AssetCapabilityNotice panel={panels.find((p) => p.id === activeId)} />

      {figuresDir && <FolderInfo dir={figuresDir} shown={items.length} total={panels.length + (runtimeAssets?.length ?? 0)} />}

      <Dialog
        open={!!zoomed}
        onOpenChange={(v) => !v && setZoomed(null)}
        title={zoomed ? (zoomed.kind === 'file' ? fileName(zoomed.panel.id) : zoomed.kind === 'runtime' ? zoomed.asset.stem : '') : ''}
        description={
          zoomed?.kind === 'file'
            ? `${formatOf(zoomed.panel)} · ${formatCm(zoomed.panel.native_w_mm)}×${formatCm(zoomed.panel.native_h_mm)}cm`
            : zoomed?.kind === 'runtime'
              ? `${ab('runtimeBadge')} · ${zoomed.asset.script}`
              : ''
        }
        size="lg"
        footer={
          <Button
            variant="primary"
            size="md"
            disabled={zoomed?.kind === 'runtime' && !zoomed.asset.descriptor}
            onClick={() => {
              // 文件与 runtime 同一条路：已经在文档里就只是聚焦它，绝不叠第二份
              if (zoomed && (zoomed.kind === 'file' || zoomed.asset.descriptor))
                addFigureToLayout(itemId(zoomed))
              setZoomed(null)
            }}
          >
            <Plus size={ICON_SIZE.md} />
            {ab('addToCanvas')}
          </Button>
        }
      >
        {zoomed?.kind === 'file' && (
          <div className="flex items-center justify-center rounded-sm border border-border bg-white p-2">
            <img
              src={renderUrl(zoomed.panel.id, 800, zoomed.panel.mtime)}
              alt={ab('zoomAlt', { name: fileName(zoomed.panel.id) })}
              className="max-h-[56vh] max-w-full object-contain"
            />
          </div>
        )}
        {zoomed?.kind === 'runtime' && <RuntimeZoom asset={zoomed.asset} />}
      </Dialog>
    </div>
  )
}

/** 区标题：图 / 脚本 两个区的分隔（计数可选）。分区小标题那一档，靠左与内容对齐 */
function SectionHeading({ label, count }: { label: string; count?: number }) {
  return (
    <h3 className="type-section flex items-center gap-1.5 px-3 pb-1 pt-2">
      {label}
      {count !== undefined && <span className="tabular-nums">{count}</span>}
    </h3>
  )
}

/** 来源 / 类型 / 排序 / 已使用收进同一个筛选 popover */
function FilterButton({
  filters,
  folders,
  activeCount,
  onChange,
}: {
  filters: Filters
  folders: string[]
  activeCount: number
  onChange: (f: Filters) => void
}) {
  useTranslation('workspace')
  const patch = (p: Partial<Filters>) => onChange({ ...filters, ...p })
  return (
    <Popover
      width={224}
      align="end"
      trigger={
        <IconButton
          iconSize="sm"
          active={activeCount > 0}
          label={activeCount ? ab('filterActiveAria', { count: activeCount }) : ab('filterAria')}
        >
          <ListFilter size={ICON_SIZE.sm} className={activeCount ? undefined : 'text-ink-2'} />
        </IconButton>
      }
    >
      <div className="flex flex-col gap-1.5">
        <Row label={ab('source')} labelWidth={36}>
          <Select
            className="min-w-0 flex-1"
            value={filters.source}
            onChange={(source) => patch({ source })}
            ariaLabel={ab('sourceAria')}
            options={[
              { value: 'all', label: ab('sourceAll') },
              ...folders.map((f) => ({ value: f, label: folderLabel(f) })),
            ]}
          />
        </Row>
        <Row label={ab('type')} labelWidth={36}>
          <Select
            className="min-w-0 flex-1"
            value={filters.type}
            onChange={(type) => patch({ type })}
            ariaLabel={ab('typeAria')}
            options={typeOptions()}
          />
        </Row>
        <Row label={ab('sort')} labelWidth={36}>
          <Select
            className="min-w-0 flex-1"
            value={filters.sort}
            onChange={(sort) => patch({ sort })}
            ariaLabel={ab('sortAria')}
            options={sortOptions()}
          />
        </Row>
        <Row label={ab('usedOnly')} labelWidth={36}>
          <Toggle
            checked={filters.usedOnly}
            onChange={(usedOnly) => patch({ usedOnly })}
            aria-label={ab('usedOnlyAria')}
          />
        </Row>
        {activeCount > 0 && (
          <Button size="sm" className="self-end text-ink-2" onClick={() => onChange(DEFAULT_FILTERS)}>
            {ab('resetFilters')}
          </Button>
        )}
      </div>
    </Popover>
  )
}

/** 骨架与真实卡片同尺寸（3:2 图片区 + 两行文字），加载完不跳版 */
function GridSkeleton({ columns }: { columns: number }) {
  return (
    <ul
      aria-hidden
      className="grid gap-2"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: 8 }, (_, i) => (
        <li key={i} className="overflow-hidden rounded-sm border border-border bg-surface">
          <div className="aspect-[3/2] animate-pulse bg-surface-2" />
          <div className="flex flex-col gap-1 px-1.5 py-1.5">
            <div className="h-3 animate-pulse rounded-xs bg-selected" />
            <div className="h-3 w-3/5 animate-pulse rounded-xs bg-surface-2" />
          </div>
        </li>
      ))}
    </ul>
  )
}

/**
 * 一张素材卡：图片占绝大部分面积，识别靠图不靠文件名。
 *
 * **两个明确的动作，各说各的后果**（UI 审计 T06）：
 *
 * * **编辑原图**（Enter / 双击 / 就近按钮）——进快速编辑工作区。图还不在
 *   文档里时它会把图加进来（快速编辑的对象只能是文档里的面板对象，ADR 0028），
 *   而这一步由 `openFastEdit` 用一条状态提示说出口、一次撤销可移除；
 * * **添加到画布**（Shift+Enter / 就近按钮 / 拖拽 / 看大图弹窗）——文档变更，
 *   已经在文档里就只是聚焦它。
 *
 * 从前这两件事共用一个叫「打开」的动作：用户以为在看图，版本预览里却多了一个
 * 对象、问题面板多了一批问题。普通用户要做的事仍然是"改这张图"，所以编辑
 * 仍是主动作——但它不能再把加入文档这件事藏在一个中性的词后面。
 */
function AssetCard({
  panel,
  used,
  selected,
  tabbable,
  onSelect,
  onOpen,
  onAdd,
  onZoom,
  onMove,
  columns,
}: {
  panel: PanelInfo
  used: number
  selected: boolean
  tabbable: boolean
  onSelect: () => void
  onOpen: () => void
  onAdd: () => void
  onZoom: () => void
  onMove: (delta: number) => void
  columns: number
}) {
  useTranslation('workspace')
  const name = fileName(panel.id)
  const cap = panel.capability
  const label = [
    name,
    formatOf(panel),
    // 状态进可达名。`capability` 缺席 = **这一轮还不知道**（就绪度扫描与素材
    // 遍历之间新出现的那一档），不是 `layout_only`——那时照旧只说老那句
    // 「可参数化」，绝不替后端补一个默认状态。
    cap ? statusLabel(cap.status) : panel.script ? ab('cardParameterizable') : null,
    ab('cardSize', {
      w: formatCm(panel.native_w_mm),
      h: formatCm(panel.native_h_mm),
    }),
    used ? ab('cardUsed', { count: used }) : null,
  ]
    .filter(Boolean)
    .join('，')

  return (
    <li
      role="option"
      aria-selected={selected}
      aria-label={label}
      aria-keyshortcuts={CARD_KEYSHORTCUTS}
      tabIndex={tabbable ? 0 : -1}
      data-card={panel.id}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('application/x-panel-id', panel.id)
        e.dataTransfer.effectAllowed = 'copy'
      }}
      onClick={onSelect}
      onDoubleClick={onOpen}
      onFocus={onSelect}
      onKeyDown={(e) => {
        const step: Record<string, number> = {
          ArrowRight: 1,
          ArrowLeft: -1,
          ArrowDown: columns,
          ArrowUp: -columns,
        }
        if (e.key === 'Enter') {
          e.preventDefault()
          // Enter = 编辑原图；Shift+Enter = 添加到画布（两个动作，两个键）
          if (e.shiftKey) onAdd()
          else onOpen()
        } else if (e.key === ' ') {
          e.preventDefault()
          onZoom()
        } else if (step[e.key] !== undefined) {
          e.preventDefault()
          onMove(step[e.key])
        }
      }}
      title={ab('cardTitle', { id: panel.id })}
      className={cn(cardClass(selected), 'cursor-grab active:cursor-grabbing')}
      style={{ contentVisibility: 'auto', containIntrinsicSize: '140px' }}
    >
      <CardPreview>
        <img
          loading="lazy"
          src={renderUrl(panel.id, 400, panel.mtime)}
          alt=""
          draggable={false}
          className="h-full w-full object-contain p-1"
        />

        {/* 不是 <button>：option 里不许再嵌交互控件（axe nested-interactive，
            serious）——哪怕 tabIndex=-1 也算。这两个只是鼠标用户的就近入口；
            键盘 / 读屏用户在 option 上按 Enter / Shift+Enter 走同一对动作，
            列表下方 `SelectedAssetActions` 里还有一对真按钮。 */}
        <CardActions selected={selected}>
          <CardAction icon={Pencil} label={ab('openFigure')} title={ab('openAria', { name })} onClick={onOpen} />
          <CardAction icon={Plus} label={ab('addToCanvas')} title={ab('addAria', { name })} onClick={onAdd} />
        </CardActions>
      </CardPreview>

      {/* 文字区：文件名一行、元数据一行。格式 / 尺寸 / 接入状态 / 使用次数都在
          这里，预览上不再压任何标签——图就是图。
          接入状态**只在需要说话时说话**：`editable` 已经有 `{}` 那个紧凑标记，
          再写一遍「可编辑」是纯噪音；完整解释在 `title` 与卡片外的说明条里。 */}
      <CardMeta
        name={name}
        selected={selected}
        marker={
          panel.script ? (
            <span
              className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-3"
              title={ab('scriptBadgeTitle')}
            >
              <Braces size={ICON_SIZE.xs} />
            </span>
          ) : undefined
        }
        parts={[
          formatOf(panel),
          translate('measure.cmSize', {
            w: formatCm(panel.native_w_mm),
            h: formatCm(panel.native_h_mm),
          }),
          cap && cap.status !== 'editable'
            ? { text: statusLabel(cap.status), title: reasonText(cap) }
            : null,
        ]}
        used={used}
      />
    </li>
  )
}

/**
 * 一张运行时图卡（RuntimeFigureAsset，ADR 0013）：没有磁盘原件，预览来自
 * materialized cache。有描述符（跑过）→ Enter/双击编辑原图、Shift+Enter 添加到
 * 画布（与文件卡同一对动作）；还没跑过 → 主动作变成「运行并发现图」（尺寸与
 * 内容只有运行后才知道，绝不给假路径 / 假尺寸——负向反证 #2：这里要求磁盘
 * path 的话加入画布当场断）。
 *
 * `sibling` = 同一脚本、同一 stem 的磁盘图（`runtimeSiblingOf`）：有的话第二行
 * 写「同源：X.pdf」而不是脚本路径——两张卡并排时用户要的是关系，不是来源。
 */
function RuntimeAssetCard({
  asset,
  sibling,
  used,
  selected,
  tabbable,
  onSelect,
  onAdd,
  onZoom,
  onMove,
  columns,
}: {
  asset: RuntimeAssetInfo
  sibling?: PanelInfo
  used: number
  selected: boolean
  tabbable: boolean
  onSelect: () => void
  onAdd: () => void
  onZoom: () => void
  onMove: (delta: number) => void
  columns: number
}) {
  useTranslation('workspace')
  const nonce = useRuntimeAssetStore((s) => s.previewNonce[asset.id])
  const run = useScriptRunStore((s) => s.byScript[asset.script])
  const busy = !!run && isBusyPhase(run.phase)

  const primary = () => {
    // 跑过的运行时图与磁盘素材同一条路：编辑原图 → 快速编辑。还没跑过的那一档
    // 主动作仍然是"运行并发现图"——尺寸与内容只有运行后才知道。
    if (asset.descriptor) openFastEdit(asset.id)
    else if (!busy) void useScriptRunStore.getState().run(asset.script)
  }
  const add = () => {
    // 没有描述符就没有可添加的东西（尺寸未知）——Shift+Enter 落到这里时安静地不做
    if (asset.descriptor) onAdd()
  }
  const rerun = () => {
    if (!busy) void useScriptRunStore.getState().run(asset.script)
  }

  // stale 角标文案复用 panelBadge.runtime*（画布角标同一批 key）；
  // 还没跑过的卡片占位区已经写着「尚未运行」，needs_rerun 不再重复一行
  const staleKey: string | null =
    asset.status === 'fresh' || (!asset.cached && asset.status === 'needs_rerun')
      ? null
      : `runtime${asset.status
          .split('_')
          .map((s) => s[0].toUpperCase() + s.slice(1))
          .join('')}`

  const label = [
    asset.stem,
    ab('runtimeBadge'),
    ab('runtimeFromScript', { script: asset.script }),
    asset.size_mm
      ? ab('cardSize', { w: formatCm(asset.size_mm[0]), h: formatCm(asset.size_mm[1]) })
      : ab('runtimeNeedsRun'),
    sibling ? ab('runtimeSiblingOf', { name: fileName(sibling.id) }) : null,
    staleKey ? translate(`panelBadge.${staleKey}`, { ns: 'workspace' }) : null,
    used ? ab('cardUsed', { count: used }) : null,
  ]
    .filter(Boolean)
    .join('，')

  return (
    <li
      role="option"
      aria-selected={selected}
      aria-label={label}
      aria-keyshortcuts={asset.descriptor ? CARD_KEYSHORTCUTS : 'Enter Space'}
      tabIndex={tabbable ? 0 : -1}
      data-card={asset.id}
      onClick={onSelect}
      onDoubleClick={primary}
      onFocus={onSelect}
      onKeyDown={(e) => {
        const step: Record<string, number> = {
          ArrowRight: 1,
          ArrowLeft: -1,
          ArrowDown: columns,
          ArrowUp: -columns,
        }
        if (e.key === 'Enter') {
          e.preventDefault()
          if (e.shiftKey) add()
          else primary()
        } else if (e.key === ' ') {
          e.preventDefault()
          onZoom()
        } else if (step[e.key] !== undefined) {
          e.preventDefault()
          onMove(step[e.key])
        }
      }}
      title={ab('runtimeCardTitle', { stem: asset.stem, script: asset.script })}
      className={cardClass(selected)}
      style={{ contentVisibility: 'auto', containIntrinsicSize: '140px' }}
    >
      <CardPreview>
        {asset.cached ? (
          <img
            loading="lazy"
            src={runtimePreviewUrl(asset.id, nonce)}
            alt=""
            draggable={false}
            className="h-full w-full object-contain p-1"
          />
        ) : (
          <span className="flex flex-col items-center gap-1 p-2 text-center type-meta">
            <Play size={ICON_SIZE.md} className="text-ink-faint" />
            {ab('runtimeNeedsRun')}
          </span>
        )}

        {/* 就近入口（与文件卡同款，非嵌套控件）：有描述符 = 编辑原图 + 添加到
            画布；没有 = 只有「运行并发现图」 */}
        <CardActions selected={selected}>
          {asset.descriptor ? (
            <>
              <CardAction
                icon={Pencil}
                label={ab('openFigure')}
                title={ab('openAria', { name: asset.stem })}
                onClick={primary}
              />
              <CardAction
                icon={Plus}
                label={ab('addToCanvas')}
                title={ab('addAria', { name: asset.stem })}
                onClick={add}
              />
            </>
          ) : (
            <CardAction
              icon={Play}
              label={translate(busy ? 'scripts.running' : 'scripts.run', { ns: 'workspace' })}
              title={ab('runtimeRunAria', { script: asset.script })}
              onClick={primary}
            />
          )}
        </CardActions>
      </CardPreview>

      {/* 第二行：「运行时图」是它的格式名；之后有同源磁盘图先说关系（脚本路径在
          title 与可达名里仍然有），否则尺寸（跑过）或脚本路径（没跑过） */}
      <CardMeta
        name={asset.stem}
        selected={selected}
        marker={
          <span className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-3">
            <Zap size={ICON_SIZE.xs} />
          </span>
        }
        parts={[
          ab('runtimeBadge'),
          sibling
            ? ab('runtimeSiblingOf', { name: fileName(sibling.id) })
            : asset.size_mm
              ? translate('measure.cmSize', {
                  w: formatCm(asset.size_mm[0]),
                  h: formatCm(asset.size_mm[1]),
                })
              : { text: asset.script, title: asset.script },
        ]}
        used={used}
        extra={
          staleKey ? (
            <p className="flex items-center justify-between gap-1 text-xs">
              <span className="min-w-0 truncate text-danger">
                {translate(`panelBadge.${staleKey}`, { ns: 'workspace' })}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  rerun()
                }}
                disabled={busy}
                className="shrink-0 rounded-xs text-ink-2 outline-none hover:text-ink focus-visible:focus-ring disabled:opacity-40"
              >
                {translate(`scripts.${busy ? 'running' : 'rerun'}`, { ns: 'workspace' })}
              </button>
            </p>
          ) : undefined
        }
      />
    </li>
  )
}

/** 卡片外壳：hairline 常态，hover 加深一档，选中是 selected 轻 tint + 名字加粗 */
const cardClass = (selected: boolean) =>
  cn(
    'group relative overflow-hidden rounded-sm border outline-none transition-colors duration-fast',
    selected ? 'border-border-strong bg-selected' : 'border-border bg-surface hover:border-border-strong',
    'focus-visible:focus-ring',
  )

/** 预览区：3:2、白底、内容按比例缩放；上面只有悬停时的就近入口，没有常驻标签 */
function CardPreview({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex aspect-[3/2] items-center justify-center overflow-hidden bg-white">
      {children}
    </div>
  )
}

/** 元数据行里的一段：纯文字，或带 title（悬停看完整解释）的文字 */
type MetaPart = string | { text: string; title?: string } | null | undefined

/**
 * 卡片文字区：第一行名字（+ 一个 16px 的标记位），第二行元数据用「·」串起来、
 * 使用次数靠右。两行都是单行截断，长文件名 / 长状态不会把卡片撑高。
 */
function CardMeta({
  name,
  selected,
  marker,
  parts,
  used,
  extra,
}: {
  name: string
  selected: boolean
  marker?: ReactNode
  parts: MetaPart[]
  used: number
  extra?: ReactNode
}) {
  const shown = parts.filter((p): p is Exclude<MetaPart, null | undefined> => p != null)
  return (
    <div className="flex flex-col px-1.5 py-1">
      <p className="flex items-center gap-1 text-xs">
        <span className={cn('min-w-0 truncate', selected ? 'font-medium text-ink' : 'text-ink')} title={name}>
          {name}
        </span>
        {marker}
      </p>
      <p className="flex items-center gap-1 type-meta tabular-nums">
        {shown.map((part, i) => (
          <Fragment key={i}>
            {i > 0 && (
              <span aria-hidden className="shrink-0">
                ·
              </span>
            )}
            <span
              className={cn('truncate', i === 0 ? 'shrink-0' : 'min-w-0')}
              title={typeof part === 'string' ? undefined : part.title}
            >
              {typeof part === 'string' ? part : part.text}
            </span>
          </Fragment>
        ))}
        {used > 0 && (
          <span className="ml-auto shrink-0 pl-1" title={ab('cardUsed', { count: used })}>
            ×{used}
          </span>
        )}
      </p>
      {extra}
    </div>
  )
}

/** 卡片可达名旁的键位说明（aria-keyshortcuts 的语法：空格分隔的组合键） */
const CARD_KEYSHORTCUTS = 'Enter Shift+Enter Space'

/** 卡片右下角的就近入口容器：悬停 / 聚焦 / 选中时出现 */
function CardActions({ selected, children }: { selected: boolean; children: ReactNode }) {
  return (
    <span
      data-card-actions
      className={cn(
        'absolute bottom-1.5 right-1.5 flex items-center gap-1',
        'opacity-0 transition-opacity duration-fast select-none',
        'group-hover:opacity-100 group-focus-visible:opacity-100',
        selected && 'opacity-100',
      )}
    >
      {children}
    </span>
  )
}

/**
 * 一个就近入口：24px 的图标小片，名字在 title（气泡）与读屏文本里——双列时预览
 * 只有 ~80px 高，两个带文字的片会把图整个盖住。
 * 不是 <button>——option 里不许再嵌交互控件（axe nested-interactive）。
 */
function CardAction({
  icon: Icon,
  label,
  title,
  onClick,
}: {
  icon: LucideIcon
  label: string
  title: string
  onClick: () => void
}) {
  return (
    <span
      title={title}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      className={cn(
        'flex h-6 w-6 cursor-pointer items-center justify-center rounded-sm',
        'border border-border bg-surface text-ink shadow-pop',
        'transition-colors duration-fast hover:border-border-strong hover:bg-surface-2',
      )}
    >
      <Icon size={ICON_SIZE.sm} />
      <span className="sr-only">{label}</span>
    </span>
  )
}

/**
 * 选中卡片的两个动作，**真按钮**，住在 listbox 外面（同 `AssetCapabilityNotice`
 * 的理由：option 里不许再嵌可 Tab 的控件）。鼠标用户有卡片上的就近入口，键盘
 * 用户有 Enter / Shift+Enter；这一条是读屏与"只想点按钮"的人的入口，三条路
 * 落到同一对 action 上。没跑过的 runtime 图只有「运行并发现图」——没有描述符
 * 就没有能编辑、能添加的东西。
 */
function SelectedAssetActions({ item }: { item: LibraryItem | undefined }) {
  useTranslation('workspace')
  const run = useScriptRunStore((s) =>
    item?.kind === 'runtime' ? s.byScript[item.asset.script] : undefined,
  )
  if (!item) return null
  const name = item.kind === 'file' ? fileName(item.panel.id) : item.asset.stem
  const actionable = item.kind === 'file' || !!item.asset.descriptor
  const busy = !!run && isBusyPhase(run.phase)
  return (
    <div
      role="group"
      aria-label={ab('selectedActionsAria', { name })}
      data-selected-asset-actions
      className="flex shrink-0 items-center gap-1 border-t border-border px-3 py-1.5"
    >
      <span className="min-w-0 flex-1 truncate text-xs text-ink-2" title={name}>
        {name}
      </span>
      {actionable ? (
        <>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              openFastEdit(itemId(item))
            }}
          >
            <Pencil size={ICON_SIZE.xs} />
            {ab('openFigure')}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              addFigureToLayout(itemId(item))
            }}
          >
            <Plus size={ICON_SIZE.xs} />
            {ab('addToCanvas')}
          </Button>
        </>
      ) : (
        <Button
          size="sm"
          variant="secondary"
          disabled={busy}
          onClick={() => void useScriptRunStore.getState().run(item.asset.script)}
        >
          <Play size={ICON_SIZE.xs} />
          {translate(busy ? 'scripts.running' : 'scripts.run', { ns: 'workspace' })}
        </Button>
      )}
    </div>
  )
}

/**
 * 选中卡片的接入说明条。
 *
 * **住在 listbox 外面**，因为它里面有一个真按钮：`role="option"` 的卡片里再
 * 嵌可 Tab 的控件是 axe 的 nested-interactive（serious），而键盘用户又必须
 * 到得了「查看接入状态」。放在列表下方，两个约束同时成立——鼠标用户点卡片
 * 就看见，键盘用户 Tab 出列表就落在它上面。
 *
 * `editable` 不显示：那一档没有需要说的话，常驻一条只会挤掉缩略图。
 * `capability` 缺席同样不显示——「这一轮还不知道」不是一种状态，编一句出来
 * 就是替后端撒谎。
 */
function AssetCapabilityNotice({ panel }: { panel?: PanelInfo }) {
  useTranslation('workspace')
  const cap = panel?.capability
  if (!panel || !cap || cap.status === 'editable') return null
  return (
    <div
      role="status"
      data-capability-notice
      className="shrink-0 border-t border-border bg-surface-2 px-3 py-1.5"
    >
      <p className="truncate text-xs text-ink" title={panel.id}>
        {ab('capabilityHeading', { name: fileName(panel.id), status: statusLabel(cap.status) })}
      </p>
      <p className="mt-0.5 text-xs leading-relaxed text-ink-2">{reasonText(cap)}</p>
      <Button
        size="sm"
        className="-ml-2 mt-0.5"
        onClick={() => useProjectReadinessStore.getState().focusPanel(panel.id, 'panel')}
      >
        {translate('readiness.openCenter', { ns: 'workspace' })}
      </Button>
    </div>
  )
}

/** 运行时图的大图预览：cache 预览 + 「没有原始文件」的如实说明 */
function RuntimeZoom({ asset }: { asset: RuntimeAssetInfo }) {
  useTranslation('workspace')
  const nonce = useRuntimeAssetStore((s) => s.previewNonce[asset.id])
  return (
    <div className="flex flex-col gap-2">
      {asset.cached ? (
        <div className="flex items-center justify-center rounded-sm border border-border bg-white p-2">
          <img
            src={runtimePreviewUrl(asset.id, nonce)}
            alt={ab('zoomAlt', { name: asset.stem })}
            className="max-h-[50vh] max-w-full object-contain"
          />
        </div>
      ) : (
        <p className="rounded-sm border border-border bg-surface-2 p-3 text-center text-xs text-ink-3">
          {ab('runtimeNeedsRun')}
        </p>
      )}
      <p className="text-xs leading-relaxed text-ink-3">{ab('runtimeNoFile')}</p>
    </div>
  )
}

/** 目录路径是排查用信息，收进可折叠的一行 */
function FolderInfo({ dir, shown, total }: { dir: string; shown: number; total: number }) {
  useTranslation('workspace')
  const [open, setOpen] = useState(false)
  return (
    <div className="shrink-0 px-3 py-1.5">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 rounded-sm text-left text-xs text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
      >
        {ab('folderInfo')}
        <span className="ml-auto font-mono">
          {shown === total ? total : `${shown} / ${total}`}
        </span>
      </button>
      {open && (
        <p className="mt-1 break-all font-mono text-xs text-ink-3" title={dir}>
          {dir}
        </p>
      )}
    </div>
  )
}
