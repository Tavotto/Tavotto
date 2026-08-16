import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Plus, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { activateCanvas, createCanvasAndActivate } from '@/store/canvasSession'
import { useDocumentStore } from '@/store/documentStore'
import { Button } from './ui/Button'
import { Menu, MenuItem, MenuSeparator } from './ui/Menu'
import { Tip } from './ui/Tooltip'

/**
 * Canvas 标签行：Tab = 打开的画布（关标签不删画布，全部画布见左栏「画布」）。
 * 单击切换、双击重命名、拖动重排、× 关闭；激活画布切换后视口自动 fit。
 */
export function CanvasTabs() {
  const openTabs = useDocumentStore((s) => s.openTabs)
  const activeId = useDocumentStore((s) => s.activeCanvasId)
  const canvases = useDocumentStore((s) => s.canvases)
  const activeName = useDocumentStore((s) => s.doc.name)
  const dirty = useDocumentStore((s) => s.dirty)
  const [renaming, setRenaming] = useState<string | null>(null)
  const dragFrom = useRef<number | null>(null)

  const nameOf = (id: string) =>
    id === activeId ? activeName : (canvases.find((c) => c.id === id)?.name ?? '')

  const activate = (id: string) => activateCanvas(id)

  return (
    <div
      role="tablist"
      aria-label="画布标签"
      className="flex h-8 shrink-0 items-center gap-0.5 border-b border-border bg-surface px-2"
    >
      <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
        {openTabs.map((id, i) => (
          <TabItem
            key={id}
            index={i}
            name={nameOf(id)}
            active={id === activeId}
            dirty={id === activeId && dirty}
            closable={openTabs.length > 1}
            renaming={renaming === id}
            onActivate={() => activate(id)}
            onRename={() => setRenaming(id)}
            onRenamed={(name) => {
              setRenaming(null)
              if (name) useDocumentStore.getState().renameCanvas(id, name)
            }}
            onClose={() => useDocumentStore.getState().closeCanvasTab(id)}
            dragFrom={dragFrom}
          />
        ))}
      </div>

      <Tip label="新建画布">
        <Button
          size="icon-sm"
          aria-label="新建画布"
          onClick={() => void createCanvasAndActivate()}
        >
          <Plus size={13} />
        </Button>
      </Tip>

      {canvases.length > openTabs.length || canvases.length > 6 ? (
        <AllCanvasesMenu activate={activate} />
      ) : null}
    </div>
  )
}

function TabItem({
  index,
  name,
  active,
  dirty,
  closable,
  renaming,
  onActivate,
  onRename,
  onRenamed,
  onClose,
  dragFrom,
}: {
  index: number
  name: string
  active: boolean
  dirty: boolean
  closable: boolean
  renaming: boolean
  onActivate: () => void
  onRename: () => void
  onRenamed: (name: string | null) => void
  onClose: () => void
  dragFrom: React.RefObject<number | null>
}) {
  const [draft, setDraft] = useState(name)
  useEffect(() => {
    if (renaming) setDraft(name)
  }, [renaming, name])

  if (renaming) {
    return (
      <input
        autoFocus
        value={draft}
        aria-label="画布名"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => onRenamed(draft.trim() || null)}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'Escape') onRenamed(null)
        }}
        className="h-6 w-28 shrink-0 rounded-sm border border-accent bg-surface px-1.5 text-xs text-ink outline-none"
      />
    )
  }

  return (
    <div
      role="tab"
      aria-selected={active}
      tabIndex={0}
      draggable
      onDragStart={() => {
        dragFrom.current = index
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={() => {
        if (dragFrom.current != null && dragFrom.current !== index) {
          useDocumentStore.getState().reorderTabs(dragFrom.current, index)
        }
        dragFrom.current = null
      }}
      onClick={onActivate}
      onDoubleClick={onRename}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onActivate()
        }
      }}
      className={cn(
        'group relative flex h-8 max-w-44 shrink-0 cursor-default items-center gap-1 px-2.5',
        'outline-none focus-visible:focus-ring',
        active
          ? 'text-ink after:absolute after:inset-x-1.5 after:bottom-0 after:h-0.5 after:rounded-full after:bg-ink'
          : 'text-ink-3 hover:text-ink-2',
      )}
      title={name}
    >
      <span className="truncate text-xs">{name}</span>
      {dirty && (
        <span
          aria-label="有未保存的改动"
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-ink-3"
        />
      )}
      {closable && (
        <button
          aria-label={`关闭标签 ${name}`}
          onClick={(e) => {
            e.stopPropagation()
            onClose()
          }}
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center rounded-sm text-ink-3',
            'opacity-0 outline-none hover:bg-ink/[.08] hover:text-ink',
            'focus-visible:opacity-100 focus-visible:focus-ring group-hover:opacity-100',
          )}
        >
          <X size={11} />
        </button>
      )}
    </div>
  )
}

/** 标签放不下 / 有未打开画布时的总览菜单 */
function AllCanvasesMenu({ activate }: { activate: (id: string) => void }) {
  const canvases = useDocumentStore((s) => s.canvases)
  const openTabs = useDocumentStore((s) => s.openTabs)
  const activeId = useDocumentStore((s) => s.activeCanvasId)
  const activeName = useDocumentStore((s) => s.doc.name)
  const unopened = canvases.filter((c) => !openTabs.includes(c.id))

  return (
    <Menu
      width={208}
      align="end"
      trigger={
        <Button size="icon-sm" aria-label="全部画布">
          <ChevronDown size={13} className="text-ink-2" />
        </Button>
      }
    >
      {openTabs.map((id) => (
        <MenuItem key={id} onSelect={() => activate(id)}>
          <span className={id === activeId ? 'text-ink' : undefined}>
            {id === activeId
              ? activeName
              : (canvases.find((c) => c.id === id)?.name ?? '')}
          </span>
        </MenuItem>
      ))}
      {unopened.length > 0 && (
        <>
          <MenuSeparator />
          {unopened.map((c) => (
            <MenuItem key={c.id} onSelect={() => activateCanvas(c.id, { open: true })}>
              <span className="text-ink-2">{c.name}</span>
            </MenuItem>
          ))}
        </>
      )}
    </Menu>
  )
}
