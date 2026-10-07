import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { ArrowDown, ArrowUp, Copy, Pencil, Plus, SearchX, Trash2 } from '@/components/ui/icons'
import { FIELD_BOX, FIELD_FOCUS } from '@/components/ui/fieldBox'
import { ICON_SIZE } from '@/components/ui/Icon'
import { dropLineClass, listRowClass, rowMetaClass } from '@/components/ui/listRow'
import {
  activateCanvas,
  createCanvasAndActivate,
  deleteCanvasWithSession,
} from '@/store/canvasSession'
import { ALT, cn, combo } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { askConfirm, useUiStore } from '@/store/uiStore'
import type { CanvasData } from '@/types/document'
import { IconButton } from '../ui/Button'
import { CanvasThumb } from '../CanvasThumb'
import { EmptyState } from '../ui/EmptyState'
import { MenuItem, MenuSeparator } from '../ui/Menu'
import { RowMenu } from '../ui/RowMenu'
import { SearchInput } from '../ui/SearchInput'
import { useRowMenu } from '../ui/useRowMenu'
import { DrawerCount } from './DrawerCount'
import { DrawerHeaderActions, DrawerTitleMeta } from './DrawerHeader'
import { useRovingList } from './rovingList'

/**
 * 画布列表（项目里的全部画布，含未打开成标签的）。
 * 点击 = 打开成标签并切换；缩略图按对象落位画真实内容（面板用现成的预览图，
 * 文字画文字，标注画轮廓）——三张不同的图仅凭缩略图就分得开（审计 T05）。
 * 缩略图本身在 `components/CanvasThumb.tsx`：版本列表里的每一行画的是同一份
 * 组件（喂给它的是后端草图），不许另画一份。
 */
/** 本组文案在 workspace:canvasList.* 下 */
const cl = (key: string, values?: Record<string, unknown>) =>
  translate(`canvasList.${key}`, { ns: 'workspace', ...(values ?? {}) })

export function CanvasList() {
  useTranslation('workspace')
  const canvases = useDocumentStore((s) => s.canvases)
  const activeId = useDocumentStore((s) => s.activeCanvasId)
  const activeDoc = useDocumentStore((s) => s.doc)
  const [query, setQuery] = useState('')
  const [renaming, setRenaming] = useState<string | null>(null)
  const dragFrom = useRef<number | null>(null)
  const roving = useRovingList<HTMLUListElement>()

  // 激活画布的内容以 doc 为准（canvases 里是最后同步的快照）
  const rows = useMemo(() => {
    const list = canvases.map((c) =>
      c.id === activeId
        ? { ...c, name: activeDoc.name, page: activeDoc.page, objects: activeDoc.objects }
        : c,
    )
    const q = query.trim().toLowerCase()
    return q ? list.filter((c) => c.name.toLowerCase().includes(q)) : list
  }, [canvases, activeId, activeDoc, query])

  const open = (id: string) => activateCanvas(id, { open: true })

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 计数进标题、「+」进标题行的动作槽，搜索行只放搜索（2026-10-07 设计审计 §10.3） */}
      <DrawerTitleMeta>
        <DrawerCount value={canvases.length} label={cl('count', { count: canvases.length })} />
      </DrawerTitleMeta>
      <DrawerHeaderActions>
        {/* 图标钮走 IconButton：名字与气泡同一份（宪法第四、五节；左栏审计 L14） */}
        <IconButton label={cl('newCanvas')} data-canvas-new onClick={() => void createCanvasAndActivate()}>
          <Plus size={ICON_SIZE.md} />
        </IconButton>
      </DrawerHeaderActions>
      <div className="flex shrink-0 items-center px-3 pb-2">
        <SearchInput
          value={query}
          onValueChange={setQuery}
          placeholder={cl('search')}
          aria-label={cl('searchAria')}
        />
      </div>

      {/* 行自己带 `mx-1`（listRowClass），列表不再另加左右内边距：缩略图落在 56 那条竖线上。
          一列一个 Tab 停靠点（↑↓ 走行，Enter 打开，F2 改名，⌥↑↓ 排序，⇧F10 菜单） */}
      <ul
        ref={roving.ref}
        onFocus={roving.onFocus}
        onKeyDown={roving.onKeyDown}
        aria-label={cl('listLabel')}
        className="min-h-0 flex-1 overflow-y-auto pb-2"
      >
        {rows.map((c, i) => (
          <CanvasRow
            key={c.id}
            canvas={c}
            index={i}
            active={c.id === activeId}
            filtered={!!query.trim()}
            count={rows.length}
            renaming={renaming === c.id}
            onOpen={() => open(c.id)}
            onRenameStart={() => setRenaming(c.id)}
            onRenamed={(name) => {
              setRenaming(null)
              if (name) useDocumentStore.getState().renameCanvas(c.id, name)
            }}
            dragFrom={dragFrom}
            onMove={(delta) => useDocumentStore.getState().reorderCanvases(i, i + delta)}
          />
        ))}
        {rows.length === 0 && (
          <li>
            <EmptyState icon={SearchX} title={cl('noMatch')} />
          </li>
        )}
      </ul>
    </div>
  )
}

