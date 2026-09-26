import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate } from '@/i18n'
import {
  ArrowDown,
  ArrowUp,
  Bookmark,
  Ellipsis,
  ExternalLink,
  Folder,
  FolderOpen,
  FolderPlus,
  SearchX,
  TriangleAlert,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { listRowClass } from '@/components/ui/listRow'
import { backendErrorMsg, type ProjectStatus, type RecentProject } from '@/lib/api'
import {
  canRevealInFileManager,
  fileManagerKind,
  revealProjectFolder,
  type FileManagerKind,
} from '@/lib/desktop'
import { disambiguateRecent, matchesRecent, splitRecent } from '@/lib/recentProjects'
import { cn } from '@/lib/utils'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { TailPath, useProjectEntry } from '../ProjectPicker'
import { Button, IconButton } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem, MenuSeparator, PointMenu } from '../ui/Menu'
import { SearchInput } from '../ui/SearchInput'

/** 本组文案在 project:workspace.* 下 */
const ws = (key: string, values?: Record<string, unknown>) =>
  translate(`workspace.${key}`, { ns: 'project', ...(values ?? {}) })

/** 带 pj 的地址在新标签页里认下那个项目（项目绑在标签页上，lib/session.ts） */
function openInNewTab(id: string) {
  window.open(`${location.pathname}?pj=${encodeURIComponent(id)}`, '_blank', 'noopener')
}

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
function RevealItem({ path }: { path: string }) {
  const reveal = () =>
    void revealProjectFolder(path).then((ok) => {
      if (!ok) useUiStore.getState().setStatus(msg('workspace.revealFailed', { path }, 'project'), 'error')
    })
  return (
    <MenuItem data-workspace-reveal onSelect={reveal}>
      <span className="flex items-center gap-2">
        <Folder size={ICON_SIZE.sm} className="text-ink-3" />
        {ws(REVEAL_LABEL[fileManagerKind()])}
      </span>
    </MenuItem>
  )
}

/**
 * 右键 = 在光标处开出与「…」同一份菜单（项目一份清单，两个入口）。
 * 菜单里一项都没有时不拦右键。
 */
function useRowContextMenu(enabled: boolean) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  const onContextMenu = enabled
    ? (e: React.MouseEvent) => {
        // 菜单本身 portal 在别处，React 事件却照着组件树冒上来：在菜单里右键不算「在行上右键」
        if (!e.currentTarget.contains(e.target as Node)) return
        e.preventDefault()
        setAt({ x: e.clientX, y: e.clientY })
      }
    : undefined
  return { at, onContextMenu, close: () => setAt(null) }
}

/**
 * 左栏「工作区」抽屉：当前项目 + 收藏 + 最近，切项目一次点击。
 *
 * 与 Project Picker 的分工：Picker 是「还没有项目时」的整屏入口（含教程、粘贴路径）；
 * 这里是「已经在干活时」换图库。两处的判据共用 `lib/recentProjects`（同名区分、
 * 筛选、失效分组），「打开文件夹 / 新建项目」共用 `useProjectEntry`。
 *
 * 顶栏的项目名按钮开的就是这个抽屉——同一份列表只在一处。
 */
