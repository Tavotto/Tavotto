import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, Plus, X } from 'lucide-react'
import { useFlip } from '@/lib/motion'
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
  const { t } = useTranslation('workspace')
  const openTabs = useDocumentStore((s) => s.openTabs)
  const activeId = useDocumentStore((s) => s.activeCanvasId)
  const canvases = useDocumentStore((s) => s.canvases)
  const activeName = useDocumentStore((s) => s.doc.name)
  const dirty = useDocumentStore((s) => s.dirty)
  const [renaming, setRenaming] = useState<string | null>(null)
  const dragFrom = useRef<number | null>(null)
  // 重排是在 drop 那一刻整排换位的（拖动中只有一条落点提示线），
  // 不给动效的话标签「啪」地跳到新位置，看不出是哪一个被挪走了
  const strip = useRef<HTMLDivElement>(null)
  useFlip(strip)
  /** 拖动经过的目标标签，给一个可见的落点提示 */
  const [dragOver, setDragOver] = useState<number | null>(null)

  const nameOf = (id: string) =>
    id === activeId ? activeName : (canvases.find((c) => c.id === id)?.name ?? '')

  const activate = (id: string) => activateCanvas(id)

  return (
    <div className="flex h-8 shrink-0 items-center gap-0.5 border-b border-border bg-surface px-2">
      {/* tablist 只许直接拥有 tab 子项（ARIA 硬性要求，axe critical）：
          role 挂在真正装着 TabItem 的滚动条上；「+」与画布菜单在 tablist 外 */}
      <div
        ref={strip}
        role="tablist"
        aria-label={t('tabs.listLabel')}
        className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto"
      >
        {openTabs.map((id, i) => (
          <TabItem
            key={id}
            id={id}
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
            dragOver={dragOver === i && dragFrom.current !== i}
            setDragOver={setDragOver}
          />
        ))}
      </div>

      <Tip label={t('tabs.newCanvas')}>
        <Button
          size="icon-sm"
          aria-label={t('tabs.newCanvas')}
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
  id,
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
  dragOver,
  setDragOver,
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
  id: string
  dragFrom: React.RefObject<number | null>
  dragOver: boolean
  setDragOver: (i: number | null) => void
}) {
  const { t } = useTranslation('workspace')
  const [draft, setDraft] = useState(name)
  useEffect(() => {
    if (renaming) setDraft(name)
  }, [renaming, name])

  if (renaming) {
    return (
      <input
        autoFocus
        value={draft}
        aria-label={t('tabs.canvasName')}
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
      data-flip-id={id}
      aria-selected={active}
      tabIndex={0}
      draggable
      onDragStart={(e) => {
        // Firefox / WebKit 不写 dataTransfer 数据就不会真正开始拖拽
        e.dataTransfer.setData('text/plain', name)
        e.dataTransfer.effectAllowed = 'move'
        dragFrom.current = index
      }}
      onDragOver={(e) => {
        e.preventDefault()
        e.dataTransfer.dropEffect = 'move'
        if (dragFrom.current != null) setDragOver(index)
      }}
      onDragLeave={() => setDragOver(null)}
      onDragEnd={() => {
        dragFrom.current = null
        setDragOver(null)
      }}
      onDrop={(e) => {
        e.preventDefault()
        if (dragFrom.current != null && dragFrom.current !== index) {
          useDocumentStore.getState().reorderTabs(dragFrom.current, index)
        }
        dragFrom.current = null
        setDragOver(null)
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
        // 拖动排序的落点提示：不只靠颜色，加背景块让目标一眼可辨
        dragOver && 'rounded-sm bg-accent-subtle text-accent',
      )}
      title={name}
    >
      <span className="truncate text-xs">{name}</span>
      {dirty && (
        <span
          aria-label={t('tabs.unsaved')}
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-ink-3"
        />
      )}
      {closable && (
        <button
          aria-label={t('tabs.closeTab', { name })}
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
  const { t } = useTranslation('workspace')
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
        <Button size="icon-sm" aria-label={t('tabs.allCanvases')}>
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
