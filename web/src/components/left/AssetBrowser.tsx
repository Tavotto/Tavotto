import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Braces, ListFilter, Plus, RotateCw, Search, X,
  SearchX,
  ImageOff,
  Play,
  TriangleAlert,
  Zap,
} from 'lucide-react'
import {
  backendErrorMsg,
  renderUrl,
  runtimePreviewUrl,
  type PanelInfo,
  type RuntimeAssetInfo,
} from '@/lib/api'
import { formatCm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { addPanel, addRuntimePanel } from '@/store/actions'
import { folderLabel, useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { refreshProjectNow } from '@/store/liveSync'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { isBusyPhase, useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Dialog } from '../ui/Dialog'
import { Popover } from '../ui/Popover'
import { Row } from '../ui/Field'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'
import { ScriptLibrary } from './ScriptLibrary'

/** 面板文件名（带扩展名）：同 stem 的 PDF / PNG 靠它区分 */
const fileName = (id: string) => id.split('/').pop() ?? id
const formatOf = (p: PanelInfo) => (p.kind === 'pdf' ? 'PDF' : 'PNG')

type TypeFilter = 'all' | 'pdf' | 'raster' | 'script' | 'runtime'
type SortKey = 'name' | 'recent' | 'used'

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

interface Filters {
  source: string
  type: TypeFilter
  sort: SortKey
  usedOnly: boolean
}

const DEFAULT_FILTERS: Filters = { source: 'all', type: 'all', sort: 'name', usedOnly: false }

/** 「图」区的一张卡：磁盘文件（FileAsset）或运行时图（RuntimeFigureAsset） */
type LibraryItem =
  | { kind: 'file'; panel: PanelInfo }
  | { kind: 'runtime'; asset: RuntimeAssetInfo }

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

  const [query, setQuery] = useState('')
  // 「刷新项目」按钮自己的忙碌态：它等的是 POST /api/project/refresh 走完
  // （静态扫描 + 合并注册表），而 assetStore 的 `loading` 只覆盖后半段的
  //  /api/panels。只看后者的话，按钮在最慢的那一步上是**不转**的。
  const [refreshing, setRefreshing] = useState(false)
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
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

    return [
      ...list.map((panel): LibraryItem => ({ kind: 'file', panel })),
      ...rt.map((asset): LibraryItem => ({ kind: 'runtime', asset })),
    ]
  }, [panels, runtimeAssets, query, source, type, sort, usedOnly, usage, recentlyUsed])

  // 列数按实测宽度算：<344px 单列大预览，更宽才双列
  useLayoutEffect(() => {
    const el = gridRef.current
    if (!el) return
    const measure = () => setColumns(el.clientWidth < 344 ? 1 : 2)
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
      <div className="flex flex-col gap-1.5 px-3 pb-2">
        <div className="flex items-center gap-1">
          <div className="relative flex-1">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.stopPropagation()}
              placeholder={ab('search')}
              aria-label={ab('searchAria')}
              className={cn(
                'h-7 w-full rounded-sm border border-transparent bg-surface-2 pl-6.5 pr-1.5 text-xs',
                'text-ink placeholder:text-ink-faint outline-none transition-colors',
                'hover:border-border focus:border-accent focus:bg-surface',
              )}
            />
          </div>
          {/* 一键只看可参数化：等价于筛选弹层里的类型=可参数化，走同一份状态，
              生效时下方出现同一个可移除的筛选标签 */}
          <Tip label={ab('scriptOnly')}>
            <Button
              size="icon-sm"
              active={type === 'script'}
              onClick={() =>
                setFilters((f) => ({ ...f, type: f.type === 'script' ? 'all' : 'script' }))
              }
              aria-label={ab('scriptOnly')}
              aria-pressed={type === 'script'}
            >
              <Braces size={12} />
            </Button>
          </Tip>
          <FilterButton
            filters={filters}
            folders={folders}
            activeCount={chips.length}
            onChange={setFilters}
          />
          <Tip label={ab('refreshTip')}>
            <Button
              size="icon-sm"
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
              aria-label={ab('refresh')}
            >
              {/* 自旋的是这个图标本身：Button 自带的 loading 会再插一个
                  Loader2，28px 的图标按钮里挤两个图标就是布局跳变 */}
              <RotateCw size={12} className={busy ? 'animate-spin text-ink-3' : 'text-ink-2'} />
            </Button>
          </Tip>
        </div>

        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1" aria-label={ab('activeFilters')}>
            {chips.map((c) => (
              <button
                key={c.key}
                onClick={() => clearChip(c.key)}
                aria-label={ab('removeFilter', { label: c.label })}
                className={cn(
                  'flex h-6 items-center gap-1 rounded-sm bg-accent-subtle px-1.5 text-xs text-accent',
                  'outline-none transition-colors hover:bg-accent/15 focus-visible:focus-ring',
                )}
              >
                {c.label}
                <X size={10} />
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
                    onAdd={() => addPanel(it.panel)}
                    onZoom={() => setZoomed(it)}
                    onMove={(d) => move(it.panel.id, d)}
                    columns={columns}
                  />
                ) : (
                  <RuntimeAssetCard
                    key={it.asset.id}
                    asset={it.asset}
                    used={usage.get(it.asset.id) ?? 0}
                    selected={activeId === it.asset.id}
                    tabbable={focusId === it.asset.id}
                    onSelect={() => setActiveId(it.asset.id)}
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
              if (zoomed?.kind === 'file') addPanel(zoomed.panel)
              else if (zoomed?.kind === 'runtime' && zoomed.asset.descriptor)
                addRuntimePanel(zoomed.asset.descriptor)
              setZoomed(null)
            }}
          >
            <Plus size={14} />
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

/** 区标题：图 / 脚本 两个区的分隔（计数可选） */
function SectionHeading({ label, count }: { label: string; count?: number }) {
  return (
    <h3 className="flex items-center gap-1.5 px-3 pb-1 pt-1.5 text-xs font-medium text-ink-2">
      {label}
      {count !== undefined && <span className="font-mono text-ink-3">{count}</span>}
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
        <Button
          size="icon-sm"
          active={activeCount > 0}
          aria-label={activeCount ? ab('filterActiveAria', { count: activeCount }) : ab('filterAria')}
        >
          <ListFilter size={13} className={activeCount ? undefined : 'text-ink-2'} />
        </Button>
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

/** 骨架与真实卡片同尺寸（4:3 图片区 + 两行文字），加载完不跳版 */
function GridSkeleton({ columns }: { columns: number }) {
  return (
    <ul
      aria-hidden
      className="grid gap-2"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: 8 }, (_, i) => (
        <li key={i} className="rounded-sm border border-border bg-surface">
          <div className="aspect-[4/3] animate-pulse rounded-t-sm bg-ink/[.05]" />
          <div className="px-1.5">
            <div className="h-3 animate-pulse rounded-sm bg-ink/[.06]" />
            <div className="h-3 w-3/5 animate-pulse rounded-sm bg-ink/[.04]" />
          </div>
        </li>
      ))}
    </ul>
  )
}

/**
 * 一张素材卡：图片占绝大部分面积，识别靠图不靠文件名。
 * 单击选中、Enter 加入、Space 看大图、方向键在网格里走；双击与拖拽保留为快捷方式。
 */
function AssetCard({
  panel,
  used,
  selected,
  tabbable,
  onSelect,
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
  onAdd: () => void
  onZoom: () => void
  onMove: (delta: number) => void
  columns: number
}) {
  useTranslation('workspace')
  const name = fileName(panel.id)
  const label = [
    name,
    formatOf(panel),
    panel.script ? ab('cardParameterizable') : null,
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
      tabIndex={tabbable ? 0 : -1}
      data-card={panel.id}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('application/x-panel-id', panel.id)
        e.dataTransfer.effectAllowed = 'copy'
      }}
      onClick={onSelect}
      onDoubleClick={onAdd}
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
          onAdd()
        } else if (e.key === ' ') {
          e.preventDefault()
          onZoom()
        } else if (step[e.key] !== undefined) {
          e.preventDefault()
          onMove(step[e.key])
        }
      }}
      title={ab('cardTitle', { id: panel.id })}
      className={cn(
        'group relative cursor-grab overflow-hidden rounded-sm border bg-surface outline-none',
        'transition-colors active:cursor-grabbing',
        selected
          ? 'border-accent bg-accent-subtle'
          : 'border-border hover:border-border-strong',
        'focus-visible:focus-ring',
      )}
      style={{ contentVisibility: 'auto', containIntrinsicSize: '150px' }}
    >
      <div className="relative flex aspect-[4/3] items-center justify-center overflow-hidden border-b border-border bg-white">
        <img
          loading="lazy"
          src={renderUrl(panel.id, 400, panel.mtime)}
          alt=""
          draggable={false}
          className="max-h-full max-w-full object-contain p-1"
        />

        <span className="pointer-events-none absolute left-1 top-1 flex items-center gap-1">
          <span className="rounded-[3px] bg-ink/[.72] px-1 font-mono text-xs leading-4 text-white">
            {formatOf(panel)}
          </span>
          {panel.script && (
            <span
              className="flex h-4 w-4 items-center justify-center rounded-[3px] bg-ink/[.72] text-white"
              title={ab('scriptBadgeTitle')}
            >
              <Braces size={10} />
            </span>
          )}
        </span>

        {used > 0 && (
          <span
            className="pointer-events-none absolute right-1 top-1 rounded-[3px] bg-ink/[.72] px-1 font-mono text-xs leading-4 text-white"
            title={ab('cardUsed', { count: used })}
          >
            ×{used}
          </span>
        )}

        {/* 不是 <button>：option 里不许再嵌交互控件（axe nested-interactive，
            serious）——哪怕 tabIndex=-1 也算。它只是鼠标用户的就近入口，
            键盘/读屏用户在 option 上按 Enter 走的就是同一个 onAdd（可达名
            由 addAria 落在 option 的 aria-keyshortcuts 语境里，能力没少）。 */}
        <span
          title={ab('addAria', { name })}
          onClick={(e) => {
            e.stopPropagation()
            onAdd()
          }}
          className={cn(
            'absolute bottom-1 right-1 flex h-6 cursor-pointer items-center gap-1 rounded-sm',
            'border border-border bg-surface px-1.5 text-xs text-ink hover:border-border-strong hover:bg-surface-2',
            'opacity-0 transition-opacity select-none',
            'group-hover:opacity-100 group-focus-visible:opacity-100',
            selected && 'opacity-100',
          )}
        >
          <Plus size={11} />
          {ab('addToCanvas')}
        </span>
      </div>

      {/* 文字区压到最薄：图片区要占到卡片约 80%，识别靠图不靠字 */}
      <div className="px-1.5 py-0.5">
        <p
          className={cn('truncate text-xs leading-4', selected ? 'text-accent' : 'text-ink')}
          title={name}
        >
          {name}
        </p>
        <p className="truncate font-mono text-xs leading-4 text-ink-3">
          {translate('measure.cmSize', {
            w: formatCm(panel.native_w_mm),
            h: formatCm(panel.native_h_mm),
          })}
          {used ? ab('usedSuffix', { count: used }) : ''}
        </p>
      </div>
    </li>
  )
}

