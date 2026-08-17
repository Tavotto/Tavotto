import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Braces,
  ChevronRight,
  Crosshair,
  Eye,
  EyeOff,
  Lock,
  LockOpen,
  MoreHorizontal,
  Search,
  TriangleAlert,
  X,
  SearchX,
} from 'lucide-react'
import type { Manifest, ManifestElement } from '@/lib/api'
import { isElementHidden } from '@/canvas/interactions'
import { cn } from '@/lib/utils'
import {
  enterElementEdit,
  hideElement,
  toggleElementLocked,
  unhideElement,
} from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'
import { roleName, UNSUPPORTED } from '../inspector/roles/registry'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem } from '../ui/Menu'
import { Tip } from '../ui/Tooltip'

/**
 * 图内元素导航器。
 *
 * 由 manifest.elements 的 gid 结构建树（figure → 子图 → 语义聚类 → 元素），
 * 是柱形系列、刻度组、重叠元素这些「画布上点不准」元素的稳定选择入口。
 * 选中走 uiStore.selectedGids —— 与画布点击、ElementInspector、批量编辑同一条通路；
 * 隐藏/恢复走 visible override（非破坏、进撤销）；锁定写在 PanelObject.lockedGids 上。
 */

/** 树节点：真实元素或语义聚类标题（聚类不可选中，只组织层级） */
interface TreeNode {
  el?: ManifestElement
  cluster?: { key: string; label: string }
  children: TreeNode[]
}

const nodeKey = (n: TreeNode, parentKey = ''): string =>
  n.el ? n.el.gid : `${parentKey}#${n.cluster!.key}`

/** gid → 父 gid：按段收缩，刻度文字归到所属刻度组下 */
function parentGid(gid: string, byGid: ReadonlySet<string>): string | null {
  if (gid === 'figure') return null
  const tickm = gid.match(/^(.*)\.([xyz])ticklabels_\d+$/)
  if (tickm && byGid.has(`${tickm[1]}.${tickm[2]}ticks`)) {
    return `${tickm[1]}.${tickm[2]}ticks`
  }
  let cur = gid
  while (cur.includes('.')) {
    cur = cur.slice(0, cur.lastIndexOf('.'))
    if (byGid.has(cur)) return cur
  }
  return 'figure'
}

/** 语义聚类：子图直属元素按角色归组，找不准的元素靠类别缩小范围 */
const CLUSTERS: { key: string; label: string; roles: Set<string> }[] = [
  { key: 'text', label: '文字', roles: new Set(['text', 'title', 'axis_label']) },
  {
    key: 'series',
    label: '数据系列',
    roles: new Set(['line', 'scatter', 'bar_series', 'bar', 'errorbar', 'fill', 'image']),
  },
  { key: 'axis', label: '坐标轴', roles: new Set(['ticks', 'spine', 'grid']) },
  { key: 'legend', label: '图例与色条', roles: new Set(['legend', 'legend_text', 'colorbar']) },
]

const clusterOf = (role: string): (typeof CLUSTERS)[number] | undefined =>
  CLUSTERS.find((c) => c.roles.has(role))

function buildTree(manifest: Manifest): TreeNode[] {
  const nodes = new Map<string, TreeNode>()
  for (const el of manifest.elements) nodes.set(el.gid, { el, children: [] })
  const byGid = new Set(nodes.keys())
  const roots: TreeNode[] = []
  for (const el of manifest.elements) {
    const node = nodes.get(el.gid)!
    const p = parentGid(el.gid, byGid)
    if (p && nodes.has(p)) nodes.get(p)!.children.push(node)
    else roots.push(node)
  }

  // 子图直属元素按语义聚类；元素很少的子图不加聚类层
  for (const node of nodes.values()) {
    const role = node.el?.role
    if (role !== 'axes' && role !== 'axes3d') continue
    if (node.children.length <= 4) continue
    const buckets = new Map<string, TreeNode>()
    const next: TreeNode[] = []
    for (const child of node.children) {
      const c = child.el ? clusterOf(child.el.role) : undefined
      if (!c) {
        next.push(child)
        continue
      }
      let bucket = buckets.get(c.key)
      if (!bucket) {
        bucket = { cluster: { key: c.key, label: c.label }, children: [] }
        buckets.set(c.key, bucket)
        next.push(bucket)
      }
      bucket.children.push(child)
    }
    // 只有一个成员的聚类不值得多一层
    node.children = next.flatMap((n) =>
      n.cluster && n.children.length === 1 ? n.children : [n],
    )
  }
  return roots
}

