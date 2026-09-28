import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
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

  // 激活的页签在可视范围外时滚进来（新建的画布排在最后、从「全部画布」菜单切过去的可能在
  // 条外）：条没有滚动条可看，不滚的话用户不知道当前是哪一页。只动这条自己的 scrollLeft，
  // 不用 scrollIntoView——它会连带滚动外层；用 offsetLeft 而不是 getBoundingClientRect，
  // 重排时 useFlip 的 transform 动画不影响量值。
  // 不只在切页签时判：窗口 / 抽屉变窄、当前页签改成长名、前面的页签改名把它挤出去，当前页签都会
  // 出界而 activeId 没变——每次渲染后与 ResizeObserver 里都再判一次。只在「当前页签的位置 / 宽度、
  // 条宽」变了时才动：用户自己横滑到别处之后，一次无关的重渲染（改一下图、dirty 变了）不许把条拽回来。
  // 认 data-canvas-tab / data-active，不认 role / aria-selected（web/AGENTS.md：选择器认稳定 data-*）
  const lastPlaced = useRef('')
  const keepActiveInView = useCallback(() => {
    const el = strip.current
    const tab = el?.querySelector<HTMLElement>('[data-canvas-tab][data-active]')
    if (!el || !tab) return
    const left = tab.offsetLeft
    const right = left + tab.offsetWidth
    const placed = `${tab.dataset.canvasTab}|${left}|${right}|${el.clientWidth}`
    if (placed === lastPlaced.current) return
    lastPlaced.current = placed
    if (left < el.scrollLeft) el.scrollLeft = left
    else if (right > el.scrollLeft + el.clientWidth) el.scrollLeft = right - el.clientWidth
  }, [])

  // 条放不下时也给「全部画布」菜单：横滚条不画之后，只有鼠标、又不在 macOS 上按 Shift 的人
  // 没有别的办法够到条外的页签。每次渲染量一次（页签增删、改名都会重渲染），窗口 / 抽屉
  // 改宽度不重渲染，交给 ResizeObserver——与 `ui/slidingIndicator` 同一写法
  //
  // 菜单只因「放不下」才在时，判「放不放得下」要按撤掉菜单之后的宽度量：菜单自己占着条右边
  // 一截，按含菜单的 clientWidth 判的话，窗口拉宽到「不带菜单放得下、带菜单放不下」之间时
  // 菜单永远收不起来（#688）。条缩着时右侧的弹性空白是 0，撤掉菜单能多出来的就是空白起点
  // 到菜单右缘这一段（菜单 + 它前面那道 gap）；菜单因别的条件常驻时只算空白
  const spacer = useRef<HTMLSpanElement>(null)
  const menuPinned = canvases.length > openTabs.length || canvases.length > 6
  const [overflowing, setOverflowing] = useState(false)
  const measureOverflow = useCallback(() => {
    const el = strip.current
    const gap = spacer.current
    if (!el || !gap) return
    const menu = menuPinned
      ? null
      : gap.parentElement?.querySelector<HTMLElement>('[data-all-canvases]')
    const from = gap.getBoundingClientRect()
    const slack = (menu ? menu.getBoundingClientRect().right : from.right) - from.left
    setOverflowing(el.scrollWidth > el.clientWidth + slack + 1)
  }, [menuPinned])
  useLayoutEffect(() => {
    measureOverflow()
    keepActiveInView()
  })
  useEffect(() => {
    const el = strip.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      measureOverflow()
      keepActiveInView()
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [measureOverflow, keepActiveInView])

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
        // scrollbar-none：横滚条不画（用户拍板），滚动靠触控板横滑 / Shift+滚轮 / 激活时自动滚到；
        // relative 让页签的 offsetLeft 以这条为基准，下面「滚进视野」用它量
        className="scrollbar-none relative flex h-full min-w-0 shrink items-center gap-4 overflow-x-auto"
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
      <span ref={spacer} className="flex-1" />

      {menuPinned || overflowing ? (
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
      data-canvas-tab={id}
      data-active={active || undefined}
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
        // 关闭键仍绝对定位，只在右边留出它那一格：左缘因此是文字本身（T8）。
        // 未保存的点也住在这一格里（悬停时让给 ×）：此前它是 flex 里的第三个子元素、只在当前页签
        // 出现，激活一个页签它就宽 10px、× 跟着右移——双击非当前页签改名时，第二下落在挪过来的
        // × 上把页签关了（WebKit，e2e/canvas-tabs-scroll.spec.ts）。多页签时这一格常驻，激活不改宽度
        (closable || dirty) && 'pr-5',
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
          className={cn(
            'pointer-events-none absolute right-0 top-1/2 flex h-4 w-4 -translate-y-1/2 items-center justify-center',
            closable && 'group-hover:opacity-0',
          )}
        >
          <span aria-label={t('tabs.unsaved')} className="h-1.5 w-1.5 rounded-full bg-ink-3" />
        </span>
      )}
      {closable && (
        <button
          aria-label={t('tabs.closeTab', { name })}
          onClick={(e) => {
            e.stopPropagation()
            // 第二道防线：双击的第二下（detail ≥ 2）落到 × 上不算关闭——那一下是冲着改名去的
            if (e.detail >= 2) return
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
        <Button size="icon-sm" data-all-canvases aria-label={t('tabs.allCanvases')}>
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
