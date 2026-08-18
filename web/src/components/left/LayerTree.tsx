import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import {
  ArrowUpRight,
  Braces,
  ChevronRight,
  Circle,
  Diamond,
  Eye,
  EyeOff,
  Hexagon,
  Image,
  Layers,
  Lock,
  LockOpen,
  Slash,
  Square,
  Triangle,
  Type,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useFlip } from '@/lib/motion'
import { renameObject, reorderObject, toggleHidden, toggleLocked } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { objectLabel, type CanvasObject, type LayoutGroup } from '@/types/document'
import { layoutKindLabel } from '@/store/actions'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'

const ICONS = {
  panel: Image,
  text: Type,
  arrow: ArrowUpRight,
  rect: Square,
  ellipse: Circle,
  line: Slash,
  triangle: Triangle,
  diamond: Diamond,
  polygon: Hexagon,
  brace: Braces,
} as const

function iconFor(o: CanvasObject) {
  if (o.type === 'shape') return ICONS[o.shape]
  return ICONS[o.type]
}

/** 本组文案在 workspace:layerTree.* 下；布局徽标与 actions 的布局名同源 */
const lt = (key: string, values?: Record<string, unknown>) =>
  translate(`layerTree.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 显示行：普通对象 / 组标题（组成员挂在标题下，可折叠） */
type TreeRow =
  | { kind: 'object'; obj: CanvasObject; depth: 0 | 1 }
  | { kind: 'group'; gid: string; members: CanvasObject[] }

export function LayerTree() {
  useTranslation('workspace')
  const objects = useDocumentStore((s) => s.doc.objects)
  const layoutGroups = useDocumentStore((s) => s.doc.layoutGroups)
  const selectedIds = useSelectionStore((s) => s.ids)
  const [dropHint, setDropHint] = useState<{ id: string; pos: 'above' | 'below' } | null>(null)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const listRef = useRef<HTMLUListElement>(null)
  // 重排是 drop 那一刻整列换位的（拖动中只有一条落点提示线）；折叠/展开组
  // 也会让下面所有行整体位移。不给动效的话行「啪」地跳，看不出是哪一行动了
  useFlip(listRef, 'data-layer')

  // 顶层在最上面，与画布的视觉层级一致
  const zOrder = [...objects].reverse()

  if (!zOrder.length) {
    return (
      <EmptyState
        icon={Layers}
        title={lt('emptyTitle')}
        action={{
          label: lt('openAssets'),
          onClick: () => useUiStore.getState().railClick('assets'),
        }}
      />
    )
  }

  // 成组的对象折进组标题下（在最上层成员的位置出现一次）
  const rows: TreeRow[] = []
  const seenGroups = new Set<string>()
  for (const o of zOrder) {
    if (!o.groupId) {
      rows.push({ kind: 'object', obj: o, depth: 0 })
      continue
    }
    if (seenGroups.has(o.groupId)) continue
    seenGroups.add(o.groupId)
    const members = zOrder.filter((x) => x.groupId === o.groupId)
    rows.push({ kind: 'group', gid: o.groupId, members })
    if (!collapsed[o.groupId]) {
      for (const m of members) rows.push({ kind: 'object', obj: m, depth: 1 })
    }
  }

  // 键盘漫游走可见行（对象行 + 组标题行共用 data-layer 定位）
  const keyOf = (r: TreeRow) => (r.kind === 'object' ? r.obj.id : `g:${r.gid}`)
  const focusRow = (key: string) =>
    listRef.current?.querySelector<HTMLElement>(`[data-layer="${CSS.escape(key)}"]`)?.focus()

  const moveFocus = (from: string, delta: number) => {
    const i = rows.findIndex((r) => keyOf(r) === from)
    const next = rows[i + delta]
    if (next) focusRow(keyOf(next))
  }

  // Alt+方向键调整 z 序；行的 DOM 会重建，等提交后把焦点接回来
  const reorder = (id: string, delta: -1 | 1) => {
    const flat = rows.filter((r): r is Extract<TreeRow, { kind: 'object' }> => r.kind === 'object')
    const i = flat.findIndex((r) => r.obj.id === id)
    const target = flat[i + delta]
    if (!target) return
    reorderObject(id, target.obj.id, delta < 0 ? 'above' : 'below')
    // 等 React 提交后接回焦点；不用 rAF——后台/隐藏标签页里 rAF 可能永不触发
    setTimeout(() => focusRow(id), 0)
  }

  // 让 Tab 落点稳定：优先当前选中（基准）行，否则第一行
  const focusKey =
    rows.find((r) => r.kind === 'object' && r.obj.id === selectedIds.at(-1)) != null
      ? selectedIds.at(-1)!
      : keyOf(rows[0])

  return (
    <ul
      ref={listRef}
      role="listbox"
      aria-label={lt('listLabel')}
      aria-multiselectable
      className="min-h-0 flex-1 overflow-y-auto py-1"
      onDragLeave={() => setDropHint(null)}
      onDrop={() => setDropHint(null)}
    >
      {rows.map((r) => {
        if (r.kind === 'group') {
          return (
            <GroupRow
              key={`g:${r.gid}`}
              gid={r.gid}
              members={r.members}
              layout={layoutGroups?.find((g) => g.id === r.gid)}
              collapsed={!!collapsed[r.gid]}
              tabbable={focusKey === `g:${r.gid}`}
              allSelected={r.members.every((m) => selectedIds.includes(m.id))}
              onToggle={() => setCollapsed((s) => ({ ...s, [r.gid]: !s[r.gid] }))}
              onMoveFocus={(d) => moveFocus(`g:${r.gid}`, d)}
            />
          )
        }
        const o = r.obj
        const selected = selectedIds.includes(o.id)
        const hint = dropHint?.id === o.id ? dropHint.pos : null
        return (
          <LayerRow
            key={o.id}
            obj={o}
            depth={r.depth}
            selected={selected}
            primary={selectedIds.at(-1) === o.id && selectedIds.length > 1}
            tabbable={focusKey === o.id}
            dropHint={hint}
            onDropHint={setDropHint}
            onMoveFocus={(d) => moveFocus(o.id, d)}
            onReorder={(d) => reorder(o.id, d)}
          />
        )
      })}
    </ul>
  )
}

/** 组标题行：点击选中整组；箭头折叠 / 展开；显示布局约束徽标 */
function GroupRow({
  gid,
  members,
  layout,
  collapsed,
  tabbable,
  allSelected,
  onToggle,
  onMoveFocus,
}: {
  gid: string
  members: CanvasObject[]
  layout?: LayoutGroup
  collapsed: boolean
  tabbable: boolean
  allSelected: boolean
  onToggle: () => void
  onMoveFocus: (delta: number) => void
}) {
  useTranslation('workspace')
  const selectAllMembers = () => useSelectionStore.getState().set(members.map((m) => m.id))
  return (
    <li
      role="option"
      aria-selected={allSelected}
      aria-expanded={!collapsed}
      aria-label={
        layout
          ? lt('groupAriaWithLayout', {
              count: members.length,
              layout: layoutKindLabel(layout.kind),
            })
          : lt('groupAria', { count: members.length })
      }
      tabIndex={tabbable ? 0 : -1}
      data-layer={`g:${gid}`}
      onPointerDown={(e) => {
        if (e.button === 0) selectAllMembers()
      }}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault()
          e.stopPropagation()
          onMoveFocus(e.key === 'ArrowDown' ? 1 : -1)
        } else if (e.key === 'ArrowRight' && collapsed) {
          e.preventDefault()
          e.stopPropagation()
          onToggle()
        } else if (e.key === 'ArrowLeft' && !collapsed) {
          e.preventDefault()
          e.stopPropagation()
          onToggle()
        } else if (e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          selectAllMembers()
        }
      }}
      className={cn(
        'group flex h-7 cursor-default items-center gap-1 border-l-2 px-1.5 text-xs outline-none focus-visible:focus-ring',
        allSelected
          ? 'border-accent bg-accent-subtle text-accent'
          : 'border-transparent text-ink-2 hover:bg-ink/[.04]',
      )}
    >
      <button
        onPointerDown={(e) => e.stopPropagation()}
        onClick={onToggle}
        aria-label={lt(collapsed ? 'expandGroup' : 'collapseGroup')}
        tabIndex={-1}
        className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-3 hover:text-ink"
      >
        <ChevronRight size={11} className={cn('transition-transform', !collapsed && 'rotate-90')} />
      </button>
      <span className="min-w-0 flex-1 truncate">
        {lt('groupLabel', { count: members.length })}
      </span>
      {layout && (
        <span className="shrink-0 rounded-[3px] border border-border px-1 text-xs text-ink-3">
          {layoutKindLabel(layout.kind)}
        </span>
      )}
    </li>
  )
}

interface RowProps {
  obj: CanvasObject
  depth?: 0 | 1
  selected: boolean
  primary: boolean
  tabbable: boolean
  dropHint: 'above' | 'below' | null
  onDropHint: (h: { id: string; pos: 'above' | 'below' } | null) => void
  onMoveFocus: (delta: number) => void
  onReorder: (delta: -1 | 1) => void
}

function LayerRow({
  obj,
  depth = 0,
  selected,
  primary,
  tabbable,
  dropHint,
  onDropHint,
  onMoveFocus,
  onReorder,
}: RowProps) {
  useTranslation('workspace')
  const [editing, setEditing] = useState(false)
  const Icon = iconFor(obj)
  const isScript = obj.type === 'panel' && !!obj.script
  const stateLabel = [obj.hidden && lt('hiddenState'), obj.locked && lt('lockedState')]
    .filter(Boolean)
    .join('，')

  return (
    <li
      role="option"
      aria-selected={selected}
      aria-label={
        stateLabel ? lt('rowAria', { label: objectLabel(obj), state: stateLabel }) : objectLabel(obj)
      }
      tabIndex={tabbable ? 0 : -1}
      data-layer={obj.id}
      onFocus={(e) => {
        // 焦点即选中（方向键漫游）；子按钮的焦点冒泡上来时不动选区
        if (e.target === e.currentTarget && !selected) useSelectionStore.getState().set([obj.id])
      }}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault()
          e.stopPropagation()
          const delta = e.key === 'ArrowDown' ? 1 : -1
          if (e.altKey) onReorder(delta as -1 | 1)
          else onMoveFocus(delta)
        } else if (e.key === 'Enter' || e.key === 'F2') {
          e.preventDefault()
          e.stopPropagation()
          setEditing(true)
        }
      }}
      draggable={!editing}
      onDragStart={(e) => {
        e.dataTransfer.setData('application/x-layer-id', obj.id)
        e.dataTransfer.effectAllowed = 'move'
      }}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes('application/x-layer-id')) return
        e.preventDefault()
        const r = e.currentTarget.getBoundingClientRect()
        onDropHint({ id: obj.id, pos: e.clientY < r.top + r.height / 2 ? 'above' : 'below' })
      }}
      onDrop={(e) => {
        const from = e.dataTransfer.getData('application/x-layer-id')
        onDropHint(null)
        if (!from || from === obj.id) return
        e.preventDefault()
        const r = e.currentTarget.getBoundingClientRect()
        reorderObject(from, obj.id, e.clientY < r.top + r.height / 2 ? 'above' : 'below')
      }}
      onPointerDown={(e) => {
        if (editing) return
        const sel = useSelectionStore.getState()
        if (e.shiftKey) sel.toggle(obj.id)
        else sel.set([obj.id])
      }}
      onDoubleClick={() => setEditing(true)}
      style={depth ? { paddingLeft: 8 + depth * 14 } : undefined}
      className={cn(
        'group relative flex h-7 items-center gap-1.5 border-l-2 px-2 text-xs outline-none focus-visible:focus-ring',
        selected
          ? 'border-accent bg-accent-subtle text-accent'
          : 'border-transparent text-ink hover:bg-ink/[.04]',
        obj.hidden && 'opacity-45',
        dropHint === 'above' && 'shadow-[inset_0_1px_0_0_var(--color-accent)]',
        dropHint === 'below' && 'shadow-[inset_0_-1px_0_0_var(--color-accent)]',
      )}
    >
      <Icon size={13} className={cn('shrink-0', selected ? 'text-accent' : 'text-ink-3')} />
      {editing ? (
        <input
          autoFocus
          defaultValue={objectLabel(obj)}
          onBlur={(e) => {
            renameObject(obj.id, e.target.value)
            setEditing(false)
            // 重命名结束把焦点接回行上，方向键漫游不断链
            const li = e.currentTarget.closest('li')
            setTimeout(() => li?.focus(), 0)
          }}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            if (e.key === 'Escape') {
              const li = e.currentTarget.closest('li')
              setEditing(false)
              setTimeout(() => li?.focus(), 0)
            }
          }}
          className="h-5 min-w-0 flex-1 rounded-[3px] border border-accent bg-surface px-1 text-xs text-ink outline-none"
        />
      ) : (
        <span className="min-w-0 flex-1 truncate">{objectLabel(obj)}</span>
      )}
      {/* 可参数化徽标与素材卡的 { } 同源；行首的 Braces 是大括号形状的种类图标，
          位置（行尾）与颜色（accent）把两个角色分开 */}
      {isScript && !editing && <Braces size={12} className="shrink-0 text-accent" />}
      {primary && !editing && (
        <span className="shrink-0 font-mono text-xs text-accent/70">{lt('primary')}</span>
      )}

      <div
        className={cn(
          'ml-auto flex shrink-0 items-center',
          !editing && 'opacity-0 group-focus-within:opacity-100 group-hover:opacity-100',
          obj.locked || obj.hidden ? 'opacity-100' : '',
        )}
      >
        <Button
          size="icon-sm"
          className="h-7 w-6"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => toggleLocked(obj.id)}
          aria-label={lt(obj.locked ? 'unlock' : 'lock')}
        >
          {obj.locked ? <Lock size={12} /> : <LockOpen size={12} className="text-ink-3" />}
        </Button>
        <Button
          size="icon-sm"
          className="h-7 w-6"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => toggleHidden(obj.id)}
          aria-label={lt(obj.hidden ? 'show' : 'hide')}
        >
          {obj.hidden ? <EyeOff size={12} /> : <Eye size={12} className="text-ink-3" />}
        </Button>
      </div>
    </li>
  )
}
