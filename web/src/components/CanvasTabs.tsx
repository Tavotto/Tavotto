import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, Plus, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { useFlip } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { activateCanvas, createCanvasAndActivate } from '@/store/canvasSession'
import { useDocumentStore } from '@/store/documentStore'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'
import { Menu, MenuItem, MenuSeparator } from './ui/Menu'
import { TAB_UNDERLINE, tabClass } from './ui/tabClass'
import { useBoldWidthLock } from './ui/useBoldWidthLock'
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
    /* px-3 与顶栏同值：品牌标 12 / 页签盒 8 / 页签文字 18 三条竖线收成一条（2026-09-15 打磨 T8）。
       条高 36 与右栏页签同档（B1）；顶栏那条 border-b 已删，整屏的那一条 hairline 就是这里 */
    <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border bg-surface px-3">
      {/* tablist 只许直接拥有 tab 子项（ARIA 硬性要求，axe critical）：
          role 挂在真正装着 TabItem 的滚动条上；「+」与画布菜单在 tablist 外 */}
      <div
        ref={strip}
        data-canvas-tabs
        role="tablist"
        aria-label={t('tabs.listLabel')}
        className="flex h-full min-w-0 shrink items-center gap-4 overflow-x-auto"
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

      {/* 「+」紧跟最后一个标签（2026-09-11 用户反馈），画布总览菜单仍靠右 */}
      <Tip label={t('tabs.newCanvas')}>
        <Button
          size="icon-sm"
          aria-label={t('tabs.newCanvas')}
          onClick={() => void createCanvasAndActivate()}
        >
          <Plus size={ICON_SIZE.sm} />
        </Button>
      </Tip>
      <span className="flex-1" />

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

  // 选中态 600 比 400 宽 2~3%：量一次加粗宽度写成 min-width，切页签时邻居不挪——与右栏 `Tab` 同一个钩子
  const nameRef = useRef<HTMLSpanElement>(null)
  useBoldWidthLock(nameRef)

  if (renaming) {
    return (
      <TextInput
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
        className="w-28 shrink-0"
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
        // 选中态与右栏页签同一副语法（`tabClass`：600 + ink + 2px 线，宪法第五节）：
        // 此前这里只借了那条线，选中仍是 400——同一屏两种「选中」（2026-09-15 打磨 B1）
        // 高度取 tabClass 的 h-full，不另写 h-9：条是 h-9 + border-b，里面只剩 35px，36px 的页签
        // 让横滚条的 overflow-y 被算成 auto、多出 1px 纵向滚动——WebKit 当场在「+」左边画一根
        // 竖滚动条（e2e/canvas-tabs-scroll.spec.ts 量 scrollHeight ≤ clientHeight）
        tabClass(active),
        'group flex max-w-44 shrink-0 cursor-default items-center gap-1',
        // 关闭键仍绝对定位，只在右边留出它那一格：左缘因此是文字本身（T8）
        closable && 'pr-5',
        // 拖动排序的落点提示：不只靠颜色，加背景块让目标一眼可辨
        dragOver && 'rounded-sm bg-selected text-ink',
      )}
      title={name}
    >
      {/* 下划线挂在**文字盒**上而不是整个 tab 上（B2）：此前「Figure 1」41px 宽、线 49px，
          可关闭时还延到 × 底下。外层给 h-full 让 `after:bottom-0` 落在条的底边 */}
      <span
        ref={nameRef}
        className={cn(
          'relative flex h-full min-w-0 items-center',
          active && cn(TAB_UNDERLINE, 'after:inset-x-0'),
        )}
      >
        <span className="truncate">{name}</span>
      </span>
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
            'absolute right-0 top-1/2 -translate-y-1/2',
            'flex h-4 w-4 shrink-0 items-center justify-center rounded-sm text-ink-3',
            'opacity-0 outline-none hover:bg-surface-hover hover:text-ink',
            'focus-visible:opacity-100 focus-visible:focus-ring group-hover:opacity-100',
          )}
        >
          <X size={ICON_SIZE.xs} />
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
          <ChevronDown size={ICON_SIZE.xs} className="text-ink-2" />
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