interface Row {
  node: TreeNode
  depth: number
  key: string
}

function flatten(
  nodes: TreeNode[],
  depth: number,
  parentKey: string,
  isOpen: (n: TreeNode, key: string) => boolean,
  out: Row[],
): Row[] {
  for (const n of nodes) {
    const key = nodeKey(n, parentKey)
    out.push({ node: n, depth, key })
    if (n.children.length && isOpen(n, key)) flatten(n.children, depth + 1, key, isOpen, out)
  }
  return out
}

/** 命中搜索：标签 / 角色名 / gid（聚类节点按聚类名） */
function matches(n: TreeNode, q: string): boolean {
  if (n.cluster) return n.cluster.label.toLowerCase().includes(q)
  const el = n.el!
  return (
    el.label.toLowerCase().includes(q) ||
    roleName(el.role).toLowerCase().includes(q) ||
    el.gid.toLowerCase().includes(q)
  )
}

/** 保留匹配节点与其祖先/后代的过滤树 */
function filterTree(nodes: TreeNode[], q: string): TreeNode[] {
  const out: TreeNode[] = []
  for (const n of nodes) {
    if (matches(n, q)) {
      out.push(n) // 自身命中：整棵子树保留
      continue
    }
    const kids = filterTree(n.children, q)
    if (kids.length) out.push({ ...n, children: kids })
  }
  return out
}

/** 只看某分支：该 gid 的祖先链 + 其整棵子树 */
function isolateTree(nodes: TreeNode[], gid: string): TreeNode[] {
  const out: TreeNode[] = []
  for (const n of nodes) {
    if (n.el?.gid === gid) {
      out.push(n)
      continue
    }
    const kids = isolateTree(n.children, gid)
    if (kids.length) out.push({ ...n, children: kids })
  }
  return out
}

const canHide = (el: ManifestElement) =>
  el.gid !== 'figure' && el.editable.some((f) => f.prop === 'visible')

export function ElementTree() {
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const selectedIds = useSelectionStore((s) => s.ids)
  const objects = useDocumentStore((s) => s.doc.objects)

  // 目标面板：正在图内编辑的优先，其次画布上选中的 可参数化面板
  const panel = useMemo(() => {
    const byId = (id: string | null) => {
      const o = id ? objects.find((x) => x.id === id) : undefined
      return o?.type === 'panel' && o.script ? o : null
    }
    return byId(elementPanelId) ?? byId(selectedIds.at(-1) ?? null)
  }, [objects, elementPanelId, selectedIds])

  const manifest = useRenderStore((s) =>
    panel ? s.byFile[panel.fileId]?.manifest : null,
  )
  const rendering = useRenderStore((s) =>
    panel ? s.byFile[panel.fileId]?.status === 'rendering' : false,
  )

  if (!panel) {
    return (
      <EmptyState
        icon={Braces}
        title="选中一个可参数化面板"
        hint="带 { } 标记的面板由脚本生成，这里会列出它的全部图内元素。"
      />
    )
  }

  if (!manifest) {
    return (
      <div className="flex flex-col items-start gap-2 px-3 py-2">
        <p className="text-xs leading-relaxed text-ink-3">
          「{panel.name ?? panel.fileId}」的元素清单需要引擎渲染一次。
        </p>
        {rendering ? (
          <p className="flex items-center gap-1.5 text-xs text-ink-2">
            <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-ink-faint" />
            正在构建图表…
          </p>
        ) : (
          <Button variant="outline" size="sm" onClick={() => enterElementEdit(panel.id)}>
            <Braces size={13} />
            加载元素清单
          </Button>
        )}
      </div>
    )
  }

  return <TreeView key={panel.id} panel={panel} manifest={manifest} />
}

