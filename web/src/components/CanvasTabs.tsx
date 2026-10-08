import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, Plus, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { keyOf } from '@/lib/keymap'
import { useFlip } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { activateCanvas, createCanvasAndActivate } from '@/store/canvasSession'
import { useDocumentStore } from '@/store/documentStore'
import { Button } from './ui/Button'
import { TextInput } from './ui/Input'
import { ZoomControls } from './ZoomControls'
import { Menu, MenuItem, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuSeparator } from './ui/Menu'
import { TAB_UNDERLINE, tabClass } from './ui/tabClass'
import { useBoldWidthLock } from './ui/useBoldWidthLock'
import { useRenameFocusReturn } from './ui/useRenameFocusReturn'
import { Tip } from './ui/Tooltip'

/**
 * Canvas 标签行：Tab = 打开的画布（关标签不删画布，全部画布见左栏「画布」）。
 * 单击切换、双击 / F2 重命名、拖动或 ⌥← / ⌥→ 重排、× / Delete / ⌘W 关闭；激活画布切换后视口自动 fit。
 *
 * 键盘（2026-10-07 设计审计 §10.1，ARIA tabs 模式）：整条只有**一个** Tab 停靠点（roving tabindex，落在
 * 当前画布上），←/→/Home/End 在页签之间挪焦点，Enter / Space 才切换（切画布要重新取景，不在方向键上自动切）。
 * × 不进 Tab 顺序（`tabIndex=-1`）：键盘用 Delete / ⌘W 关。
 */
