import { Fragment, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { ArrowDown, ArrowUp, Copy, Pencil, Plus, Trash2 } from '@/components/ui/icons'
import { FIELD_BOX, FIELD_FOCUS } from '@/components/ui/fieldBox'
import { ICON_SIZE } from '@/components/ui/Icon'
import { dropLineClass, listRowClass } from '@/components/ui/listRow'
import { TreeChevron, treeIndent } from '@/components/ui/TreeRow'
import { useRenameFocusReturn } from '@/components/ui/useRenameFocusReturn'
import { activateCanvas, createCanvasAndActivate, deleteCanvasWithSession } from '@/store/canvasSession'
import { ALT, cn, combo } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { askConfirm, useUiStore } from '@/store/uiStore'
import type { CanvasData } from '@/types/document'
import { IconButton } from '../ui/Button'
import { MenuItem, MenuSeparator } from '../ui/Menu'
import { RowMenu } from '../ui/RowMenu'
import { useRowMenu } from '../ui/useRowMenu'
import { DrawerHeaderActions } from './DrawerHeader'
import { LayerTree } from './LayerTree'

/**
 * 大纲（2026-10 界面审计 A3）：画布 → 图，一棵树。图内元素是左轨独立的「图内」抽屉（用户验收后裁定，与 main 一致）。
 *
 * 此前是左轨上三个并列的页签（画布 / 图层 / 图内），用户得先想「我要找的东西算哪一层」再去点对应页签。
 * 现在画布与图层挂在同一棵树上：顶层是项目里的全部画布（含没开成标签的）；当前画布展开，里面是 `LayerTree`（图与标注，含分组 / 锁定 / 隐藏）。
 *
 * 选中的同步不在这里做：两层的行都读写同一份 `selectionStore`，
 * 点大纲里的行与点画布上的图本来就是同一个动作。
 * 图层子树保留原来的 `data-tree="layers"` 钩子，画布行保留 `data-canvas-*`。
 */
const ol = (key: string, values?: Record<string, unknown>) =>
  translate(`canvasList.${key}`, { ns: 'workspace', ...(values ?? {}) })

/** 画布层级：顶层 0；图 1（组成员的图再深一层，由 `LayerTree` 自己加） */
const LAYER_BASE = 1

export function OutlineTree() {
  useTranslation('workspace')
  const canvases = useDocumentStore((s) => s.canvases)
  const activeId = useDocumentStore((s) => s.activeCanvasId)
  const activeDoc = useDocumentStore((s) => s.doc)
  const [renaming, setRenaming] = useState<string | null>(null)
  // 用户亲手折叠 / 展开过当前画布；没表过态时当前画布展开
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const dragFrom = useRef<number | null>(null)
  const listRef = useRef<HTMLUListElement>(null)

  // 激活画布的内容以 doc 为准（canvases 里是最后同步的快照）
  const rows = useMemo(
    () =>
      canvases.map((c) =>
        c.id === activeId ? { ...c, name: activeDoc.name, page: activeDoc.page, objects: activeDoc.objects } : c,
      ),
    [canvases, activeId, activeDoc],
  )

  const focusCanvasRow = (id: string) =>
    listRef.current?.querySelector<HTMLElement>(`[data-canvas-row="${CSS.escape(id)}"]`)?.focus()

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <DrawerHeaderActions>
        <IconButton label={ol('newCanvas')} data-canvas-new onClick={() => void createCanvasAndActivate()}>
          <Plus size={ICON_SIZE.md} />
        </IconButton>
      </DrawerHeaderActions>
      <ul
        ref={listRef}
        role="tree"
        data-tree="outline"
        aria-label={ol('listLabel')}
        className="min-h-0 flex-1 scroll-py-1 overflow-y-auto py-1"
      >
        {rows.map((c, i) => {
          const active = c.id === activeId
          const open = active && (expanded[c.id] ?? true)
          return (
            <Fragment key={c.id}>
              <CanvasRow
                canvas={c}
                index={i}
                count={rows.length}
                active={active}
                expanded={open}
                renaming={renaming === c.id}
                onRenameStart={() => setRenaming(c.id)}
                onRenamed={(name) => {
                  setRenaming(null)
                  if (name) useDocumentStore.getState().renameCanvas(c.id, name)
                }}
                onToggle={() => {
                  // 大纲只展开当前画布：展开别的画布 = 打开它
                  if (!active) activateCanvas(c.id, { open: true })
                  else setExpanded((s) => ({ ...s, [c.id]: !open }))
                }}
                onNavKey={(key) => {
                  const to = key === 'ArrowDown' ? rows[i + 1] : rows[i - 1]
                  // 展开着的当前画布：↓ 先进它的第一张图
                  const firstLayer =
                    key === 'ArrowDown' && open
                      ? listRef.current?.querySelector<HTMLElement>('[data-tree="layers"] [data-layer]')
                      : null
                  if (firstLayer) firstLayer.focus()
                  else if (to) focusCanvasRow(to.id)
                }}
                dragFrom={dragFrom}
                onMove={(delta) => useDocumentStore.getState().reorderCanvases(i, i + delta)}
              />
              {open && (
                <LayerTree
                  embedded
                  baseDepth={LAYER_BASE}
                  onEdge={(edge) => {
                    if (edge === 'down') {
                      if (rows[i + 1]) focusCanvasRow(rows[i + 1].id)
                    } else focusCanvasRow(c.id)
                  }}
                />
              )}
            </Fragment>
          )
        })}
      </ul>
    </div>
  )
}

