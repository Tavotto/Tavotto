import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import {
  ArrowUpRight,
  Braces,
  ChevronDown,
  ChevronUp,
  Circle,
  Diamond,
  Ellipsis,
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
} from '@/components/ui/icons'
import { FIELD_BOX, FIELD_FOCUS } from '@/components/ui/fieldBox'
import { ICON_SIZE } from '@/components/ui/Icon'
import { EditableFigureIcon } from '@/components/ui/semanticIcons'
import { cn } from '@/lib/utils'
import { listRowClass } from '@/components/ui/listRow'
import { TreeChevron, TreeIcon, treeIndent } from '@/components/ui/TreeRow'
import { useFlip } from '@/lib/motion'
import { renameObject, reorderObject, toggleHidden, toggleLocked } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { objectLabel, type CanvasObject, type LayoutGroup } from '@/types/document'
import { layoutKindLabel } from '@/store/actions'
import { Badge } from '../ui/Badge'
import { IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem, MenuSeparator } from '../ui/Menu'

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

  // 轻量占位：起步的那个主要行动（「添加图」）只在画布中央出现一次（审计 T03），
  // 侧栏不再各配一颗按钮
  if (!zOrder.length) return <EmptyState icon={Layers} title={lt('emptyTitle')} />

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

  // 「上移 / 下移一层」的可换位落点：可见的对象行之间。⋯ 菜单与 Alt+方向键
  // 共用这一份顺序，两处不各数一遍
  const flatIds = rows
    .filter((r): r is Extract<TreeRow, { kind: 'object' }> => r.kind === 'object')
    .map((r) => r.obj.id)

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
    const i = flatIds.indexOf(id)
    const target = i < 0 ? undefined : flatIds[i + delta]
    if (!target) return
    reorderObject(id, target, delta < 0 ? 'above' : 'below')
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
        const zi = flatIds.indexOf(o.id)
        return (
          <LayerRow
            key={o.id}
            obj={o}
            depth={r.depth}
            selected={selected}
            primary={selectedIds.at(-1) === o.id && selectedIds.length > 1}
            tabbable={focusKey === o.id}
            dropHint={hint}
            canMoveUp={zi > 0}
            canMoveDown={zi >= 0 && zi < flatIds.length - 1}
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
      style={treeIndent(0)}
      className={cn(listRowClass({ selected: allSelected, muted: true }), 'gap-1.5 pr-2')}
    >
      <TreeChevron
        expanded={!collapsed}
        onToggle={onToggle}
        label={lt(collapsed ? 'expandGroup' : 'collapseGroup')}
      />
      <TreeIcon icon={Layers} selected={allSelected} />
      <span className="min-w-0 flex-1 truncate">
        {lt('groupLabel', { count: members.length })}
      </span>
      {layout && <Badge>{layoutKindLabel(layout.kind)}</Badge>}
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
  /** 这一行上下还有没有可换位的对象——决定 ⋯ 里那两项禁不禁用 */
  canMoveUp: boolean
  canMoveDown: boolean
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
  canMoveUp,
  canMoveDown,
  onDropHint,
  onMoveFocus,
  onReorder,
}: RowProps) {
  useTranslation('workspace')
  const [editing, setEditing] = useState(false)
  const Icon = iconFor(obj)
  const isScript = obj.type === 'panel' && !!obj.script
  // 可编辑（能进图内编辑）与隐藏 / 锁定一样进可达名：角标只是视觉记号
  const stateLabel = [
    isScript && lt('editableState'),
    obj.hidden && lt('hiddenState'),
    obj.locked && lt('lockedState'),
  ]
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
      style={treeIndent(depth)}
      className={cn(
        listRowClass({ selected, hidden: obj.hidden }),
        'gap-1.5 pr-0.5',
        dropHint === 'above' && 'shadow-[inset_0_1px_0_0_var(--color-accent)]',
        dropHint === 'below' && 'shadow-[inset_0_-1px_0_0_var(--color-accent)]',
      )}
    >
      {/* 顶层对象没有折叠箭头，留一个空列：与组标题行的图标对齐 */}
      <TreeChevron />
      <TreeIcon icon={Icon} selected={selected} />
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
          // 行内改名框是「可编辑框」那一副（fieldBox），28 高 = 整行（左栏审计 L31）
          className={cn('h-7 min-w-0 flex-1 px-1.5 outline-none', FIELD_BOX, FIELD_FOCUS)}
        />
      ) : (
        <span className="min-w-0 flex-1 truncate">{objectLabel(obj)}</span>
      )}
      {/* 「可编辑的图」角标与素材卡同源（`ui/semanticIcons`），尺寸与色也同源：xs / ink-3——
          角标不该比行首 14px 的类型图标更响（左栏审计 L28）；行首的 Braces 是大括号形状的
          种类图标，两个角色靠位置（行尾）与图形分开。悬停说清它是什么——审计 B04：一个
          没有名字的记号只能靠猜 */}
      {isScript && !editing && (
        <span
          className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-3"
          title={lt('editableBadge')}
        >
          <EditableFigureIcon size={ICON_SIZE.xs} aria-hidden />
        </span>
      )}
      {/* 「基准」是一个词不是取值代号：type-meta，不用等宽（左栏审计 L29） */}
      {primary && !editing && (
        <span className="shrink-0 type-meta font-normal">{lt('primary')}</span>
      )}

      {/* 锁定 / 隐藏状态常驻，动作收进 ⋯——与图内元素行同一副行尾（2026-09-15 全面打磨拍板）。
          此前这里是两颗常驻的 28px 钮：省一次点击，代价是每一行都挂着两颗钮，
          两棵并列的树对同一对操作长出两种形态。状态图标是 xs / ink-3，与元素行同源 */}
      {obj.locked && (
        <Lock size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-label={lt('lockedState')} />
      )}
      {obj.hidden && (
        <EyeOff size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-label={lt('hiddenState')} />
      )}

      {/* 低频操作收进 ⋯，hover / 键盘落到行里才出现（键盘可达靠 focus-within） */}
      <span
        className={cn(
          'ml-auto shrink-0 transition-opacity duration-fast',
          !editing && 'opacity-0 group-focus-within:opacity-100 group-hover:opacity-100',
        )}
      >
        <Menu
          width={168}
          align="end"
          trigger={
            <IconButton
              iconSize="sm"
              tabIndex={-1}
              onPointerDown={(e) => e.stopPropagation()}
              label={lt('rowActions', { label: objectLabel(obj) })}
            >
              <Ellipsis size={ICON_SIZE.sm} className="text-ink-3" />
            </IconButton>
          }
        >
          <MenuItem icon={obj.locked ? LockOpen : Lock} onSelect={() => toggleLocked(obj.id)}>
            {lt(obj.locked ? 'unlock' : 'lock')}
          </MenuItem>
          <MenuItem icon={obj.hidden ? Eye : EyeOff} onSelect={() => toggleHidden(obj.id)}>
            {lt(obj.hidden ? 'show' : 'hide')}
          </MenuItem>
          <MenuSeparator />
          {/* 层级动作作用在**这一行**上，不看选区——菜单是从这一行打开的。
              走的是行自己那条 `onReorder`（与 Alt+方向键同一份实现），
              到顶 / 到底时那一项禁用，而不是点了什么都不发生 */}
          <MenuItem icon={ChevronUp} disabled={!canMoveUp} onSelect={() => onReorder(-1)}>
            {lt('moveUp')}
          </MenuItem>
          <MenuItem icon={ChevronDown} disabled={!canMoveDown} onSelect={() => onReorder(1)}>
            {lt('moveDown')}
          </MenuItem>
        </Menu>
      </span>
    </li>
  )
}