/**
 * 一张运行时图卡（RuntimeFigureAsset，ADR 0013）：没有磁盘原件，预览来自
 * materialized cache。有描述符（跑过）→ Enter/双击加入画布；还没跑过 →
 * 主动作变成「运行并发现图」（尺寸与内容只有运行后才知道，绝不给假路径 /
 * 假尺寸——负向反证 #2：这里要求磁盘 path 的话加入画布当场断）。
 */
function RuntimeAssetCard({
  asset,
  used,
  selected,
  tabbable,
  onSelect,
  onZoom,
  onMove,
  columns,
}: {
  asset: RuntimeAssetInfo
  used: number
  selected: boolean
  tabbable: boolean
  onSelect: () => void
  onZoom: () => void
  onMove: (delta: number) => void
  columns: number
}) {
  useTranslation('workspace')
  const nonce = useRuntimeAssetStore((s) => s.previewNonce[asset.id])
  const run = useScriptRunStore((s) => s.byScript[asset.script])
  const busy = !!run && isBusyPhase(run.phase)

  const primary = () => {
    if (asset.descriptor) addRuntimePanel(asset.descriptor)
    else if (!busy) void useScriptRunStore.getState().run(asset.script)
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
          primary()
        } else if (e.key === ' ') {
          e.preventDefault()
          onZoom()
        } else if (step[e.key] !== undefined) {
          e.preventDefault()
          onMove(step[e.key])
        }
      }}
      title={ab('runtimeCardTitle', { stem: asset.stem, script: asset.script })}
      className={cn(
        'group relative overflow-hidden rounded-sm border bg-surface outline-none transition-colors',
        selected
          ? 'border-accent bg-accent-subtle'
          : 'border-border hover:border-border-strong',
        'focus-visible:focus-ring',
      )}
      style={{ contentVisibility: 'auto', containIntrinsicSize: '150px' }}
    >
      <div className="relative flex aspect-[4/3] items-center justify-center overflow-hidden border-b border-border bg-white">
        {asset.cached ? (
          <img
            loading="lazy"
            src={runtimePreviewUrl(asset.id, nonce)}
            alt=""
            draggable={false}
            className="max-h-full max-w-full object-contain p-1"
          />
        ) : (
          <span className="flex flex-col items-center gap-1 p-2 text-center text-xs text-ink-3">
            <Play size={16} className="text-ink-faint" />
            {ab('runtimeNeedsRun')}
          </span>
        )}

        <span className="pointer-events-none absolute left-1 top-1 flex items-center gap-1">
          <span className="flex items-center gap-0.5 rounded-[3px] bg-ink/[.72] px-1 text-xs leading-4 text-white">
            <Zap size={9} />
            {ab('runtimeBadge')}
          </span>
        </span>

        {used > 0 && (
          <span
            className="pointer-events-none absolute right-1 top-1 rounded-[3px] bg-ink/[.72] px-1 font-mono text-xs leading-4 text-white"
            title={ab('cardUsed', { count: used })}
          >
            ×{used}
          </span>
        )}

        {/* 主动作（与文件卡同款的就近入口，非嵌套控件）：有描述符 = 加入
            画布；没有 = 运行并发现图 */}
        <span
          title={
            asset.descriptor
              ? ab('addAria', { name: asset.stem })
              : ab('runtimeRunAria', { script: asset.script })
          }
          onClick={(e) => {
            e.stopPropagation()
            primary()
          }}
          className={cn(
            'absolute bottom-1 right-1 flex h-6 cursor-pointer items-center gap-1 rounded-sm',
            'border border-border bg-surface px-1.5 text-xs text-ink hover:border-border-strong hover:bg-surface-2',
            'opacity-0 transition-opacity select-none',
            'group-hover:opacity-100 group-focus-visible:opacity-100',
            selected && 'opacity-100',
          )}
        >
          {asset.descriptor ? <Plus size={11} /> : <Play size={11} />}
          {busy
            ? translate('scripts.running', { ns: 'workspace' })
            : asset.descriptor
              ? ab('addToCanvas')
              : translate('scripts.run', { ns: 'workspace' })}
        </span>
      </div>

      <div className="px-1.5 py-0.5">
        <p
          className={cn('truncate text-xs leading-4', selected ? 'text-accent' : 'text-ink')}
          title={asset.stem}
        >
          {asset.stem}
        </p>
        <p className="truncate font-mono text-xs leading-4 text-ink-3" title={asset.script}>
          {asset.size_mm
            ? translate('measure.cmSize', {
                w: formatCm(asset.size_mm[0]),
                h: formatCm(asset.size_mm[1]),
              })
            : asset.script}
          {used ? ab('usedSuffix', { count: used }) : ''}
        </p>
        {staleKey && (
          <p className="flex items-center justify-between gap-1 pb-0.5 text-xs leading-4">
            <span className="min-w-0 truncate text-danger">
              {translate(`panelBadge.${staleKey}`, { ns: 'workspace' })}
            </span>
            <button
              onClick={(e) => {
                e.stopPropagation()
                rerun()
              }}
              disabled={busy}
              className="shrink-0 rounded-sm text-ink-3 outline-none hover:text-ink focus-visible:focus-ring disabled:opacity-40"
            >
              {translate(`scripts.${busy ? 'running' : 'rerun'}`, { ns: 'workspace' })}
            </button>
          </p>
        )}
      </div>
    </li>
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
