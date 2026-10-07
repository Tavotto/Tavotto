import { useState, type KeyboardEvent, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import { ArrowDown, ArrowUp, Bookmark, ExternalLink, Folder, TriangleAlert, X } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { dropLineClass, listRowClass, rowMetaClass } from '@/components/ui/listRow'
import type { RecentProject } from '@/lib/api'
import { canRevealInFileManager, fileManagerKind, revealProjectFolder, type FileManagerKind } from '@/lib/desktop'
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
  const reveal = () =>
    void revealProjectFolder(path).then((ok) => {
      if (!ok) useUiStore.getState().setStatus(msg('workspace.revealFailed', { path }, 'project'), 'error')
    })
  return (
    <MenuItem data-workspace-reveal onSelect={reveal} icon={Folder}>
      {ws(REVEAL_LABEL[fileManagerKind()])}
    </MenuItem>
  )
}

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
 * * `density="drawer"`：44px（listRowClass md），名字 + 一行路径 / 辨认后缀，行上有收藏开关；
 * * `density="page"`：52px（lg），前面多一枚文件夹记号，选择器那一屏用。
 *
 * 菜单只有一份清单、三个入口（⋯ / 右键 / ⇧F10，`ui/RowMenu`）；收藏区的行可拖（落点 `dropLineClass`）、
 * ⌥↑ / ⌥↓ 与菜单里的上移 / 下移是拖动的键盘那条路。行的主按钮带 `data-roving`，一列一个 Tab 停靠点
 * （`left/rovingList`），Enter 打开。
 */
export function ProjectRow({
  entry,
  hint,
  density,
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
  density: 'drawer' | 'page'
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
  const drawer = density === 'drawer'
  const openable = entry.exists && !current && !busy && !switching
  const canNewTab = !!entry.id && !current
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
          <MenuItem disabled={order.index === 0} onSelect={() => order.move(-1)} icon={ArrowUp} shortcut={combo(ALT, '↑')}>
            {ws('moveUp')}
          </MenuItem>
          <MenuItem
            disabled={order.index >= order.count - 1}
            onSelect={() => order.move(1)}
            icon={ArrowDown}
            shortcut={combo(ALT, '↓')}
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
      data-workspace-row={drawer || undefined}
      data-picker-row={!drawer || undefined}
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
        listRowClass({ size: drawer ? 'md' : 'lg', selected: current }),
        'gap-2 pr-1',
        drawer ? 'pl-2' : 'mx-0 pl-2',
      )}
    >
      <span aria-hidden className={dropLineClass(drop)} />
      {!drawer && (
        <span
          aria-hidden
          className="flex size-8 shrink-0 items-center justify-center rounded-md bg-surface-hover text-ink-3"
        >
          <Folder size={ICON_SIZE.md} />
        </span>
      )}
      <button
        type="button"
        data-roving
        // 行里有打开 / 收藏 / 「…」几颗钮：「打开」自己带锚点，不靠在行里的次序
        data-workspace-open={drawer || undefined}
        data-picker-open={!drawer || undefined}
        onClick={onOpen}
        disabled={!openable}
        aria-current={current || undefined}
        aria-label={translate('picker.openProject', { ns: 'project', name: entry.name })}
        title={entry.tutorial ? undefined : entry.path}
        className="min-w-0 flex-1 self-stretch text-left outline-none focus-visible:focus-ring disabled:cursor-default"
      >
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
      </button>
      {/* 收藏开关就在行上：这是这个抽屉最常做的整理动作，不该藏进菜单第二层 */}
      {drawer && onTogglePin && (
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
