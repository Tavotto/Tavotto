import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { Copy, MoreHorizontal, Pencil, Plus, Search, Trash2,
  SearchX,
} from 'lucide-react'
import {
  activateCanvas,
  createCanvasAndActivate,
  deleteCanvasWithSession,
} from '@/store/canvasSession'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { askConfirm, useUiStore } from '@/store/uiStore'
import type { CanvasData } from '@/types/document'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem, MenuSeparator } from '../ui/Menu'
import { TextInput } from '../ui/Input'

/**
 * 画布列表（项目里的全部画布，含未打开成标签的）。
 * 点击 = 打开成标签并切换；缩略图是对象布局示意（页面比例 + 对象框），
 * 不做真实渲染——识别用，不冒充成图。
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
        <div className="relative min-w-0 flex-1">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-faint" />
          <TextInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={cl('search')}
            aria-label={cl('searchAria')}
            className="w-full pl-6"
          />
        </div>
        <Button
          size="icon"
          aria-label={cl('newCanvas')}
          onClick={() => void createCanvasAndActivate()}
        >
          <Plus size={14} />
        </Button>
      </div>

      <ul aria-label={cl('listLabel')} className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {rows.map((c, i) => (
          <CanvasRow
            key={c.id}
            canvas={c}
            index={i}
            active={c.id === activeId}
            filtered={!!query.trim()}
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
            <EmptyState icon={SearchX} title={cl('noMatch', { query })} />
          </li>
        )}
      </ul>
    </div>
  )
}

function CanvasRow({
  canvas,
  index,
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
      className={cn(
        'group relative flex items-center gap-2 rounded-sm px-1.5 py-1.5',
        active ? 'bg-accent-subtle' : 'hover:bg-ink/[.035]',
      )}
    >
      {active && (
        <span aria-hidden className="absolute -left-0.5 top-2 h-8 w-0.5 rounded-full bg-accent" />
      )}
      <SchemaThumb canvas={canvas} />
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
            className="h-5 w-full rounded-sm border border-accent bg-surface px-1 text-xs text-ink outline-none"
          />
        ) : (
          <>
            <span className={cn('block truncate text-xs', active ? 'font-medium text-ink' : 'text-ink-2')}>
              {canvas.name}
            </span>
            <span className="block text-xs text-ink-3">
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
          <Button
            size="icon-sm"
            aria-label={cl('rowActions', { name: canvas.name })}
            className="opacity-0 focus-visible:opacity-100 group-hover:opacity-100"
          >
            <MoreHorizontal size={13} className="text-ink-3" />
          </Button>
        }
      >
        <MenuItem onSelect={onRenameStart}>
          <span className="flex items-center gap-2">
            <Pencil size={13} className="text-ink-3" />
            {cl('rename')}
          </span>
        </MenuItem>
        <MenuItem
          onSelect={() => {
            const nid = useDocumentStore.getState().duplicateCanvas(canvas.id)
            if (nid) activateCanvas(nid, { open: true })
          }}
        >
          <span className="flex items-center gap-2">
            <Copy size={13} className="text-ink-3" />
            {cl('duplicate')}
          </span>
        </MenuItem>
        <MenuSeparator />
        <MenuItem danger onSelect={() => void remove()}>
          <span className="flex items-center gap-2">
            <Trash2 size={13} />
            {cl('delete')}
          </span>
        </MenuItem>
      </Menu>
    </li>
  )
}

/** 布局示意缩略图：页面比例 + 对象包围盒（识别用途，非真实渲染） */
function SchemaThumb({ canvas }: { canvas: CanvasData }) {
  const { w, h } = canvas.page
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      aria-hidden
      className="h-10 w-14 shrink-0 rounded-[3px] border border-border bg-white"
      preserveAspectRatio="xMidYMid meet"
    >
      {canvas.objects
        .filter((o) => !o.hidden)
        .slice(0, 40)
        .map((o) => (
          <rect
            key={o.id}
            x={o.x}
            y={o.y}
            width={Math.max(o.w, w / 60)}
            height={Math.max(o.h, h / 60)}
            fill="currentColor"
            className={o.type === 'panel' ? 'text-ink/25' : 'text-ink/12'}
          />
        ))}
    </svg>
  )
}