export function WorkspaceList() {
  useTranslation('project')
  const project = useProjectStore((s) => s.project)
  const recent = useProjectStore((s) => s.recent)
  const pinned = useProjectStore((s) => s.pinned)
  // 切项目是串行的（projectStore 的切换队列）；切换期间所有「打开」入口一起置灰，
  // 不只是正在打开的那一行
  const switching = useProjectStore((s) => s.switching)
  const [query, setQuery] = useState('')
  const [busyPath, setBusyPath] = useState<string | null>(null)
  /** 拖动起点记**路径**：落下时按路径发 move，由后端查此刻的位置 */
  const dragFrom = useRef<string | null>(null)

  // 每次打开抽屉取一次最新的两份列表：别的标签页改过收藏、别处打开过项目，这里
  // 不会收到事件（收藏的改动按路径执行，旧列表点下去也不会盖掉别人的，但看到的该是新的）
  useEffect(() => {
    void useProjectStore.getState().refreshRecent()
  }, [])

  const go = async (path: string, create = false) => {
    setBusyPath(path)
    try {
      await useProjectStore.getState().open(path, create)
    } catch (e) {
      useUiStore.getState().setStatus(backendErrorMsg(e), 'error')
    } finally {
      setBusyPath(null)
    }
  }
  const entry = useProjectEntry((path, create) => void go(path, create), project?.figures_dir ?? undefined)

  const currentPath = project?.figures_dir ?? null
  const pinnedPaths = useMemo(() => new Set(pinned.map((p) => p.path)), [pinned])
  // 辨认后缀按收藏 + 最近的**整份**并集算：两个 figs 分在两个区里也仍然要分得开
  const hints = useMemo(() => {
    const seen = new Set<string>()
    return disambiguateRecent(
      [...pinned, ...recent].filter((e) => !seen.has(e.path) && (seen.add(e.path), true)),
    )
  }, [pinned, recent])

  const filtered = !!query.trim()
  const pinnedRows = pinned.filter((e) => matchesRecent(e, query))
  // 最近区不重复收藏里已有的、也不重复顶上的当前项目
  const { available, missing } = splitRecent(
    recent.filter((r) => !pinnedPaths.has(r.path) && r.path !== currentPath),
    query,
  )
  const nothing = pinnedRows.length + available.length + missing.length === 0

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-workspace-list>
      <div className="flex shrink-0 items-center gap-1.5 px-3 pb-2">
        <SearchInput
          value={query}
          onValueChange={setQuery}
          placeholder={ws('filter')}
          aria-label={ws('filterLabel')}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {project?.open && <CurrentProject project={project} pinned={pinnedPaths.has(currentPath ?? '')} />}

        {pinnedRows.length > 0 && (
          <Section id="pinned" label={ws('pinned')}>
            {pinnedRows.map((e) => {
              const index = pinned.indexOf(e)
              return (
                <ProjectRow
                  key={e.path}
                  entry={e}
                  hint={hints.get(e.path)}
                  busy={busyPath === e.path}
                  switching={switching}
                  current={e.path === currentPath}
                  pinned
                  onOpen={() => void go(e.path)}
                  // 筛选中索引对不上真实顺序：拖动与上移 / 下移都停用（同画布列表）
                  order={filtered ? undefined : { index, count: pinned.length, dragFrom }}
                />
              )
            })}
          </Section>
        )}

        {available.length + missing.length > 0 && (
          <Section id="recent" label={ws('recent')}>
            {[...available, ...missing].map((e) => (
              <ProjectRow
                key={e.path}
                entry={e}
                hint={hints.get(e.path)}
                busy={busyPath === e.path}
                switching={switching}
                current={e.path === currentPath}
                pinned={false}
                onOpen={() => void go(e.path)}
                onRemove={() => void useProjectStore.getState().remove(e.path)}
              />
            ))}
            {missing.length > 1 && (
              <li className="px-3 pt-1">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    void useProjectStore.getState().removeMany(missing.map((m) => m.path))
                  }
                >
                  {ws('removeMissing', { count: missing.length })}
                </Button>
              </li>
            )}
          </Section>
        )}

        {nothing &&
          (filtered ? (
            <EmptyState icon={SearchX} title={ws('noMatch')} />
          ) : (
            <p className="px-4 py-3 type-meta">{ws('emptyRecent')}</p>
          ))}
      </div>

      {/* 两个入口平级，都不是这一屏的主动作：不给填色（宪法：每个上下文最多一个填色主动作，
          而左栏这一格没有「那一个」） */}
      <div className="flex shrink-0 gap-1.5 border-t border-border px-3 py-2">
        <Button
          size="md"
          variant="secondary"
          className="flex-1"
          disabled={switching}
          onClick={entry.startOpen}
        >
          <FolderOpen size={ICON_SIZE.sm} />
          {ws('openFolder')}
        </Button>
        <Button
          size="md"
          variant="secondary"
          className="flex-1"
          disabled={switching}
          onClick={entry.startCreate}
        >
          <FolderPlus size={ICON_SIZE.sm} />
          {ws('newProject')}
        </Button>
      </div>
      {entry.dialogs}
    </div>
  )
}

