import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Braces, ListFilter, Plus, RotateCw, Search, X,
  SearchX,
  ImageOff,
  TriangleAlert,
} from 'lucide-react'
import { renderUrl, type PanelInfo } from '@/lib/api'
import { formatCm } from '@/lib/units'
import { cn } from '@/lib/utils'
import { addPanel } from '@/store/actions'
import { folderLabel, useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Dialog } from '../ui/Dialog'
import { Popover } from '../ui/Popover'
import { Row } from '../ui/Field'
import { Select } from '../ui/Select'
import { Toggle } from '../ui/Toggle'
import { Tip } from '../ui/Tooltip'

/** 面板文件名（带扩展名）：同 stem 的 PDF / PNG 靠它区分 */
const fileName = (id: string) => id.split('/').pop() ?? id
const formatOf = (p: PanelInfo) => (p.kind === 'pdf' ? 'PDF' : 'PNG')

type TypeFilter = 'all' | 'pdf' | 'raster' | 'script'
type SortKey = 'name' | 'recent' | 'used'

const TYPE_OPTIONS: { value: TypeFilter; label: string }[] = [
  { value: 'all', label: '全部类型' },
  { value: 'pdf', label: 'PDF' },
  { value: 'raster', label: '图片' },
  { value: 'script', label: '可参数化' },
]

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'name', label: '按名称' },
  { value: 'recent', label: '最近使用' },
  { value: 'used', label: '使用次数' },
]

interface Filters {
  source: string
  type: TypeFilter
  sort: SortKey
  usedOnly: boolean
}

const DEFAULT_FILTERS: Filters = { source: 'all', type: 'all', sort: 'name', usedOnly: false }