function CanvasRow({
  canvas,
  index,
  count,
  active,
  expanded,
  renaming,
  onRenameStart,
  onRenamed,
  onToggle,
  onNavKey,
  dragFrom,
  onMove,
}: {
  canvas: CanvasData
  index: number
  /** 画布个数：上移 / 下移到头就禁用 */
  count: number
  active: boolean
  expanded: boolean
  renaming: boolean
  onRenameStart: () => void
  onRenamed: (name: string | null) => void
  onToggle: () => void
  onNavKey: (key: 'ArrowDown' | 'ArrowUp') => void
  dragFrom: React.RefObject<number | null>
  /** 上移 / 下移一格（菜单与 ⌥↑↓ 同一个动作） */
  onMove: (delta: -1 | 1) => void
}) {
  useTranslation('workspace')
  const menu = useRowMenu({ enabled: !renaming })
  const [drop, setDrop] = useState<'before' | 'after' | null>(null)
  const canMove = (delta: -1 | 1) => (delta < 0 ? index > 0 : index < count - 1)
  const rowRef = useRef<HTMLLIElement>(null)
  // Enter / Esc 结束改名后焦点回到这一行；失焦提交（点了别处）不抢焦点（判据见 `ui/useRenameFocusReturn`）
  const markKeyFinish = useRenameFocusReturn(renaming, rowRef)
  const finishRename = (name: string | null, viaKey: boolean) => {
    if (viaKey) markKeyFinish()
    onRenamed(name)
  }

  const remove = async () => {
    const s = useDocumentStore.getState()
    if (s.canvases.length <= 1) {
      useUiStore.getState().setStatus(msg('canvasList.keepOne', undefined, 'workspace'), 'error')
      return
    }
    const ok = await askConfirm({
      title: msg('canvasList.deleteTitle', { name: canvas.name }, 'workspace'),
      body: msg('canvasList.deleteBody', { count: canvas.objects.length }, 'workspace'),
      confirmLabel: msg('canvasList.deleteConfirm', undefined, 'workspace'),
      danger: true,
    })
    if (!ok) return
    deleteCanvasWithSession(canvas.id)
    useUiStore.getState().setStatus(msg('canvasList.deleted', { name: canvas.name }, 'workspace'), 'done')
  }

  return (
    <li
      ref={rowRef}
      {...menu.rowProps}
      role="treeitem"
      aria-level={1}
      aria-posinset={index + 1}
      aria-setsize={count}
      aria-expanded={expanded}
      aria-selected={active}
      aria-current={active || undefined}
      aria-label={ol('openCanvas', { name: canvas.name })}
      tabIndex={active ? 0 : -1}
      data-canvas-row={canvas.id}
      data-canvas-open
      draggable={!renaming}
      onClick={(e) => {
        // ⋯ 菜单（portal，React 事件仍会冒上来）、行内按钮与改名框里的点击不算「打开这一张」
        if (renaming || !e.currentTarget.contains(e.target as Node) || (e.target as HTMLElement).closest('button, input')) return
        if (!active) activateCanvas(canvas.id, { open: true })
      }}
      onDoubleClick={onRenameStart}
      onKeyDown={(e) => {
        menu.rowProps.onKeyDown?.(e)
        if (e.defaultPrevented || renaming || e.target !== e.currentTarget) return
        if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
          const delta = e.key === 'ArrowUp' ? -1 : 1
          if (!canMove(delta)) return
          e.preventDefault()
          e.stopPropagation()
          onMove(delta)
        } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault()
          e.stopPropagation()
          onNavKey(e.key)
        } else if (e.key === 'ArrowRight' && !expanded) {
          e.preventDefault()
          onToggle()
        } else if (e.key === 'ArrowLeft' && expanded) {
          e.preventDefault()
          onToggle()
        } else if (e.key === 'Enter') {
          e.preventDefault()
          if (!active) activateCanvas(canvas.id, { open: true })
        } else if (e.key === 'F2') {
          // F2 = 改当前行的名字（与访达 / 资源管理器同一个键）
          e.preventDefault()
          onRenameStart()
        }
      }}
      onDragStart={() => {
        dragFrom.current = index
      }}
      // 拖动取消 / 松在列表外时 onDrop 不会来：不清的话起点一直挂着，之后任何外部拖进来
      // 落在画布行上的东西（访达里的文件、别处的文字）都会被当成「挪那一张」
      onDragEnd={() => {
        dragFrom.current = null
        setDrop(null)
      }}
      // 只接本列表内部发起的重排：起点为空 = 外部拖进来的，不 preventDefault（不接收）
      onDragOver={(e) => {
        const from = dragFrom.current
        if (from == null || from === index) return
        e.preventDefault()
        setDrop(from < index ? 'after' : 'before')
      }}
      onDragLeave={() => setDrop(null)}
      onDrop={() => {
        if (dragFrom.current != null && dragFrom.current !== index) {
          useDocumentStore.getState().reorderCanvases(dragFrom.current, index)
        }
        dragFrom.current = null
        setDrop(null)
      }}
      style={treeIndent(0)}
      // 当前画布是「分组头」那一档（500 / ink），其余画布是叶子（ink-2）；选中底留给图与元素行
      className={cn(listRowClass({ tree: true, group: active }), 'pr-0.5')}
    >
      <span aria-hidden className={dropLineClass(drop)} />
      <TreeChevron expanded={expanded} onToggle={onToggle} label={ol(expanded ? 'collapseCanvas' : 'expandCanvas')} />
      {renaming ? (
        <RenameInput initial={canvas.name} onDone={finishRename} />
      ) : (
        <>
          <span data-tree-label className="min-w-0 flex-1 truncate">
            {canvas.name}
          </span>
          <span className="shrink-0 type-meta font-normal tabular-nums">
            {canvas.page.w}×{canvas.page.h}
          </span>
        </>
      )}
      {/* 一份清单三个入口：⋯ / 右键 / ⇧F10（`ui/RowMenu`）；行有焦点时 ⋯ 进 Tab 顺序。外面这层挡住按下：开菜单不改选区 */}
      <span className="flex shrink-0" onPointerDown={(e) => e.stopPropagation()}>
        <RowMenu state={menu} label={ol('rowActions', { name: canvas.name })} width={180} data-canvas-menu>
          <MenuItem onSelect={onRenameStart} icon={Pencil} shortcut="F2">
            {ol('rename')}
          </MenuItem>
          {/* 拖动重排只有鼠标能用：菜单里给键盘一条同样的路 */}
          <MenuItem icon={ArrowUp} disabled={!canMove(-1)} shortcut={combo(ALT, '↑')} onSelect={() => onMove(-1)}>
            {ol('moveUp')}
          </MenuItem>
          <MenuItem icon={ArrowDown} disabled={!canMove(1)} shortcut={combo(ALT, '↓')} onSelect={() => onMove(1)}>
            {ol('moveDown')}
          </MenuItem>
          <MenuItem
            icon={Copy}
            onSelect={() => {
              const nid = useDocumentStore.getState().duplicateCanvas(canvas.id)
              if (nid) activateCanvas(nid, { open: true })
            }}
          >
            {ol('duplicate')}
          </MenuItem>
          <MenuSeparator />
          <MenuItem danger onSelect={() => void remove()} icon={Trash2}>
            {ol('delete')}
          </MenuItem>
        </RowMenu>
      </span>
    </li>
  )
}