function Section({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <section data-workspace-section={id} className="pt-2">
      <h3 className="type-section flex h-7 items-center px-3">{label}</h3>
      <ul aria-label={ws('listLabel', { section: label })}>{children}</ul>
    </section>
  )
}

/**
 * 顶上的当前项目：名字、路径、脚本数；项目级动作收在「…」里（在新标签页打开、
 * 接入状态、收藏）。它不是一个可点的切换目标——已经在这个项目里了。
 */
function CurrentProject({ project, pinned }: { project: ProjectStatus; pinned: boolean }) {
  useTranslation('project')
  const readOnly = project.settings?.allow_write_back === false
  const menu = useRowContextMenu(true)
  const items = (
    <>
      {project.figures_dir && (
        <MenuItem
          onSelect={() => void useProjectStore.getState().togglePin(project.figures_dir!)}
        >
          <span className="flex items-center gap-2">
            <Bookmark size={ICON_SIZE.sm} filled={pinned} className="text-ink-3" />
            {pinned ? ws('unpin') : ws('pin')}
          </span>
        </MenuItem>
      )}
      {project.id && (
        <MenuItem onSelect={() => openInNewTab(project.id!)}>
          <span className="flex items-center gap-2">
            <ExternalLink size={ICON_SIZE.sm} className="text-ink-3" />
            {ws('openInNewTab')}
          </span>
        </MenuItem>
      )}
      {project.figures_dir && canRevealInFileManager() && <RevealItem path={project.figures_dir} />}
      <MenuSeparator />
      <MenuItem onSelect={() => useUiStore.getState().setRegistryOpen(true)}>
        {ws('registry')}
      </MenuItem>
    </>
  )
  return (
    <section data-workspace-section="current" className="pt-1">
      <h3 className="type-section flex h-7 items-center px-3">{ws('current')}</h3>
      {/* 选中底（selected）上 ink-3 只有 4.38:1，元数据在这里升一档用 ink-2（axe 实测） */}
      <div
        data-workspace-current
        onContextMenu={menu.onContextMenu}
        className={cn(listRowClass({ selected: true }), 'h-auto gap-2 px-2 py-1.5')}
      >
        <div className="min-w-0 flex-1" aria-current="true">
          <span className="block truncate text-sm text-ink">{project.name}</span>
          {project.tutorial ? (
            <span className="block text-xs font-normal text-ink-2">
              {translate('picker.tutorialBadge', { ns: 'project' })}
            </span>
          ) : (
            project.figures_dir && <TailPath path={project.figures_dir} className="text-ink-2" />
          )}
          <span className="block text-xs font-normal text-ink-2">
            {ws('scriptCount', { count: project.scripts ?? 0 })}
            {readOnly && ws('readOnlySuffix')}
          </span>
        </div>
        <Menu
          width={200}
          align="end"
          trigger={
            <IconButton iconSize="sm" label={ws('currentActions')}>
              <Ellipsis size={ICON_SIZE.sm} className="text-ink-3" />
            </IconButton>
          }
        >
          {items}
        </Menu>
        {menu.at && (
          <PointMenu
            open
            onOpenChange={(open) => {
              if (!open) menu.close()
            }}
            at={menu.at}
            ariaLabel={ws('currentActions')}
            width={200}
          >
            {items}
          </PointMenu>
        )}
      </div>
    </section>
  )
}