function TreeView({ panel, manifest }: { panel: PanelObject; manifest: Manifest }) {
  const selectedGids = useUiStore((s) => s.selectedGids)
  const editing = useUiStore((s) => s.elementPanelId === panel.id)
  const [query, setQuery] = useState('')
  const [isolated, setIsolated] = useState<string | null>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const listRef = useRef<HTMLUListElement>(null)

  const tree = useMemo(() => buildTree(manifest), [manifest])
  const q = query.trim().toLowerCase()

  const shown = useMemo(() => {
    let nodes = tree
    if (isolated) nodes = isolateTree(nodes, isolated)
    if (q) nodes = filterTree(nodes, q)
    return nodes
  }, [tree, isolated, q])

  const isOpen = (n: TreeNode, key: string) => {
    // 搜索 / 只看分支时全部展开，否则命不中匹配项
    if (q || isolated) return open[key] ?? true
    // 默认只展开 Figure 与 Axes 一级：聚类和刻度组等更深层收起
    const role = n.el?.role
    return open[key] ?? (n.el?.gid === 'figure' || role === 'axes' || role === 'axes3d')
  }
  const rows = useMemo(
    () => flatten(shown, 0, '', isOpen, []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shown, open, q, isolated],
  )

  // 画布上点选元素后，树滚动到该行（但不抢焦点）
  const primaryGid = selectedGids.at(-1)
  useEffect(() => {
    if (!primaryGid) return
    listRef.current
      ?.querySelector(`[data-el="${CSS.escape(primaryGid)}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [primaryGid])

  const focusRow = (key: string) =>
    listRef.current?.querySelector<HTMLElement>(`[data-el="${CSS.escape(key)}"]`)?.focus()

  const moveFocus = (from: string, delta: number) => {
    const i = rows.findIndex((r) => r.key === from)
    const next = rows[i + delta]
    if (next) focusRow(next.key)
  }

  /** 点树选中元素：未在编辑态则先进入（选中与画布/属性页共用同一条通路） */
  const selectGid = (gid: string, additive: boolean) => {
    if (!editing) enterElementEdit(panel.id)
    const ui = useUiStore.getState()
    if (additive && gid !== 'figure') ui.toggleSelectedGid(gid)
    else ui.setSelectedGid(gid)
  }

  const focusKey = rows.find((r) => r.node.el?.gid === primaryGid)?.key ?? rows[0]?.key

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 px-3 pb-1.5">
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Escape') {
                if (query) setQuery('')
                else (e.target as HTMLInputElement).blur()
              }
              if (e.key === 'ArrowDown' && rows.length) {
                e.preventDefault()
                focusRow(rows[0].key)
              }
            }}
            placeholder="搜索名称 / 角色 / gid"
            aria-label="搜索图内元素"
            className={cn(
              'h-7 w-full rounded-sm border border-transparent bg-surface-2 pl-6.5 pr-6 text-xs',
              'text-ink placeholder:text-ink-faint outline-none transition-colors',
              'hover:border-border focus:border-accent focus:bg-surface',
            )}
          />
          {query && (
            <button
              onClick={() => setQuery('')}
              aria-label="清除搜索"
              className="absolute right-1 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-sm text-ink-3 hover:text-ink"
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      {isolated && (
        <div className="flex shrink-0 items-center gap-1.5 bg-accent-subtle px-3 py-1">
          <Crosshair size={11} className="shrink-0 text-accent" />
          <span className="min-w-0 flex-1 truncate text-xs text-accent">
            只看：{manifest.elements.find((e) => e.gid === isolated)?.label ?? isolated}
          </span>
          <button
            onClick={() => setIsolated(null)}
            className="shrink-0 text-xs text-accent underline-offset-2 hover:underline"
          >
            退出
          </button>
        </div>
      )}

      <ul
        ref={listRef}
        role="tree"
        aria-label="图内元素"
        className="min-h-0 flex-1 overflow-y-auto py-1"
      >
        {rows.length === 0 && (
          <li>
            <EmptyState icon={SearchX} title="没有匹配的元素" />
          </li>
        )}
        {rows.map(({ node, depth, key }) =>
          node.cluster ? (
            <ClusterRow
              key={key}
              rowKey={key}
              label={node.cluster.label}
              count={node.children.length}
              depth={depth}
              expanded={isOpen(node, key)}
              tabbable={focusKey === key}
              onToggle={() => setOpen((s) => ({ ...s, [key]: !isOpen(node, key) }))}
              onMoveFocus={(d) => moveFocus(key, d)}
            />
          ) : (
            <ElementRow
              key={key}
              rowKey={key}
              panel={panel}
              el={node.el!}
              depth={depth}
              selected={selectedGids.includes(node.el!.gid)}
              tabbable={focusKey === key}
              expanded={node.children.length ? isOpen(node, key) : undefined}
              onToggle={() => setOpen((s) => ({ ...s, [key]: !isOpen(node, key) }))}
              onSelect={(additive) => selectGid(node.el!.gid, additive)}
              onIsolate={() => setIsolated(node.el!.gid)}
              onMoveFocus={(d) => moveFocus(key, d)}
            />
          ),
        )}
      </ul>
    </div>
  )
}

/** 聚类标题行：只组织层级，不可选中 */
function ClusterRow({
  rowKey,
  label,
  count,
  depth,
  expanded,
  tabbable,
  onToggle,
  onMoveFocus,
}: {
  rowKey: string
  label: string
  count: number
  depth: number
  expanded: boolean
  tabbable: boolean
  onToggle: () => void
  onMoveFocus: (delta: number) => void
}) {
  return (
    <li
      role="treeitem"
      aria-expanded={expanded}
      aria-label={`${label}（${count} 个元素）`}
      tabIndex={tabbable ? 0 : -1}
      data-el={rowKey}
      style={{ paddingLeft: 8 + depth * 12 }}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault()
          e.stopPropagation()
          onMoveFocus(e.key === 'ArrowDown' ? 1 : -1)
        } else if ((e.key === 'ArrowRight' && !expanded) || (e.key === 'ArrowLeft' && expanded) || e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          onToggle()
        }
      }}
      onPointerDown={(e) => {
        if (e.button === 0) onToggle()
      }}
      className="flex h-7 cursor-default items-center gap-1 border-l-2 border-transparent pr-1.5 text-xs text-ink-2 outline-none hover:bg-ink/[.04] focus-visible:focus-ring"
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-3">
        <ChevronRight size={11} className={cn('transition-transform', expanded && 'rotate-90')} />
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className="shrink-0 font-mono text-xs text-ink-3">{count}</span>
    </li>
  )
}

function ElementRow({
  rowKey,
  panel,
  el,
  depth,
  selected,
  tabbable,
  expanded,
  onToggle,
  onSelect,
  onIsolate,
  onMoveFocus,
}: {
  rowKey: string
  panel: PanelObject
  el: ManifestElement
  depth: number
  selected: boolean
  tabbable: boolean
  /** undefined = 叶子节点，无展开箭头 */
  expanded?: boolean
  onToggle: () => void
  onSelect: (additive: boolean) => void
  onIsolate: () => void
  onMoveFocus: (delta: number) => void
}) {
  // override 未渲染回来前也要即时反馈，所以两处都查
  const hidden =
    panel.overrides.some(
      (o) => o.gid === el.gid && o.prop === 'visible' && o.value === false,
    ) || isElementHidden(el)
  const locked = panel.lockedGids?.includes(el.gid) ?? false
  const unsupported = UNSUPPORTED[el.role]
  const readonly = el.editable.length === 0

  return (
    <li
      role="treeitem"
      aria-selected={selected}
      aria-expanded={expanded}
      aria-label={`${el.label}（${roleName(el.role)}）${hidden ? '，已隐藏' : ''}${locked ? '，已锁定' : ''}`}
      tabIndex={tabbable ? 0 : -1}
      data-el={rowKey}
      style={{ paddingLeft: 8 + depth * 12 }}
      onFocus={(e) => {
        if (e.target !== e.currentTarget || selected) return
        // 焦点漫游即选中，与图层树一致
        onSelect(false)
      }}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault()
          e.stopPropagation()
          onMoveFocus(e.key === 'ArrowDown' ? 1 : -1)
        } else if (e.key === 'ArrowRight' && expanded === false) {
          e.preventDefault()
          e.stopPropagation()
          onToggle()
        } else if (e.key === 'ArrowLeft' && expanded === true) {
          e.preventDefault()
          e.stopPropagation()
          onToggle()
        } else if (e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          onSelect(e.shiftKey)
        } else if (e.key === 'Delete' || e.key === 'Backspace') {
          e.preventDefault()
          e.stopPropagation()
          if (canHide(el) && !hidden) hideElement(panel.id, el.gid, el.label)
        } else if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          useUiStore.getState().setSelectedGid(null)
          ;(e.currentTarget as HTMLElement).blur()
        }
      }}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        onSelect(e.shiftKey)
      }}
      className={cn(
        'group relative flex h-7 cursor-default items-center gap-1 border-l-2 pr-1 text-xs outline-none focus-visible:focus-ring',
        selected
          ? 'border-accent bg-accent-subtle text-accent'
          : 'border-transparent text-ink hover:bg-ink/[.04]',
        hidden && 'opacity-45',
      )}
    >
      {expanded !== undefined ? (
        <button
          onPointerDown={(e) => e.stopPropagation()}
          onClick={onToggle}
          aria-label={expanded ? '折叠' : '展开'}
          tabIndex={-1}
          className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-3 hover:text-ink"
        >
          <ChevronRight
            size={11}
            className={cn('transition-transform', expanded && 'rotate-90')}
          />
        </button>
      ) : (
        <span className="w-4 shrink-0" />
      )}

      <span className="min-w-0 flex-1 truncate" title={`${el.label} · ${roleName(el.role)} · ${el.gid}`}>
        {el.label}
      </span>

      {unsupported && (
        <Tip label={`${unsupported.title}暂不支持：${unsupported.reason}`} side="right">
          <TriangleAlert size={11} className="shrink-0 text-ink-3" />
        </Tip>
      )}
      {readonly && <span className="shrink-0 text-xs text-ink-3">只读</span>}

      {/* 锁定 / 隐藏状态常驻；动作本身收进 ⋯ 菜单 */}
      {locked && <Lock size={11} className="shrink-0 text-ink-3" aria-label="已锁定" />}
      {hidden && <EyeOff size={11} className="shrink-0 text-ink-3" aria-label="已隐藏" />}

      <span
        className={cn(
          'shrink-0',
          'opacity-0 group-focus-within:opacity-100 group-hover:opacity-100',
        )}
      >
        <Menu
          width={168}
          align="end"
          trigger={
            <Button
              size="icon-sm"
              className="h-7 w-6"
              tabIndex={-1}
              onPointerDown={(e) => e.stopPropagation()}
              aria-label={`${el.label} 的操作`}
            >
              <MoreHorizontal size={12} className="text-ink-3" />
            </Button>
          }
        >
          <MenuItem onSelect={onIsolate}>
            <span className="flex items-center gap-2">
              <Crosshair size={12} className="text-ink-3" />
              只看此分支
            </span>
          </MenuItem>
          {el.gid !== 'figure' && (
            <MenuItem onSelect={() => toggleElementLocked(panel.id, el.gid, el.label)}>
              <span className="flex items-center gap-2">
                {locked ? (
                  <LockOpen size={12} className="text-ink-3" />
                ) : (
                  <Lock size={12} className="text-ink-3" />
                )}
                {locked ? '解锁' : '锁定（画布点击跳过）'}
              </span>
            </MenuItem>
          )}
          {canHide(el) && (
            <MenuItem
              onSelect={() =>
                hidden ? unhideElement(panel.id, el.gid) : hideElement(panel.id, el.gid, el.label)
              }
            >
              <span className="flex items-center gap-2">
                {hidden ? (
                  <Eye size={12} className="text-ink-3" />
                ) : (
                  <EyeOff size={12} className="text-ink-3" />
                )}
                {hidden ? '恢复显示' : '隐藏（可恢复）'}
              </span>
            </MenuItem>
          )}
        </Menu>
      </span>
    </li>
  )
}
