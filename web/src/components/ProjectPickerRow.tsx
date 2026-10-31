import { useState, type KeyboardEvent, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { ArrowDown, ArrowUp, Bookmark, ExternalLink, Folder, TriangleAlert, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { dropLineClass, listRowClass, rowMetaClass } from '@/components/ui/listRow'
import type { RecentProject } from '@/lib/api'
import { canRevealInFileManager, fileManagerKind, isDesktop, revealProjectFolder, type FileManagerKind } from '@/lib/desktop'
import { ALT, cn, combo } from '@/lib/utils'
import { useUiStore } from '@/store/uiStore'
import { TailPath } from './DirBrowser'
import { openInNewTab } from './left/projectActions'
import { IconButton } from './ui/Button'
import { MenuItem, MenuSeparator } from './ui/Menu'
import { RowMenu } from './ui/RowMenu'
import { useRowMenu } from './ui/useRowMenu'

/** 本组文案在 project:workspace.* 下 */
const ws = (key: string, values?: Record<string, unknown>) =>
  translate(`workspace.${key}`, { ns: 'project', ...(values ?? {}) })

/** 「在 Finder 中打开」按平台说出文件管理器的名字（键写全，方便按键名查到用处） */
const REVEAL_LABEL: Record<FileManagerKind, string> = {
  finder: 'revealInFinder',
  explorer: 'revealInExplorer',
  files: 'revealInFileManager',
}

/**
 * 在系统文件管理器里打开项目文件夹（有脚本就选中脚本）。只有桌面壳做得到（`canRevealInFileManager`），
 * 浏览器模式不摆这一项；失败绝不静默——把完整路径告诉用户。
 */
export function RevealItem({ path }: { path: string }) {
  return (
    <MenuItem data-workspace-reveal onSelect={() => revealProject(path)} icon={Folder}>
      {revealLabel()}
    </MenuItem>
  )
}

/** 「在 Finder 中打开」的动作本体：菜单项与「全部项目」详情卡的链接共用，失败都把完整路径告诉用户 */
export function revealProject(path: string): void {
  void revealProjectFolder(path).then((ok) => {
    if (!ok) useUiStore.getState().setStatus(msg('workspace.revealFailed', { path }, 'project'), 'error')
  })
}

/** 文案按平台说文件管理器的名字 */
export const revealLabel = (): string => ws(REVEAL_LABEL[fileManagerKind()])

/** 已收藏的那一枚（实心孪生）：`MenuItem icon=` 只收组件，不收 `filled` 这一档 */
export const BookmarkFilled = (p: { size?: number; className?: string }) => <Bookmark {...p} filled />

export interface ProjectRowOrder {
  index: number
  count: number
  /** 拖动起点记**路径**：落下时按路径发 move，由后端查此刻的位置 */
  dragFrom: RefObject<string | null>
  /** 起点在收藏里排第几（决定落点线画在上缘还是下缘） */
  indexOf: (path: string) => number
  move: (delta: number) => void
  moveTo: (fromPath: string) => void
}

/**
 * 一个项目的一行——**左栏「项目」抽屉与 Project Picker「全部项目」共用这一份**（2026-10-07 设计审计 §10.3：
 * 此前两处各一种行、各一种菜单、各一种移除写法）。
 *
 * 抽屉里的行：44px（listRowClass md），名字 + 一行路径 / 辨认后缀，行上有收藏开关。
 * 「全部项目」页不再用它（手帐索引页，2026-10-28 redesign-3 A3）：那一屏的行是下面的 `ProjectIndexRow`。
 *
 * 菜单只有一份清单、三个入口（⋯ / 右键 / ⇧F10，`ui/RowMenu`）；收藏区的行可拖（落点 `dropLineClass`）、
 * ⌥↑ / ⌥↓ 与菜单里的上移 / 下移是拖动的键盘那条路。行的主按钮带 `data-roving`，一列一个 Tab 停靠点
 * （`left/rovingList`），Enter 打开。
 */
export function ProjectRow({
  entry,
  hint,
  busy,
  switching,
  current = false,
  pinned,
  onTogglePin,
  onOpen,
  onRemove,
  order,
}: {
  entry: RecentProject
  /** 同名项目的辨认后缀（`lib/recentProjects.disambiguateRecent`） */
  hint?: string
  busy: boolean
  /** 有一次切换正在进行：这一行也不能点（切换串行，见 projectStore） */
  switching: boolean
  /**
   * 这一行是不是本标签页此刻的项目——由调用方按 `project.figures_dir` 算，**不读
   * `entry.current`**：那是上一次刷新时按当时的 pj 算的，切换完成到刷新回来之间是旧的。
   */
  current?: boolean
  /** 抽屉：收藏状态（行上的开关与菜单项） */
  pinned?: boolean
  onTogglePin?: () => void
  onOpen: () => void
  /** 「从列表移除」（最近区 / 选择器）；收藏区的行取消收藏即可 */
  onRemove?: () => void
  /** 收藏区、且没在筛选：可拖动、可上移 / 下移 */
  order?: ProjectRowOrder
}) {
  useTranslation('project')
  const openable = entry.exists && !current && !busy && !switching
  // 桌面壳不新开窗口（main.rs new_window_decision 一律拒绝），点了会静默无反应：桌面版不给这一项
  const canNewTab = !!entry.id && !current && !isDesktop()
  const canReveal = entry.exists && canRevealInFileManager()
  const hasMenu = !!order || !!onTogglePin || canNewTab || canReveal || !!onRemove
  const menu = useRowMenu({ enabled: hasMenu })
  const [drop, setDrop] = useState<'before' | 'after' | null>(null)
  const meta = rowMetaClass(current)

  const items = (
    <>
      {/* 拖动只有鼠标能用：菜单里给键盘一条同样的路（⌥↑ / ⌥↓ 同一个动作） */}
      {order && (
        <>
          <MenuItem
            disabled={order.index === 0}
            onSelect={() => order.move(-1)}
            icon={ArrowUp}
            shortcut={combo(ALT, '↑')}
            data-project-move="up"
          >
            {ws('moveUp')}
          </MenuItem>
          <MenuItem
            disabled={order.index >= order.count - 1}
            onSelect={() => order.move(1)}
            icon={ArrowDown}
            shortcut={combo(ALT, '↓')}
            data-project-move="down"
          >
            {ws('moveDown')}
          </MenuItem>
          <MenuSeparator />
        </>
      )}
      {onTogglePin && (
        <MenuItem icon={pinned ? BookmarkFilled : Bookmark} onSelect={onTogglePin}>
          {pinned ? ws('unpin') : ws('pin')}
        </MenuItem>
      )}
      {/* 新标签页要带 pj：只有后端已经打开着的项目才有 id */}
      {canNewTab && (
        <MenuItem onSelect={() => openInNewTab(entry.id!)} icon={ExternalLink}>
          {ws('openInNewTab')}
        </MenuItem>
      )}
      {canReveal && <RevealItem path={entry.path} />}
      {onRemove && (
        <>
          {(canNewTab || canReveal || onTogglePin) && <MenuSeparator />}
          {/* 只动列表、不删磁盘：不用垃圾桶，也不标 danger */}
          <MenuItem onSelect={onRemove} icon={X} data-project-remove>
            {ws('removeFromList')}
          </MenuItem>
        </>
      )}
    </>
  )

  const onKeyDown = (e: KeyboardEvent) => {
    menu.rowProps.onKeyDown?.(e)
    if (e.defaultPrevented || !order || !e.altKey) return
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
    const delta = e.key === 'ArrowUp' ? -1 : 1
    if ((delta < 0 && order.index === 0) || (delta > 0 && order.index >= order.count - 1)) return
    e.preventDefault()
    order.move(delta)
  }

  return (
    <li
      {...menu.rowProps}
      onKeyDown={onKeyDown}
      data-workspace-row
      // 稳定的身份锚点（e2e / 诊断按它找行，不按可达名——名字会重复、会翻译）
      data-project-path={entry.path}
      data-project-tutorial={entry.tutorial || undefined}
      draggable={!!order}
      onDragStart={() => {
        if (order) order.dragFrom.current = entry.path
      }}
      // 拖动取消 / 松在列表外时 onDrop 不会来：不清的话起点一直挂着，之后任何外部拖进来
      // 落在收藏行上的东西都会被当成「挪那一条」（Codex #550）
      onDragEnd={() => {
        if (order) order.dragFrom.current = null
        setDrop(null)
      }}
      // 只接本列表内部发起的重排：起点为空 = 外部拖进来的，不 preventDefault（不接收）
      onDragOver={
        order
          ? (e) => {
              const from = order.dragFrom.current
              if (from == null || from === entry.path) return
              e.preventDefault()
              // 落下之后它占这一行的位置：从上面拖下来就画在下缘，从下面拖上来画在上缘
              setDrop(order.indexOf(from) < order.index ? 'after' : 'before')
            }
          : undefined
      }
      onDragLeave={() => setDrop(null)}
      onDrop={() => {
        const from = order?.dragFrom.current
        if (order && from != null && from !== entry.path) order.moveTo(from)
        if (order) order.dragFrom.current = null
        setDrop(null)
      }}
      className={cn(
        // 失效行不整行压淡：那句红字要过 4.5:1，压到 45% 就过不了；只把名字降一档
        listRowClass({ size: 'md', selected: current }),
        'gap-2 pl-2 pr-1',
      )}
    >
      <span aria-hidden className={dropLineClass(drop)} />
      <button
        type="button"
        data-roving
        // 行里有打开 / 收藏 / 「…」几颗钮：「打开」自己带锚点，不靠在行里的次序
        data-workspace-open
        onClick={() => openable && onOpen()}
        // 目录不在了是**常态**的打不开：用 aria-disabled 而不是 disabled，行仍是漫游列表里的一站，
        // 焦点落得进来，⋯（移除 / 收藏）才够得着（Codex #832）。切换中 / 正在打开是一时的，照旧 disabled
        disabled={entry.exists && !openable}
        aria-disabled={!openable || undefined}
        aria-current={current || undefined}
        aria-label={translate('picker.openProject', { ns: 'project', name: entry.name })}
        title={entry.tutorial ? undefined : entry.path}
        className="min-w-0 flex-1 self-stretch text-left outline-none focus-visible:focus-ring disabled:cursor-default aria-disabled:cursor-default"
      >
        <span className="block min-w-0">
        <span className="flex items-center gap-1.5">
          <span className={cn('truncate', entry.exists ? 'text-ink' : 'text-ink-3')}>{entry.name}</span>
          {!entry.exists && (
            <span className="flex shrink-0 items-center gap-1 text-xs font-normal text-danger-content">
              <TriangleAlert size={ICON_SIZE.xs} aria-hidden />
              {translate('picker.missingDir', { ns: 'project' })}
            </span>
          )}
          {entry.opened && !current && <span className={cn('shrink-0', meta)}>{ws('openedElsewhere')}</span>}
          {busy && (
            <span className={cn('shrink-0', meta)}>{translate('picker.opening', { ns: 'project' })}</span>
          )}
        </span>
        {/* 教程副本躺在数据目录里：显示「教程项目」而不是那条路径（T-104） */}
        {entry.tutorial ? (
          <span className={cn('block', meta)}>{translate('picker.tutorialBadge', { ns: 'project' })}</span>
        ) : hint ? (
          <span className={cn('block truncate font-mono', meta)}>{hint}</span>
        ) : (
          <TailPath path={entry.path} className={cn('font-normal', current ? 'text-ink-2' : 'text-ink-3')} />
        )}
        </span>
      </button>
      {/* 收藏开关就在行上：这是这个抽屉最常做的整理动作，不该藏进菜单第二层 */}
      {onTogglePin && (
        <IconButton
          iconSize="sm"
          label={pinned ? ws('unpinLabel', { name: entry.name }) : ws('pinLabel', { name: entry.name })}
          tip={pinned ? ws('unpin') : ws('pin')}
          aria-pressed={pinned}
          data-workspace-pin
          tabIndex={menu.focusWithin ? 0 : -1}
          onClick={onTogglePin}
          className={cn(
            'transition-opacity focus-visible:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100',
            pinned ? 'opacity-100' : 'opacity-0',
          )}
        >
          <Bookmark size={ICON_SIZE.sm} filled={pinned} className={pinned ? 'text-ink-2' : 'text-ink-3'} />
        </IconButton>
      )}
      {/* 打不开的条目只剩「移除」一个动作：⋯ 常驻，不藏在悬停后面 */}
      <RowMenu
        state={menu}
        label={ws('rowActions', { name: entry.name })}
        width={200}
        visible={entry.exists ? 'hover' : 'always'}
        data-project-menu
      >
        {items}
      </RowMenu>
    </li>
  )
}

/**
 * 「全部项目」页索引里的一行（手帐索引页，2026-10-28 redesign-3 A3）：「•」记号 · 名字 · 虚点引线 · 短日期（等宽数字）。
 * 目录已不存在的是「×」记号 + 删除线。一行就是**一颗**漫游按钮（`data-roving`）：
 * 单击 / 聚焦 = 选中（右边详情卡跟着翻页），双击或 Enter = 打开；↑↓ 的漫游由列表容器的 `useRovingList` 管。
 * 行上没有任何别的控件——打开 / 在 Finder 中显示 / 从列表移除都在详情卡里。
 */
export function ProjectIndexRow({
  entry,
  hint,
  when,
  selected,
  busy,
  switching,
  onSelect,
  onOpen,
}: {
  entry: RecentProject
  /** 同名项目的辨认后缀 */
  hint?: string
  /** 短日期（已格式化；没有记录时缺席） */
  when?: string
  selected: boolean
  busy: boolean
  /** 有一次切换正在进行：这一行也不能点（切换串行，见 projectStore） */
  switching: boolean
  onSelect: () => void
  onOpen: () => void
}) {
  useTranslation('project')
  const openable = entry.exists && !busy && !switching
  return (
    <li
      data-picker-row
      data-project-path={entry.path}
      data-project-tutorial={entry.tutorial || undefined}
      data-selected={selected || undefined}
      className="list-none"
    >
      <button
        type="button"
        data-roving
        data-picker-open
        onClick={onSelect}
        onFocus={onSelect}
        onDoubleClick={() => openable && onOpen()}
        onKeyDown={(e) => {
          // Enter 是打开，不是「点一下 = 选中」：拦掉原生 click
          if (e.key !== 'Enter' || e.altKey || e.metaKey || e.ctrlKey || e.shiftKey) return
          e.preventDefault()
          if (openable) onOpen()
        }}
        // 切换中 / 正在打开是一时的，照旧 disabled；目录不在了是常态的打不开：仍是漫游的一站，能选中、看详情、移除
        disabled={entry.exists && !openable}
        aria-current={selected || undefined}
        aria-label={translate('picker.openProject', { ns: 'project', name: entry.name })}
        title={entry.tutorial ? undefined : entry.path}
        className={cn(
          'flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-base outline-none transition-colors duration-fast',
          'focus-visible:focus-ring-inset disabled:cursor-default',
          selected ? 'bg-selected text-ink' : 'text-ink-2 hover:bg-surface-hover hover:text-ink',
        )}
      >
        <span
          aria-hidden
          className={cn('w-2.5 shrink-0 text-center', selected ? 'text-ink' : 'text-ink-faint')}
        >
          {entry.exists ? '•' : '×'}
        </span>
        <span className={cn('truncate', !entry.exists && 'text-ink-faint line-through')}>{entry.name}</span>
        {hint && (
          <span className="min-w-0 truncate font-mono text-xs text-ink-3" dir="ltr">
            {hint}
          </span>
        )}
        {busy && <span className="shrink-0 text-xs text-ink-3">{translate('picker.opening', { ns: 'project' })}</span>}
        {/* 虚点引线：贴着名字的基线往右拉到日期；名字很长时它收缩到只剩一点 */}
        <span aria-hidden className="mb-[-6px] h-px min-w-2 flex-1 self-center border-b border-dotted border-ink-faint" />
        {when && (
          <span
            className={cn(
              'min-w-8 shrink-0 text-right text-xs tabular-nums',
              entry.exists ? 'text-ink-3' : 'text-ink-faint line-through',
            )}
          >
            {when}
          </span>
        )}
      </button>
    </li>
  )
}