export function CanvasTabs() {
  const { t } = useTranslation('workspace')
  const openTabs = useDocumentStore((s) => s.openTabs)
  const activeId = useDocumentStore((s) => s.activeCanvasId)
  const canvases = useDocumentStore((s) => s.canvases)
  const activeName = useDocumentStore((s) => s.doc.name)
  const [renaming, setRenaming] = useState<string | null>(null)
  const dragFrom = useRef<number | null>(null)
  // 重排是在 drop 那一刻整排换位的（拖动中只有一条落点提示线），
  // 不给动效的话标签「啪」地跳到新位置，看不出是哪一个被挪走了
  const strip = useRef<HTMLDivElement>(null)
  useFlip(strip)
  /** 拖动经过的目标标签，给一个可见的落点提示 */
  const [dragOver, setDragOver] = useState<number | null>(null)
  /** 键盘焦点所在的页签（roving tabindex 的那一个）；null = 跟着当前画布 */
  const [focusId, setFocusId] = useState<string | null>(null)
  const rovingId = focusId && openTabs.includes(focusId) ? focusId : activeId

  // 激活的页签在可视范围外时滚进来（新建的画布排在最后、从「全部画布」菜单切过去的可能在
  // 条外）：条没有滚动条可看，不滚的话用户不知道当前是哪一页。只动这条自己的 scrollLeft，
  // 不用 scrollIntoView——它会连带滚动外层；用 offsetLeft 而不是 getBoundingClientRect，
  // 重排时 useFlip 的 transform 动画不影响量值。
  // 不只在切页签时判：窗口 / 抽屉变窄、当前页签改成长名、前面的页签改名把它挤出去，当前页签都会
  // 出界而 activeId 没变——每次渲染后与 ResizeObserver 里都再判一次。只在「当前页签的位置 / 宽度、
  // 条宽」变了时才动：用户自己横滑到别处之后，一次无关的重渲染（改一下图）不许把条拽回来。
  // 认 data-canvas-tab / data-active，不认 role / aria-selected（web/AGENTS.md：选择器认稳定 data-*）
  const lastPlaced = useRef('')
  const keepActiveInView = useCallback(() => {
    const el = strip.current
    const tab = el?.querySelector<HTMLElement>('[data-canvas-tab][data-active]')
    if (!el || !tab) return
    const left = tab.offsetLeft
    // 宽度向上取整：offsetWidth 是四舍五入的整数，72.4px 的页签量成 72，滚完还差半个像素露在条外
    // （「全部画布」菜单切到最后一张时 e2e 实测差 1px）。FLIP 只用 translate，不影响宽度
    const right = left + Math.ceil(tab.getBoundingClientRect().width || tab.offsetWidth)
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

  // 溢出边缘的渐隐（2026-10-07 设计审计 §3.7 / §10.1）：条外还有页签的那一侧 16px alpha mask，
  // 不叠色块。只在真的还能往那边滚时画，滚到头就收
  const [fade, setFade] = useState<{ start: boolean; end: boolean }>({ start: false, end: false })
  const measureFade = useCallback(() => {
    const el = strip.current
    if (!el) return
    const start = el.scrollLeft > 1
    const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 1
    setFade((f) => (f.start === start && f.end === end ? f : { start, end }))
  }, [])

  useLayoutEffect(() => {
    measureOverflow()
    keepActiveInView()
    measureFade()
  })
  useEffect(() => {
    const el = strip.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      measureOverflow()
      keepActiveInView()
      measureFade()
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [measureOverflow, keepActiveInView, measureFade])

  const nameOf = (id: string) =>
    id === activeId ? activeName : (canvases.find((c) => c.id === id)?.name ?? '')

  const activate = (id: string) => activateCanvas(id)

  /** 把键盘焦点挪到第 i 个页签（roving tabindex）；i 按**这一次渲染**的 `openTabs` 算 */
  const focusTab = (i: number) => {
    const id = openTabs[Math.max(0, Math.min(openTabs.length - 1, i))]
    if (id) focusTabId(id)
  }
  /** 按 id 挪焦点：会改 `openTabs` 的动作（关 / 重排）之后用它——下标对的是改之前的数组；焦点跟着 DOM 走，渲染后再 focus */
  const focusTabId = (id: string) => {
    setFocusId(id)
    requestAnimationFrame(() =>
      strip.current?.querySelector<HTMLElement>(`[data-canvas-tab="${CSS.escape(id)}"]`)?.focus(),
    )
  }

  const onTabKey = (e: KeyboardEvent<HTMLDivElement>, id: string, i: number) => {
    const mod = e.metaKey || e.ctrlKey
    const closable = openTabs.length > 1
    let handled = true
    if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      // ⌥← / ⌥→：把这个页签往左 / 右挪一格（与拖动同一个 reorderTabs），焦点跟着它
      const to = i + (e.key === 'ArrowLeft' ? -1 : 1)
      if (to >= 0 && to < openTabs.length) {
        useDocumentStore.getState().reorderTabs(i, to)
        focusTabId(id)
      }
    } else if (e.key === 'ArrowLeft') focusTab(i === 0 ? openTabs.length - 1 : i - 1)
    else if (e.key === 'ArrowRight') focusTab(i === openTabs.length - 1 ? 0 : i + 1)
    else if (e.key === 'Home') focusTab(0)
    else if (e.key === 'End') focusTab(openTabs.length - 1)
    else if (e.key === 'Enter' || e.key === ' ') activate(id)
    else if (e.key === 'F2') setRenaming(id)
    else if (closable && (e.key === 'Delete' || (mod && e.key.toLowerCase() === 'w'))) {
      // 关之前先记下留下来的邻居（右边那个，没有就左边那个）：关完再按下标去取，取的是关之前的数组，
      // 关第一个 / 中间那个时会落回刚关掉的 id，焦点掉出页签条（Codex #833）
      const neighbor = openTabs[i + 1] ?? openTabs[i - 1]
      useDocumentStore.getState().closeCanvasTab(id)
      if (neighbor) focusTabId(neighbor)
    } else handled = false
    if (handled) {
      // 这些键在页签上有自己的意思：不许再冒到全局快捷键（Delete 会删画布上的选中对象）
      e.preventDefault()
      e.stopPropagation()
    }
  }

  return (
    /* px-3 与顶栏同值：品牌胶囊 / 页签文字左缘收成一条竖线（2026-09-15 打磨 T8）。
       条高 44 与右栏页签条同档（2026-09-30 重设计）；底边那条 hairline 与属性栏页签条的连成一条 */
    <div className="flex h-11 shrink-0 items-center gap-1 border-b border-border bg-surface px-3">
      {/* tablist 只许直接拥有 tab 子项（ARIA 硬性要求，axe critical）：
          role 挂在真正装着 TabItem 的滚动条上；「+」与画布菜单在 tablist 外 */}
      <div
        ref={strip}
        data-canvas-tabs
        data-fade-start={fade.start || undefined}
        data-fade-end={fade.end || undefined}
        role="tablist"
        aria-label={t('tabs.listLabel')}
        onScroll={measureFade}
        // scrollbar-none：横滚条不画（用户拍板），滚动靠触控板横滑 / Shift+滚轮 / 激活时自动滚到；
        // relative 让页签的 offsetLeft 以这条为基准，下面「滚进视野」用它量
        // pr-1：条宽常是小数（654.86px），浏览器的最大 scrollLeft 向下取整，最后一个页签滚到头仍差不到 1px
        // 露在条外；尾部留 4px，「滚到最后一张」才真的整颗在里面（e2e/canvas-tabs-scroll.spec.ts）
        className="scrollbar-none relative flex h-full min-w-0 shrink items-center gap-4 overflow-x-auto pr-1"
        style={fadeMask(fade)}
      >
        {openTabs.map((id, i) => (
          <TabItem
            key={id}
            id={id}
            index={i}
            name={nameOf(id)}
            active={id === activeId}
            roving={id === rovingId}
            closable={openTabs.length > 1}
            renaming={renaming === id}
            onActivate={() => {
              setFocusId(null)
              activate(id)
            }}
            onRename={() => setRenaming(id)}
            onRenamed={(name) => {
              setRenaming(null)
              if (name) useDocumentStore.getState().renameCanvas(id, name)
            }}
            onClose={() => useDocumentStore.getState().closeCanvasTab(id)}
            onKeyDown={(e) => onTabKey(e, id, i)}
            onFocus={() => setFocusId(id)}
            dragFrom={dragFrom}
            dropSide={
              dragOver === i && dragFrom.current != null && dragFrom.current !== i
                ? dragFrom.current < i
                  ? 'after'
                  : 'before'
                : null
            }
            setDragOver={setDragOver}
          />
        ))}
      </div>

      {/* 「+」紧跟最后一个标签（2026-09-11 用户反馈），画布总览菜单仍靠右 */}
      <Tip label={t('tabs.newCanvas')}>
        <Button
          size="icon-sm"
          data-new-canvas-tab
          aria-label={t('tabs.newCanvas')}
          onClick={() => void createCanvasAndActivate()}
        >
          <Plus size={ICON_SIZE.sm} />
        </Button>
      </Tip>
      <span ref={spacer} className="flex-1" />

      {menuPinned || overflowing ? <AllCanvasesMenu activate={activate} /> : null}
      {/* 缩放菜单住在标签行最右（2026-09-30 重设计 A1；此前在顶栏右段） */}
      <ZoomControls />
    </div>
  )
}

/** 溢出那一侧的 16px alpha 渐隐（mask-image，不叠色块：底下是什么颜色都对） */
function fadeMask(f: { start: boolean; end: boolean }): React.CSSProperties | undefined {
  if (!f.start && !f.end) return undefined
  const a = f.start ? 'transparent 0, #000 16px' : '#000 0'
  const b = f.end ? '#000 calc(100% - 16px), transparent 100%' : '#000 100%'
  const mask = `linear-gradient(to right, ${a}, ${b})`
  return { maskImage: mask, WebkitMaskImage: mask }
}

function TabItem({
  id,
  index,
  name,
  active,
  roving,
  closable,
  renaming,
  onActivate,
  onRename,
  onRenamed,
  onClose,
  onKeyDown,
  onFocus,
  dragFrom,
  dropSide,
  setDragOver,
}: {
  id: string
  index: number
  name: string
  active: boolean
  /** roving tabindex 落在它上面（整条唯一的 Tab 停靠点） */
  roving: boolean
  closable: boolean
  renaming: boolean
  onActivate: () => void
  onRename: () => void
  onRenamed: (name: string | null) => void
  onClose: () => void
  onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => void
  onFocus: () => void
  dragFrom: React.RefObject<number | null>
  /** 拖动排序的落点：这个页签的左边 / 右边亮一根 2px 竖线 */
  dropSide: 'before' | 'after' | null
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

  // Enter / Esc 收起改名把焦点还给这个页签；点了别处（另一个输入框、工具条上的钮）收起的不抢回来（Codex #833）
  const tabRef = useRef<HTMLDivElement>(null)
  const markKeyFinish = useRenameFocusReturn(renaming, tabRef)

  if (renaming) {
    return (
      <TextInput
        autoFocus
        value={draft}
        aria-label={t('tabs.canvasName')}
        data-canvas-tab-rename
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => onRenamed(draft.trim() || null)}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter') {
            markKeyFinish()
            ;(e.target as HTMLInputElement).blur()
          }
          if (e.key === 'Escape') {
            markKeyFinish()
            onRenamed(null)
          }
        }}
        className="w-28 shrink-0"
      />
    )
  }

  return (
    <div
      ref={tabRef}
      role="tab"
      data-flip-id={id}
      data-canvas-tab={id}
      data-active={active || undefined}
      data-drop={dropSide ?? undefined}
      aria-selected={active}
      tabIndex={roving ? 0 : -1}
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
      onKeyDown={onKeyDown}
      onFocus={onFocus}
      className={cn(
        // 选中态与右栏页签同一副语法（`tabClass`：600 + ink + 2px 线，宪法第五节）。
        // 高度取 tabClass 的 h-full，不另写：条是 h-11 + border-b，里面只剩 43px，页签再写一个固定高度
        // 就会让横滚条的 overflow-y 被算成 auto、多出 1px 纵向滚动——WebKit 当场画一根竖滚动条
        // （e2e/canvas-tabs-scroll.spec.ts 量 scrollHeight ≤ clientHeight）
        tabClass(active),
        'group flex max-w-44 shrink-0 cursor-default items-center gap-1',
        // 关闭键仍绝对定位，只在右边留出它那一格（20px 命中区）：左缘因此是文字本身（T8）。
        // 多页签时这一格常驻，激活不改宽度（双击非当前页签改名时，第二下不许落在挪过来的 × 上，
        // e2e/canvas-tabs-scroll.spec.ts）。
        // 页签上**没有「未保存」点**（2026-10-07 审计 P0）：保存状态是整份文档的事（顶栏文档状态芯片）
        closable && 'pr-5',
      )}
      title={name}
    >
      {/* 拖动排序的落点：页签之间一根 2px accent 竖线（2026-10-07 设计审计 §10.1，与列表的 `dropLineClass`
          同一种语言：线说「落在这儿」），不再把目标页签整块染灰（那像是「选中了它」） */}
      {dropSide && (
        <span
          aria-hidden
          data-drop-line
          className={cn(
            'pointer-events-none absolute inset-y-2.5 w-0.5 rounded-full bg-accent',
            dropSide === 'before' ? '-left-2.5' : '-right-2.5',
          )}
        />
      )}
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
      {closable && (
        <button
          type="button"
          // 不进 Tab 顺序：整条只有一个停靠点（roving），键盘用 Delete / ⌘W 关（ARIA tabs 模式里 tab 内不嵌可聚焦控件）
          tabIndex={-1}
          data-canvas-tab-close
          aria-label={t('tabs.closeTab', { name })}
          title={`${t('tabs.closeTab', { name })} (${keyOf('tabClose')})`}
          onClick={(e) => {
            e.stopPropagation()
            // 第二道防线：双击的第二下（detail ≥ 2）落到 × 上不算关闭——那一下是冲着改名去的
            if (e.detail >= 2) return
            onClose()
          }}
          className={cn(
            'absolute right-0 top-1/2 -translate-y-1/2',
            'flex size-5 shrink-0 items-center justify-center rounded-full text-ink-3',
            'opacity-0 outline-none hover:bg-surface-hover hover:text-ink',
            'group-hover:opacity-100 group-focus-visible:opacity-100',
          )}
        >
          <X size={ICON_SIZE.xs} />
        </button>
      )}
    </div>
  )
}

/**
 * 标签放不下 / 有未打开画布时的总览菜单。打开着的那些是一组互斥取值（当前那张带勾，`MenuRadioGroup`），
 * 未打开的另起一组、带「未打开」组头——此前两组只差一条分隔线和字色，看不出第二组是什么。
 */
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
      <MenuRadioGroup value={activeId} onValueChange={activate}>
        {openTabs.map((id) => (
          <MenuRadioItem key={id} value={id} data-all-canvases-item={id}>
            {id === activeId ? activeName : (canvases.find((c) => c.id === id)?.name ?? '')}
          </MenuRadioItem>
        ))}
      </MenuRadioGroup>
      {unopened.length > 0 && (
        <>
          <MenuSeparator />
          <MenuLabel>{t('tabs.unopened')}</MenuLabel>
          {unopened.map((c) => (
            <MenuItem
              key={c.id}
              data-all-canvases-item={c.id}
              onSelect={() => activateCanvas(c.id, { open: true })}
            >
              {c.name}
            </MenuItem>
          ))}
        </>
      )}
    </Menu>
  )
}
