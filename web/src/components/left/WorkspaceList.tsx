import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { Bookmark, ClipboardList, ExternalLink, FolderOpen, FolderPlus, SearchX } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { backendErrorMsg, type ProjectStatus } from '@/lib/api'
import { canRevealInFileManager } from '@/lib/desktop'
import { disambiguateRecent, matchesRecent, splitRecent } from '@/lib/recentProjects'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { TailPath, useProjectEntry } from '../ProjectPicker'
import { BookmarkFilled, ProjectRow, RevealItem } from '../ProjectPickerRow'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { EmptyState } from '../ui/EmptyState'
import { MenuItem, MenuSeparator } from '../ui/Menu'
import { RowMenu } from '../ui/RowMenu'
import { SearchInput } from '../ui/SearchInput'
import { useRowMenu } from '../ui/useRowMenu'
import { openInNewTab } from './projectActions'
import { useRovingList } from './rovingList'

/** 本组文案在 project:workspace.* 下 */
const ws = (key: string, values?: Record<string, unknown>) =>
  translate(`workspace.${key}`, { ns: 'project', ...(values ?? {}) })

/**
 * 左栏「项目」抽屉：当前项目 + 收藏 + 最近，切项目一次点击。
 *
 * 与 Project Picker 的分工：Picker 是「还没有项目时」的整屏入口（含教程、粘贴路径）；
 * 这里是「已经在干活时」换图库。两处的判据共用 `lib/recentProjects`（同名区分、
 * 筛选、失效分组），「打开文件夹 / 新建项目」共用 `useProjectEntry`，**行共用 `ProjectRow`**
 * （`ProjectPickerRow.tsx`，2026-10-07 设计审计 §10.3：这里 `density="drawer"`）。
 *
 * 当前项目是顶上一张 subtle 卡（不是一行选中行），**不再出现在收藏里**——此前它被收藏时一屏画两次选中。
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
  const roving = useRovingList<HTMLDivElement>()

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

  const currentPath = project?.open ? (project.figures_dir ?? null) : null
  const pinnedPaths = useMemo(() => new Set(pinned.map((p) => p.path)), [pinned])
  // 辨认后缀按收藏 + 最近的**整份**并集算：两个 figs 分在两个区里也仍然要分得开
  const hints = useMemo(() => {
    const seen = new Set<string>()
    return disambiguateRecent(
      [...pinned, ...recent].filter((e) => !seen.has(e.path) && (seen.add(e.path), true)),
    )
  }, [pinned, recent])

  const filtered = !!query.trim()
  // 当前项目只在顶上的卡里：收藏区与最近区都不重复它
  const pinnedRows = pinned.filter((e) => e.path !== currentPath && matchesRecent(e, query))
  const { available, missing } = splitRecent(
    recent.filter((r) => !pinnedPaths.has(r.path) && r.path !== currentPath),
    query,
  )
  const nothing = pinnedRows.length + available.length + missing.length === 0
  // 排序按**显示出来的**收藏算（Codex #832）：当前项目被收藏时它藏在顶上的卡里，下标、两端判据与
  // 「上移 / 下移」都只认可见的那几行——不然紧挨着它的那一行第一下是跟看不见的它换位，界面上没动静，
  // 可见的首尾行也还亮着上移 / 下移。不筛选时 `pinnedRows` 就是这份显示顺序
  const pinnedIndex = (path: string) => pinnedRows.findIndex((p) => p.path === path)
  /**
   * 与可见的相邻一行换位（跳过藏起来的当前项目）。此刻没有邻居就不排；有的话邻居留给
   * 收藏队列**轮到执行时**按最新列表再找——连按两下 ⌥↓ 是挪两格，不是挪过去又挪回来（Codex #832）
   */
  const movePinnedBy = (path: string, delta: number) => {
    if (!pinnedRows[pinnedIndex(path) + delta]) return
    void useProjectStore.getState().movePinned(path, { step: delta < 0 ? -1 : 1, skip: currentPath })
  }
  const togglePin = (path: string) => void useProjectStore.getState().togglePin(path)

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-workspace-list>
      {/* 搜索行只放搜索（审计 §10.3） */}
      <div className="flex shrink-0 items-center px-3 pb-2">
        <SearchInput value={query} onValueChange={setQuery} placeholder={ws('filter')} aria-label={ws('filterLabel')} />
      </div>

      <div
        ref={roving.ref}
        onFocus={roving.onFocus}
        onKeyDown={roving.onKeyDown}
        className="min-h-0 flex-1 overflow-y-auto pb-2"
      >
        {project?.open && <CurrentProject project={project} pinned={pinnedPaths.has(currentPath ?? '')} />}

        {pinnedRows.length > 0 && (
          <Section id="pinned" label={ws('pinned')} count={pinnedRows.length}>
            {pinnedRows.map((e) => {
              const index = pinnedIndex(e.path)
              return (
                <ProjectRow
                  key={e.path}
                  entry={e}
                  hint={hints.get(e.path)}
                  density="drawer"
                  busy={busyPath === e.path}
                  switching={switching}
                  pinned
                  onTogglePin={() => togglePin(e.path)}
                  onOpen={() => void go(e.path)}
                  // 筛选中索引对不上真实顺序：拖动与上移 / 下移都停用（同画布列表）
                  order={
                    filtered
                      ? undefined
                      : {
                          index,
                          count: pinnedRows.length,
                          dragFrom,
                          indexOf: pinnedIndex,
                          move: (delta) => movePinnedBy(e.path, delta),
                          moveTo: (from) => void useProjectStore.getState().movePinned(from, { toPath: e.path }),
                        }
                  }
                />
              )
            })}
          </Section>
        )}

        {available.length + missing.length > 0 && (
          <Section id="recent" label={ws('recent')} count={available.length + missing.length}>
            {[...available, ...missing].map((e) => (
              <ProjectRow
                key={e.path}
                entry={e}
                hint={hints.get(e.path)}
                density="drawer"
                busy={busyPath === e.path}
                switching={switching}
                pinned={false}
                onTogglePin={() => togglePin(e.path)}
                onOpen={() => void go(e.path)}
                onRemove={() => void useProjectStore.getState().remove(e.path)}
              />
            ))}
            {missing.length > 1 && (
              <li className="px-3 pt-1" data-workspace-remove-missing>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void useProjectStore.getState().removeMany(missing.map((m) => m.path))}
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

      {/* 名词说明（2026-09-26 用户拍板四个名词，ADR 0001 修订）：项目 ≠ 排版。一行 meta
          字压在两个入口上方，不占列表的位置 */}
      <p className="shrink-0 px-3 pb-1.5 type-meta" data-workspace-hint>
        {ws('hint')}
      </p>

      {/* 页脚（左栏只有一种页脚语法：border-t px-1.5 py-1 + 28px 控件，底是抽屉底）。两个入口平级，
          都不是这一屏的主动作：不给填色（宪法：每个上下文最多一个填色主动作） */}
      <div
        data-workspace-footer
        className="flex shrink-0 gap-1 border-t border-border bg-[var(--drawer-bg,var(--color-surface))] px-1.5 py-1"
      >
        <Button size="md" variant="ghost" className="flex-1" disabled={switching} onClick={entry.startOpen}>
          <FolderOpen size={ICON_SIZE.sm} />
          {ws('openFolder')}
        </Button>
        <Button size="md" variant="ghost" className="flex-1" disabled={switching} onClick={entry.startCreate}>
          <FolderPlus size={ICON_SIZE.sm} />
          {ws('newProject')}
        </Button>
      </div>
      {entry.dialogs}
    </div>
  )
}

/** 子节头：28px type-section + type-meta 计数（左栏各抽屉同一种写法，审计 §10.3） */
function Section({
  id,
  label,
  count,
  children,
}: {
  id: string
  label: string
  count: number
  children: React.ReactNode
}) {
  return (
    <section data-workspace-section={id} className="pt-2">
      <h3 className="flex h-7 items-center gap-1.5 px-3">
        <span className="type-section">{label}</span>
        <span className="type-meta">{count}</span>
      </h3>
      <ul aria-label={ws('listLabel', { section: label })}>{children}</ul>
    </section>
  )
}

/**
 * 顶上的当前项目：一张 subtle 卡（2026-10-07 审计 §10.3）——名字、路径、脚本数；项目级动作收在「⋯」里
 * （收藏、在新标签页打开、在文件管理器中打开、接入状态），右键 / ⇧F10 开同一份。⋯ 常驻 Tab 顺序（`tabbable`）。
 * 它不是一个可点的切换目标——已经在这个项目里了。
 */
function CurrentProject({ project, pinned }: { project: ProjectStatus; pinned: boolean }) {
  useTranslation('project')
  const readOnly = project.settings?.allow_write_back === false
  const menu = useRowMenu()
  return (
    <section data-workspace-section="current" className="px-2 pt-1">
      <h3 className="flex h-7 items-center px-1">
        <span className="type-section">{ws('current')}</span>
      </h3>
      <Card
        appearance="subtle"
        padding="sm"
        data-workspace-current
        {...menu.rowProps}
        tabIndex={-1}
        className="group flex items-start gap-2 outline-none"
      >
        <div className="min-w-0 flex-1 pl-1" aria-current="true">
          <span className="block truncate text-sm font-medium text-ink">{project.name}</span>
          {project.tutorial ? (
            <span className="block type-meta text-ink-2">{translate('picker.tutorialBadge', { ns: 'project' })}</span>
          ) : (
            project.figures_dir && <TailPath path={project.figures_dir} className="text-ink-3" />
          )}
          <span className="block type-meta">
            {ws('scriptCount', { count: project.scripts ?? 0 })}
            {readOnly && ws('readOnlySuffix')}
          </span>
        </div>
        {/* 卡本身 tabIndex -1（不是切换目标）：⋯ 常驻 Tab 顺序，是它唯一的键盘入口（Codex #832） */}
        <RowMenu state={menu} label={ws('currentActions')} visible="always" tabbable width={220}>
          {project.figures_dir && (
            <MenuItem
              icon={pinned ? BookmarkFilled : Bookmark}
              onSelect={() => void useProjectStore.getState().togglePin(project.figures_dir!)}
            >
              {pinned ? ws('unpin') : ws('pin')}
            </MenuItem>
          )}
          {project.id && (
            <MenuItem onSelect={() => openInNewTab(project.id!)} icon={ExternalLink}>
              {ws('openInNewTab')}
            </MenuItem>
          )}
          {/* 目录被删 / 卷被卸下时当前项目仍是 open，只是 exists 为 false：与行同一个判据，不给这一项 */}
          {project.figures_dir && project.exists !== false && canRevealInFileManager() && (
            <RevealItem path={project.figures_dir} />
          )}
          <MenuSeparator />
          <MenuItem icon={ClipboardList} onSelect={() => useUiStore.getState().setRegistryOpen(true)}>
            {ws('registry')}
          </MenuItem>
        </RowMenu>
      </Card>
    </section>
  )
}