export function AssetBrowser() {
  const panels = useAssetStore((s) => s.panels)
  const loading = useAssetStore((s) => s.loading)
  const loaded = useAssetStore((s) => s.loaded)
  const error = useAssetStore((s) => s.error)
  const figuresDir = useAssetStore((s) => s.figuresDir)
  const recentlyUsed = useAssetStore((s) => s.recentlyUsed)
  const objects = useDocumentStore((s) => s.doc.objects)

  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [zoomed, setZoomed] = useState<PanelInfo | null>(null)

  const gridRef = useRef<HTMLDivElement>(null)
  const [columns, setColumns] = useState(1)

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
  const items = useMemo(() => {
    const q = query.trim().toLowerCase()
    let list = panels.filter((p) => {
      if (q && !p.id.toLowerCase().includes(q) && !p.name.toLowerCase().includes(q)) return false
      if (usedOnly && !usage.has(p.id)) return false
      if (source !== 'all' && p.folder !== source) return false
      if (type === 'pdf' && p.kind !== 'pdf') return false
      if (type === 'raster' && p.kind !== 'raster') return false
      if (type === 'script' && !p.script) return false
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
    return list
  }, [panels, query, source, type, sort, usedOnly, usage, recentlyUsed])

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
    if (activeId && !items.some((p) => p.id === activeId)) setActiveId(null)
  }, [items, activeId])

  const focusCard = (id: string) => {
    setActiveId(id)
    gridRef.current
      ?.querySelector<HTMLElement>(`[data-card="${CSS.escape(id)}"]`)
      ?.focus({ preventScroll: false })
  }

  const move = (from: string, delta: number) => {
    const i = items.findIndex((p) => p.id === from)
    const next = items[i + delta]
    if (next) focusCard(next.id)
  }

  const focusId = items.some((p) => p.id === activeId) ? activeId : items[0]?.id

  /** 非默认筛选条件 → 可移除的标签 */
  const chips: { key: keyof Filters; label: string }[] = []
  if (source !== 'all') chips.push({ key: 'source', label: folderLabel(source) })
  if (type !== 'all') {
    chips.push({ key: 'type', label: TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type })
  }
  if (usedOnly) chips.push({ key: 'usedOnly', label: '已使用' })
  if (sort !== 'name') {
    chips.push({ key: 'sort', label: SORT_OPTIONS.find((o) => o.value === sort)?.label ?? sort })
  }

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
              placeholder="搜索面板…"
              aria-label="搜索面板"
              className={cn(
                'h-7 w-full rounded-sm border border-transparent bg-surface-2 pl-6.5 pr-1.5 text-xs',
                'text-ink placeholder:text-ink-faint outline-none transition-colors',
                'hover:border-border focus:border-accent focus:bg-surface',
              )}
            />
          </div>
          {/* 一键只看可参数化：等价于筛选弹层里的类型=可参数化，走同一份状态，
              生效时下方出现同一个可移除的筛选标签 */}
          <Tip label="只看可参数化面板">
            <Button
              size="icon-sm"
              active={type === 'script'}
              onClick={() =>
                setFilters((f) => ({ ...f, type: f.type === 'script' ? 'all' : 'script' }))
              }
              aria-label="只看可参数化面板"
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
          <Tip label="重新扫描素材目录">
            <Button size="icon-sm" onClick={() => useAssetStore.getState().load()} aria-label="重新扫描">
              <RotateCw size={12} className={loading ? 'animate-spin text-ink-3' : 'text-ink-2'} />
            </Button>
          </Tip>
        </div>

        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1" aria-label="生效中的筛选">
            {chips.map((c) => (
              <button
                key={c.key}
                onClick={() => clearChip(c.key)}
                aria-label={`移除筛选：${c.label}`}
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

      {loading && loaded && (
        <p className="px-3 py-1 text-xs text-ink-3">正在重新扫描…</p>
      )}
      {error && loaded && (
        <p className="bg-danger-subtle px-3 py-1.5 text-xs text-danger">
          刷新失败：{error}
        </p>
      )}

      <div ref={gridRef} className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
        {error && !loaded && (
          <EmptyState
            icon={TriangleAlert}
            title="素材库读取失败"
            hint={error}
            action={{ label: '重试', onClick: () => void useAssetStore.getState().load() }}
          />
        )}

        {!loaded && !error && <GridSkeleton columns={columns} />}

        {loaded && !error && items.length === 0 && (
          query || chips.length ? (
            <EmptyState icon={SearchX} title="没有符合条件的面板" />
          ) : (
            <EmptyState
              icon={ImageOff}
              title="项目里还没有可用面板"
              hint="把 matplotlib 输出的 PDF/PNG 放进项目目录即可出现在这里。"
            />
          )
        )}

        {items.length > 0 && (
          <ul
            role="listbox"
            aria-label="素材面板"
            className="grid gap-2"
            style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
          >
            {items.map((p) => (
              <AssetCard
                key={p.id}
                panel={p}
                used={usage.get(p.id) ?? 0}
                selected={activeId === p.id}
                tabbable={focusId === p.id}
                onSelect={() => setActiveId(p.id)}
                onAdd={() => addPanel(p)}
                onZoom={() => setZoomed(p)}
                onMove={(d) => move(p.id, d)}
                columns={columns}
              />
            ))}
          </ul>
        )}
      </div>

      {figuresDir && <FolderInfo dir={figuresDir} shown={items.length} total={panels.length} />}

      <Dialog
        open={!!zoomed}
        onOpenChange={(v) => !v && setZoomed(null)}
        title={zoomed ? fileName(zoomed.id) : ''}
        description={
          zoomed
            ? `${formatOf(zoomed)} · ${formatCm(zoomed.native_w_mm)}×${formatCm(zoomed.native_h_mm)}cm`
            : ''
        }
        size="lg"
        footer={
          <Button
            variant="primary"
            size="md"
            onClick={() => {
              if (zoomed) addPanel(zoomed)
              setZoomed(null)
            }}
          >
            <Plus size={14} />
            加入画布
          </Button>
        }
      >
        {zoomed && (
          <div className="flex items-center justify-center rounded-sm border border-border bg-white p-2">
            <img
              src={renderUrl(zoomed.id, 800, zoomed.mtime)}
              alt={`${fileName(zoomed.id)} 大图预览`}
              className="max-h-[56vh] max-w-full object-contain"
            />
          </div>
        )}
      </Dialog>
    </div>
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
  const patch = (p: Partial<Filters>) => onChange({ ...filters, ...p })
  return (
    <Popover
      width={224}
      align="end"
      trigger={
        <Button
          size="icon-sm"
          active={activeCount > 0}
          aria-label={activeCount ? `筛选（${activeCount} 项生效）` : '筛选'}
        >
          <ListFilter size={13} className={activeCount ? undefined : 'text-ink-2'} />
        </Button>
      }
    >
      <div className="flex flex-col gap-1.5">
        <Row label="来源" labelWidth={36}>
          <Select
            className="min-w-0 flex-1"
            value={filters.source}
            onChange={(source) => patch({ source })}
            ariaLabel="来源筛选"
            options={[
              { value: 'all', label: '全部来源' },
              ...folders.map((f) => ({ value: f, label: folderLabel(f) })),
            ]}
          />
        </Row>
        <Row label="类型" labelWidth={36}>
          <Select
            className="min-w-0 flex-1"
            value={filters.type}
            onChange={(type) => patch({ type })}
            ariaLabel="类型筛选"
            options={TYPE_OPTIONS}
          />
        </Row>
        <Row label="排序" labelWidth={36}>
          <Select
            className="min-w-0 flex-1"
            value={filters.sort}
            onChange={(sort) => patch({ sort })}
            ariaLabel="排序方式"
            options={SORT_OPTIONS}
          />
        </Row>
        <Row label="已使用" labelWidth={36}>
          <Toggle
            checked={filters.usedOnly}
            onChange={(usedOnly) => patch({ usedOnly })}
            aria-label="只看当前文档已使用的素材"
          />
        </Row>
        {activeCount > 0 && (
          <Button size="sm" className="self-end text-ink-2" onClick={() => onChange(DEFAULT_FILTERS)}>
            重置筛选
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
  const name = fileName(panel.id)
  const label = [
    name,
    formatOf(panel),
    panel.script ? '可参数化' : null,
    `${formatCm(panel.native_w_mm)}×${formatCm(panel.native_h_mm)} 厘米`,
    used ? `当前文档已用 ${used} 次` : null,
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
      title={`${panel.id}\n单击选中 · Enter 加入画布 · 空格看大图 · 也可直接拖到画布`}
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
          <span className="rounded-[3px] bg-ink/[.55] px-1 font-mono text-xs leading-4 text-white">
            {formatOf(panel)}
          </span>
          {panel.script && (
            <span
              className="flex h-4 w-4 items-center justify-center rounded-[3px] bg-ink/[.55] text-white"
              title="可参数化：由 matplotlib 脚本生成"
            >
              <Braces size={10} />
            </span>
          )}
        </span>

        {used > 0 && (
          <span
            className="pointer-events-none absolute right-1 top-1 rounded-[3px] bg-ink/[.55] px-1 font-mono text-xs leading-4 text-white"
            title={`当前文档已用 ${used} 次`}
          >
            ×{used}
          </span>
        )}

        <Button
          size="sm"
          variant="outline"
          tabIndex={-1}
          aria-label={`把 ${name} 加入画布`}
          onClick={(e) => {
            e.stopPropagation()
            onAdd()
          }}
          className={cn(
            'absolute bottom-1 right-1 h-6 px-1.5 text-xs opacity-0 transition-opacity',
            'group-hover:opacity-100 group-focus-visible:opacity-100 focus-visible:opacity-100',
            selected && 'opacity-100',
          )}
        >
          <Plus size={11} />
          加入画布
        </Button>
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
          {formatCm(panel.native_w_mm)}×{formatCm(panel.native_h_mm)}cm
          {used ? ` · 已用 ${used} 次` : ''}
        </p>
      </div>
    </li>
  )
}

/** 目录路径是排查用信息，收进可折叠的一行 */
function FolderInfo({ dir, shown, total }: { dir: string; shown: number; total: number }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="shrink-0 px-3 py-1.5">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 rounded-sm text-left text-xs text-ink-3 outline-none hover:text-ink-2 focus-visible:focus-ring"
      >
        文件夹信息
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