/**
 * 行内改名框。每次进入改名都重新挂载，草稿从**此刻**的名字起步——此前草稿只在行挂载时
 * 取一次，Esc 放弃后再改名，框里还是上次没提交的草稿。
 */
function RenameInput({
  initial,
  onDone,
}: {
  initial: string
  onDone: (name: string | null, viaKey: boolean) => void
}) {
  const [draft, setDraft] = useState(initial)
  // Esc / Enter 之后框被卸掉，浏览器可能再补一次 blur：只认第一次结束
  const done = useRef(false)
  const finish = (name: string | null, viaKey: boolean) => {
    if (done.current) return
    done.current = true
    onDone(name, viaKey)
  }
  return (
    <input
      autoFocus
      data-canvas-rename
      value={draft}
      aria-label={ol('canvasName')}
      onChange={(e) => setDraft(e.target.value)}
      onFocus={(e) => e.currentTarget.select()}
      onBlur={() => finish(draft.trim() || null, false)}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter') finish(draft.trim() || null, true)
        if (e.key === 'Escape') finish(null, true)
      }}
      // 行内改名框是「可编辑框」那一副（fieldBox），28 高 = 整行
      className={cn('h-7 min-w-0 flex-1 px-1.5 outline-none', FIELD_BOX, FIELD_FOCUS)}
    />
  )
}