function CanvasRow({
  canvas,
  index,
  count,
  active,
  filtered,
  renaming,
  onOpen,
  onRenameStart,
  onRenamed,
  dragFrom,
  onMove,
}: {
  canvas: CanvasData
  index: number
  /** 可见行数：上移 / 下移到头就禁用 */
  count: number
  active: boolean
  /** 搜索过滤中禁用拖动重排（索引对不上真实顺序） */
  filtered: boolean
  renaming: boolean
  onOpen: () => void
  onRenameStart: () => void
  onRenamed: (name: string | null) => void
  dragFrom: React.RefObject<number | null>
  /** 上移 / 下移一格（菜单与 ⌥↑↓ 同一个动作；过滤中禁用） */
  onMove: (delta: -1 | 1) => void
}) {
  useTranslation('workspace')
  const menu = useRowMenu()
  const [drop, setDrop] = useState<'before' | 'after' | null>(null)
  const canMove = (delta: -1 | 1) => !filtered && (delta < 0 ? index > 0 : index < count - 1)
  const openRef = useRef<HTMLButtonElement>(null)
  // Enter / Esc 结束改名后焦点回到这一行的按钮；失焦提交（点了别处）不抢焦点
  const refocus = useRef(false)
  useEffect(() => {
    if (!renaming && refocus.current) {
      refocus.current = false
      openRef.current?.focus()
    }
  }, [renaming])
  const finishRename = (name: string | null, viaKey: boolean) => {
    refocus.current = viaKey
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
    useUiStore
      .getState()
      .setStatus(msg('canvasList.deleted', { name: canvas.name }, 'workspace'), 'done')
  }

  return (
    <li
      {...menu.rowProps}
      onKeyDown={(e) => {
        menu.rowProps.onKeyDown?.(e)
        if (e.defaultPrevented || renaming || !e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return
        const delta = e.key === 'ArrowUp' ? -1 : 1
        if (!canMove(delta)) return
        e.preventDefault()
        onMove(delta)
      }}
      data-canvas-row
      draggable={!filtered && !renaming}
      onDragStart={() => {
        dragFrom.current = index
      }}
      // 拖动取消 / 松在列表外时 onDrop 不会来：不清的话起点一直挂着，之后任何外部拖进来
      // 落在画布行上的东西（访达里的文件、别处的文字）都会被当成「挪那一张」——而且挪的是
      // 当时那个位置上此刻的画布（与工作区抽屉的收藏行同一个缺陷，Codex #550）
      onDragEnd={() => {
        dragFrom.current = null
        setDrop(null)
      }}
      // 只接本列表内部发起的重排：起点为空 = 外部拖进来的，不 preventDefault（不接收）
      onDragOver={(e) => {
        const from = dragFrom.current
        if (from == null || from === index) return
        e.preventDefault()
        // 落点线（2px accent + 圆点）：落下之后它占这一行，从上面拖下来画在下缘
        setDrop(from < index ? 'after' : 'before')
      }}
      onDragLeave={() => setDrop(null)}
      onDrop={() => {
        if (!filtered && dragFrom.current != null && dragFrom.current !== index) {
          useDocumentStore.getState().reorderCanvases(dragFrom.current, index)
        }
        dragFrom.current = null
        setDrop(null)
      }}
      // 与树行 / 列表行同一副外观（hover / selected / mx-1），带缩略图的行是 lg 52（审计 §10.3）
      className={cn(listRowClass({ size: 'lg', selected: active }), 'gap-2 pl-2 pr-1')}
    >
      <span aria-hidden className={dropLineClass(drop)} />
      <CanvasThumb page={canvas.page} objects={canvas.objects} />
      {/* 改名框与「打开」按钮是兄弟、不是父子：此前框嵌在 <button> 里（嵌套交互控件，
          读屏与键盘都乱）。改名时整颗按钮让位给框，结束后焦点回到按钮 */}
      {renaming ? (
        <RenameInput initial={canvas.name} onDone={finishRename} />
      ) : (
        <button
          ref={openRef}
          data-roving
          data-canvas-open
          onClick={onOpen}
          onDoubleClick={onRenameStart}
          onKeyDown={(e) => {
            // F2 = 改当前行的名字（与访达 / 资源管理器同一个键）
            if (e.key === 'F2') {
              e.preventDefault()
              onRenameStart()
            }
          }}
          className="min-w-0 flex-1 self-stretch text-left outline-none focus-visible:focus-ring"
          aria-label={cl('openCanvas', { name: canvas.name })}
          aria-current={active || undefined}
        >
          {/* 画布名是主文字：12 / ink（选中时行自己加粗），与树行 / 卡名同一档（L02 / L32）；
              元数据 11 / ink-3，选中时不跟着行加粗 */}
          <span className="block truncate text-sm text-ink">{canvas.name}</span>
          <span className={cn('block', rowMetaClass(active))}>
            {cl('meta', {
              w: canvas.page.w,
              h: canvas.page.h,
              count: canvas.objects.length,
            })}
          </span>
        </button>
      )}
      {/* 一份清单三个入口：⋯ / 右键 / ⇧F10（`ui/RowMenu`）；行有焦点时 ⋯ 进 Tab 顺序 */}
      <RowMenu state={menu} label={cl('rowActions', { name: canvas.name })} width={180} data-canvas-menu>
        <MenuItem onSelect={onRenameStart} icon={Pencil} shortcut="F2">
          {cl('rename')}
        </MenuItem>
        {/* 拖动重排只有鼠标能用：菜单里给键盘一条同样的路（搜索过滤中索引对不上，禁用） */}
        <MenuItem icon={ArrowUp} disabled={!canMove(-1)} shortcut={combo(ALT, '↑')} onSelect={() => onMove(-1)}>
          {cl('moveUp')}
        </MenuItem>
        <MenuItem icon={ArrowDown} disabled={!canMove(1)} shortcut={combo(ALT, '↓')} onSelect={() => onMove(1)}>
          {cl('moveDown')}
        </MenuItem>
        <MenuItem
          icon={Copy}
          onSelect={() => {
            const nid = useDocumentStore.getState().duplicateCanvas(canvas.id)
            if (nid) activateCanvas(nid, { open: true })
          }}
        >
          {cl('duplicate')}
        </MenuItem>
        <MenuSeparator />
        <MenuItem danger onSelect={() => void remove()} icon={Trash2}>
          {cl('delete')}
        </MenuItem>
      </RowMenu>
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
      aria-label={cl('canvasName')}
      onChange={(e) => setDraft(e.target.value)}
      onFocus={(e) => e.currentTarget.select()}
      onBlur={() => finish(draft.trim() || null, false)}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter') finish(draft.trim() || null, true)
        if (e.key === 'Escape') finish(null, true)
      }}
      // 行内改名框也是「可编辑框」那一副（fieldBox），28 高；此前三处行内改名框三种
      // 高度 / 圆角（左栏审计 L31）
      className={cn('h-7 min-w-0 flex-1 px-1.5 outline-none', FIELD_BOX, FIELD_FOCUS)}
    />
  )
}