function ProjectRow({
  entry,
  hint,
  busy,
  switching,
  current,
  pinned,
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
   * `entry.current`**：那是上一次刷新时按当时的 pj 算的，切换完成到刷新回来之间是旧的
   * （旧项目被置灰、真正的当前项目反而能点）。「最近」区排除当前项目用的也是同一个判据。
   */
  current: boolean
  pinned: boolean
  onOpen: () => void
  /** 只有最近区的行能「从列表移除」；收藏区的行取消收藏即可 */
  onRemove?: () => void
  /** 收藏区、且没在筛选：可拖动、可上移 / 下移（index / count 只用来决定按钮禁不禁用） */
  order?: {
    index: number
    count: number
    dragFrom: React.RefObject<string | null>
  }
}) {
  useTranslation('project')
  const openable = entry.exists && !current && !busy && !switching
  // 当前项目的行是选中底：ink-3 在上面过不了 4.5:1，元数据升一档（同顶上的当前卡片）
  const meta = current ? 'text-ink-2' : 'text-ink-3'
  const togglePin = () => void useProjectStore.getState().togglePin(entry.path)
  const moveBy = (delta: number) =>
    void useProjectStore.getState().movePinned(entry.path, { delta })
  const canNewTab = !!entry.id && !current
  const canReveal = entry.exists && canRevealInFileManager()
  // 菜单里一项都没有时不摆「…」、也不拦右键（筛选中的收藏行、且项目没开着、浏览器模式）
  const hasMenu = !!order || canNewTab || canReveal || !!onRemove
  const menu = useRowContextMenu(hasMenu)
  const items = (
    <>
      {/* 拖动只有鼠标能用：菜单里给键盘一条同样的路 */}
      {order && (
        <>
          <MenuItem disabled={order.index === 0} onSelect={() => moveBy(-1)}>
            <span className="flex items-center gap-2">
              <ArrowUp size={ICON_SIZE.sm} className="text-ink-3" />
              {ws('moveUp')}
            </span>
          </MenuItem>
          <MenuItem disabled={order.index >= order.count - 1} onSelect={() => moveBy(1)}>
            <span className="flex items-center gap-2">
              <ArrowDown size={ICON_SIZE.sm} className="text-ink-3" />
              {ws('moveDown')}
            </span>
          </MenuItem>
        </>
      )}
      {/* 新标签页要带 pj：只有后端已经打开着的项目才有 id */}
      {canNewTab && (
        <MenuItem onSelect={() => openInNewTab(entry.id!)}>
          <span className="flex items-center gap-2">
            <ExternalLink size={ICON_SIZE.sm} className="text-ink-3" />
            {ws('openInNewTab')}
          </span>
        </MenuItem>
      )}
      {canReveal && <RevealItem path={entry.path} />}
      {onRemove && (
        <>
          {(canNewTab || canReveal) && <MenuSeparator />}
          {/* 只动列表、不删磁盘：不用垃圾桶，也不标 danger */}
          <MenuItem onSelect={onRemove}>{ws('removeFromList')}</MenuItem>
        </>
      )}
    </>
  )

  return (
    <li
      data-workspace-row
      // 稳定的身份锚点（e2e / 诊断按它找行，不按可达名——名字会重复、会翻译）
      data-project-path={entry.path}
      data-project-tutorial={entry.tutorial || undefined}
      onContextMenu={menu.onContextMenu}
      draggable={!!order}
      onDragStart={() => {
        if (order) order.dragFrom.current = entry.path
      }}
      // 拖动取消 / 松在列表外时 onDrop 不会来：不清的话起点一直挂着，之后任何外部拖进来
      // 落在收藏行上的东西都会被当成「挪那一条」（Codex #550）
      onDragEnd={() => {
        if (order) order.dragFrom.current = null
      }}
      // 只接本列表内部发起的重排：起点为空 = 外部拖进来的，不 preventDefault（不接收）
      onDragOver={
        order
          ? (e) => {
              if (order.dragFrom.current != null) e.preventDefault()
            }
          : undefined
      }
      onDrop={() => {
        const from = order?.dragFrom.current
        if (order && from != null && from !== entry.path) {
          void useProjectStore.getState().movePinned(from, { toPath: entry.path })
        }
        if (order) order.dragFrom.current = null
      }}
      className={cn(
        // 失效行不整行压淡：那句红字要过 4.5:1，压到 45% 就过不了；只把名字降一档
        listRowClass({ selected: current }),
        'h-auto gap-2 px-2 py-1.5',
      )}
    >
      <button
        // 行里有打开 / 收藏 / 「…」几颗钮：「打开」自己带锚点，不靠在行里的次序
        data-workspace-open
        onClick={onOpen}
        disabled={!openable}
        aria-current={current || undefined}
        aria-label={translate('picker.openProject', { ns: 'project', name: entry.name })}
        title={entry.tutorial ? undefined : entry.path}
        className="min-w-0 flex-1 text-left outline-none focus-visible:focus-ring disabled:cursor-default"
      >
        <span className="flex items-center gap-1.5">
          <span className={cn('truncate text-sm', entry.exists ? 'text-ink' : 'text-ink-3')}>
            {entry.name}
          </span>
          {entry.opened && !current && (
            <span className={cn('shrink-0 text-xs font-normal', meta)}>{ws('openedElsewhere')}</span>
          )}
          {busy && (
            <span className={cn('shrink-0 text-xs font-normal', meta)}>
              {translate('picker.opening', { ns: 'project' })}
            </span>
          )}
        </span>
        {!entry.exists ? (
          <span className="flex items-center gap-1 text-xs font-normal text-danger">
            <TriangleAlert size={ICON_SIZE.xs} aria-hidden />
            {translate('picker.missingDir', { ns: 'project' })}
          </span>
        ) : entry.tutorial ? (
          <span className={cn('block text-xs font-normal', meta)}>
            {translate('picker.tutorialBadge', { ns: 'project' })}
          </span>
        ) : hint ? (
          <span className={cn('block truncate font-mono text-xs font-normal', meta)}>{hint}</span>
        ) : (
          <TailPath path={entry.path} className={meta} />
        )}
      </button>
      {/* 收藏开关就在行上：这是这个抽屉最常做的整理动作，不该藏进菜单第二层 */}
      <IconButton
        iconSize="sm"
        label={pinned ? ws('unpinLabel', { name: entry.name }) : ws('pinLabel', { name: entry.name })}
        tip={pinned ? ws('unpin') : ws('pin')}
        aria-pressed={pinned}
        data-workspace-pin
        onClick={togglePin}
        className={cn(
          'transition-opacity focus-visible:opacity-100 group-hover:opacity-100',
          pinned ? 'opacity-100' : 'opacity-0',
        )}
      >
        <Bookmark size={ICON_SIZE.sm} filled={pinned} className={pinned ? 'text-ink-2' : 'text-ink-3'} />
      </IconButton>
      {hasMenu && (
      <Menu
        width={180}
        align="end"
        trigger={
          <IconButton
            iconSize="sm"
            label={ws('rowActions', { name: entry.name })}
            className="opacity-0 focus-visible:opacity-100 group-hover:opacity-100"
          >
            <Ellipsis size={ICON_SIZE.sm} className="text-ink-3" />
          </IconButton>
        }
      >
        {items}
      </Menu>
      )}
      {menu.at && (
        <PointMenu
          open
          onOpenChange={(open) => {
            if (!open) menu.close()
          }}
          at={menu.at}
          ariaLabel={ws('rowActions', { name: entry.name })}
          width={180}
        >
          {items}
        </PointMenu>
      )}
    </li>
  )
}
