import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import {
  ArrowDown,
  ArrowUp,
  Copy,
  Ellipsis,
  Pencil,
  Plus,
  SearchX,
  Trash2,
} from '@/components/ui/icons'
import { FIELD_BOX, FIELD_FOCUS } from '@/components/ui/fieldBox'
import { ICON_SIZE } from '@/components/ui/Icon'
import { listRowClass } from '@/components/ui/listRow'
import {
  activateCanvas,
  createCanvasAndActivate,
  deleteCanvasWithSession,
} from '@/store/canvasSession'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { askConfirm, useUiStore } from '@/store/uiStore'
import type { CanvasData } from '@/types/document'
import { IconButton } from '../ui/Button'
import { CanvasThumb } from '../CanvasThumb'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem, MenuSeparator } from '../ui/Menu'
import { SearchInput } from '../ui/SearchInput'

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
      <div className="flex shrink-0 items-center gap-1.5 px-3 pb-2">
        <SearchInput
          value={query}
          onValueChange={setQuery}
          placeholder={cl('search')}
          aria-label={cl('searchAria')}
        />
        {/* 图标钮走 IconButton：名字与气泡同一份（宪法第四、五节；左栏审计 L14） */}
        <IconButton label={cl('newCanvas')} onClick={() => void createCanvasAndActivate()}>
          <Plus size={ICON_SIZE.md} />
        </IconButton>
      </div>

      {/* 行自己带 `mx-1`（listRowClass），列表不再另加左右内边距：缩略图落在 56 那条竖线上 */}
      <ul aria-label={cl('listLabel')} className="min-h-0 flex-1 overflow-y-auto pb-2">
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
}) {
  useTranslation('workspace')
  const [draft, setDraft] = useState(canvas.name)

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
      .setStatus(msg('canvasList.deleted', { name: canvas.name }, 'workspace'))
  }

  return (
    <li
      draggable={!filtered && !renaming}
      onDragStart={() => {
        dragFrom.current = index
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={() => {
        if (!filtered && dragFrom.current != null && dragFrom.current !== index) {
          useDocumentStore.getState().reorderCanvases(dragFrom.current, index)
        }
        dragFrom.current = null
      }}
      // 与树行 / 列表行同一副外观（hover / selected / mx-1）；高度是例外——缩略图 40 撑到 52
      // （左栏审计 L32）
      className={cn(listRowClass({ selected: active }), 'h-auto gap-2 px-2 py-1.5')}
    >
      <CanvasThumb page={canvas.page} objects={canvas.objects} />
      <button
        onClick={onOpen}
        onDoubleClick={onRenameStart}
        className="min-w-0 flex-1 text-left outline-none focus-visible:focus-ring"
        aria-label={cl('openCanvas', { name: canvas.name })}
        aria-current={active || undefined}
      >
        {renaming ? (
          <input
            autoFocus
            value={draft}
            aria-label={cl('canvasName')}
            onChange={(e) => setDraft(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={() => onRenamed(draft.trim() || null)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
              if (e.key === 'Escape') onRenamed(null)
            }}
            // 行内改名框也是「可编辑框」那一副（fieldBox），28 高；此前三处行内改名框三种
            // 高度 / 圆角（左栏审计 L31）
            className={cn('h-7 w-full px-1.5 outline-none', FIELD_BOX, FIELD_FOCUS)}
          />
        ) : (
          <>
            {/* 画布名是主文字：12 / ink（选中时行自己加粗），与树行 / 卡名同一档（L02 / L32）；
                元数据 11 / ink-3，选中时不跟着行加粗 */}
            <span className="block truncate text-sm text-ink">{canvas.name}</span>
            <span className="block text-xs font-normal text-ink-3">
              {cl('meta', {
                w: canvas.page.w,
                h: canvas.page.h,
                count: canvas.objects.length,
              })}
            </span>
          </>
        )}
      </button>
      <Menu
        width={148}
        align="end"
        trigger={
          <IconButton
            iconSize="sm"
            label={cl('rowActions', { name: canvas.name })}
            className="opacity-0 focus-visible:opacity-100 group-hover:opacity-100"
          >
            <Ellipsis size={ICON_SIZE.sm} className="text-ink-3" />
          </IconButton>
        }
      >
        <MenuItem onSelect={onRenameStart}>
          <span className="flex items-center gap-2">
            <Pencil size={ICON_SIZE.sm} className="text-ink-3" />
            {cl('rename')}
          </span>
        </MenuItem>
        {/* 拖动重排只有鼠标能用：菜单里给键盘一条同样的路（搜索过滤中索引对不上，禁用） */}
        <MenuItem
          disabled={filtered || index === 0}
          onSelect={() => useDocumentStore.getState().reorderCanvases(index, index - 1)}
        >
          <span className="flex items-center gap-2">
            <ArrowUp size={ICON_SIZE.sm} className="text-ink-3" />
            {cl('moveUp')}
          </span>
        </MenuItem>
        <MenuItem
          disabled={filtered || index >= count - 1}
          onSelect={() => useDocumentStore.getState().reorderCanvases(index, index + 1)}
        >
          <span className="flex items-center gap-2">
            <ArrowDown size={ICON_SIZE.sm} className="text-ink-3" />
            {cl('moveDown')}
          </span>
        </MenuItem>
        <MenuItem
          onSelect={() => {
            const nid = useDocumentStore.getState().duplicateCanvas(canvas.id)
            if (nid) activateCanvas(nid, { open: true })
          }}
        >
          <span className="flex items-center gap-2">
            <Copy size={ICON_SIZE.sm} className="text-ink-3" />
            {cl('duplicate')}
          </span>
        </MenuItem>
        <MenuSeparator />
        <MenuItem danger onSelect={() => void remove()}>
          <span className="flex items-center gap-2">
            <Trash2 size={ICON_SIZE.sm} />
            {cl('delete')}
          </span>
        </MenuItem>
      </Menu>
    </li>
  )
}
