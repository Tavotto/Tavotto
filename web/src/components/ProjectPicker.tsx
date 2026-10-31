import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft,
  BookOpen,
  ChevronRight,
  CornerDownLeft,
  ExternalLink,
  FolderOpen,
  FolderPlus,
  Search,
  TriangleAlert,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { backendErrorText, type RecentProject } from '@/lib/api'
import { AppMenuButton } from './AppMenuButton'
import { ContactButton } from './contact/ContactButton'
import { useRovingList } from './left/rovingList'
import { ProjectIndexRow, revealLabel, revealProject } from './ProjectPickerRow'
import {
  canRevealInFileManager,
  dragRegionProps,
  isDesktop,
  pickDirectory,
  WINDOW_CONTROLS_INSET,
  windowChrome,
} from '@/lib/desktop'
import { formatRelativeTime, formatShortWhen } from '@/i18n/format'
import {
  homeVariant,
  runTutorialEntry,
  tutorialEntry,
} from '@/lib/onboarding/tutorial'
import { disambiguateRecent, looksLikePath, splitRecent, submitTargetFor } from '@/lib/recentProjects'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { DirBrowser, NewProjectNameDialog, TailPath } from './DirBrowser'
import { HomeView } from './home/HomeView'
import { Button } from './ui/Button'
import { Card } from './ui/Card'
import { EmptyState } from './ui/EmptyState'
import { FIELD_BOX, FIELD_FOCUS_WITHIN } from './ui/fieldBox'
import { TooltipProvider } from './ui/Tooltip'

export { DirBrowser, TailPath }

/**
 * Project Picker：没有打开的项目时替代整个工作台。
 * 项目 = 论文图所在的目录（figures 目录）；打开即切换本标签页的项目。
 *
 * 两个视图共用「打开」这一份状态（出错信息、哪一条正在打开）：
 *   * **主页**（默认，`home/HomeView`）——新手版 / 老手版，按 `homeVariant()` 分；
 *   * **全部项目**（主页的「浏览更多项目 / 更多 / 其他打开方式」进来）——下面的 `AllProjects`：
 *     新建、打开文件夹、粘贴路径 / 筛选、完整最近列表、失效目录、教程入口的三态。
 * 视图是这一屏的临时状态，不持久：每次回到选择器都从主页开始。
 */
export function ProjectPicker() {
  const open = useProjectStore((s) => s.open)
  const variant = useOnboardingStore((s) => homeVariant(s.status))
  const [view, setView] = useState<'home' | 'all'>('home')
  const [error, setError] = useState<string | null>(null)
  const [busyPath, setBusyPath] = useState<string | null>(null)

  const openPath = async (path: string, create = false): Promise<boolean> => {
    setError(null)
    setBusyPath(path)
    try {
      await open(path, create)
      return true
    } catch (e) {
      setError(backendErrorText(e))
      return false
    } finally {
      setBusyPath(null)
    }
  }

  // 选择器不在工作台的 TooltipProvider 里（App 在没有项目时只渲染这一屏）：行的「⋯」与 Windows 桌面版
  // 左上角的「菜单」按钮都是带气泡的 IconButton，主页与「全部项目」两个视图都要有这一层
  // （只给「全部项目」包过：Windows 桌面版主页因 AppMenuButton 直接崩到「界面出错了」，#1080）
  return (
    <TooltipProvider>
      {/* 联系作者（ADR 0145）：**窗口**右上角（固定定位，不跟内容列、不随主页滚动），与左上角的应用菜单对称；两个视图（主页的新手版 / 老手版、全部项目）
          共用这一个。Windows 的窗口按钮压在最右 138px，按钮让出去 */}
      <div
        data-contact-corner
        className="fixed top-3 z-sticky"
        style={{ right: windowChrome() === 'menu-button' ? WINDOW_CONTROLS_INSET + 12 : 12 }}
      >
        <ContactButton placement="home" />
      </div>
      {view === 'home' ? (
        <HomeView
          variant={variant}
          error={error}
          busyPath={busyPath}
          openPath={(path) => openPath(path)}
          onShowAll={() => {
            setError(null)
            setView('all')
          }}
        />
      ) : (
        <AllProjects
          error={error}
          busyPath={busyPath}
          openPath={openPath}
          onBack={() => {
            setError(null)
            setView('home')
          }}
        />
      )}
    </TooltipProvider>
  )
}

/**
 * 「全部项目」：手帐索引页（2026-10-28 redesign-3 A3，master–detail）。
 *
 * ### 版式
 *
 * 返回主页、标题 + 数量、**一个**框（筛选 / 粘路径）固定在上方；下面是左右两栏：
 * 左边是索引（「•」记号 · 名字 · 虚点引线 · 短日期，目录没了的是「×」+ 删除线，末尾一行「已不存在 N · 全部移除」），
 * 右边是选中那个项目的详情卡（缩略图占位、名字、路径、上次打开、一颗黑色「打开」，两个静默链接）。
 * **只有索引 + 详情那一块自己滚**（审计 T02：二十条最近项目不许把输入框推出视口）；窄于 640px 详情叠到索引下面。
 *
 * ### 一个框两件事
 *
 * 输入名字就筛最近列表；输入绝对路径（`/`、`~`、`C:\`）就打开那条路径（判据在 `lib/recentProjects.submitTargetFor`）：
 * 路径态下框尾的「新建项目 · 浏览…」换成「打开 ↵」。回车：路径 / 唯一匹配项照旧；其余情况打开当前选中的那一个。
 *
 * ### 选中
 *
 * 默认选第一个；单击或键盘聚焦选中；双击 / 回车打开。↑↓ 是索引的漫游焦点（`useRovingList`，整列一个 Tab 停靠点）。
 * 同名项目的辨认后缀跟在名字后面，目录不存在的项目选中后详情卡的主按钮是「从列表移除」。
 */
function AllProjects({
  error,
  busyPath,
  openPath,
  onBack,
}: {
  error: string | null
  busyPath: string | null
  openPath: (path: string, create?: boolean) => Promise<boolean>
  onBack: () => void
}) {
  const { t } = useTranslation('project')
  const recent = useProjectStore((s) => s.recent)
  const remove = useProjectStore((s) => s.remove)
  const removeMany = useProjectStore((s) => s.removeMany)
  // 从设置「切换项目」进来时后端仍有打开的项目——允许原路返回
  const currentOpen = useProjectStore((s) => s.project?.open === true)
  // 切项目是串行的；切换期间这一屏所有「打开 / 新建 / 返回」一起置灰（Codex #550）
  const switching = useProjectStore((s) => s.switching)
  const [typed, setTyped] = useState('')
  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  const { available, missing } = useMemo(() => splitRecent(recent, typed), [recent, typed])
  // 辨认后缀按**整份**列表算：筛选到只剩一个 figs 时它仍是六个里的那一个
  const hints = useMemo(() => disambiguateRecent(recent), [recent])
  const target = submitTargetFor(typed, available)
  const pathMode = looksLikePath(typed)
  const entry = useProjectEntry((path, create) => void openPath(path, create))
  const roving = useRovingList<HTMLDivElement>()
  const listRef = roving.ref

  // 选中的那一个：用户点过的还在列表里就是它，否则第一个（筛选把它筛掉了也回到第一个）
  const shown = useMemo(() => [...available, ...missing], [available, missing])
  const selected = shown.find((r) => r.path === selectedPath) ?? shown[0] ?? null
  const now = Date.now()
  const whenOf = (r: RecentProject) => (r.last_opened > 0 ? formatShortWhen(r.last_opened, now) : undefined)

  const openSelected = () => {
    if (selected?.exists) void openPath(selected.path)
  }

  return (
    // 窗框：macOS 下空白处可拖动窗口（红绿灯在左上角，这一列居中、碰不到它们）
    <div className="relative flex h-full justify-center bg-bg" {...dragRegionProps('self')}>
      {/* Windows 的应用菜单：与首页同一个位置（左上角） */}
      <AppMenuButton className="absolute left-2 top-3" />
      <main
        aria-label={t('picker.regionLabel')}
        className="flex h-full w-[720px] max-w-[calc(100vw-3rem)] flex-col"
        {...dragRegionProps('self')}
      >
        {/* 固定区：返回 · 标题 · 框。`shrink-0` 是这一屏的要点——列表再长也挤不动它 */}
        <header className="shrink-0 pt-3" {...dragRegionProps('self')}>
          <Button size="md" className="-ml-2.5 text-ink-2" data-home-back onClick={onBack}>
            <ArrowLeft size={ICON_SIZE.sm} />
            {t('home.backHome')}
          </Button>
          <div className="mb-3 mt-4 flex items-baseline gap-2">
            <h1 className="type-heading">{t('picker.title')}</h1>
            {/* 计数是 type-meta（等宽数字），不是等宽字体（审计 §10.3） */}
            {shown.length > 0 && <span className="type-meta tabular-nums">{t('picker.recentCount', { count: shown.length })}</span>}
            {currentOpen && (
              <Button
                size="md"
                className="ml-auto text-ink-3"
                disabled={switching}
                onClick={() => useProjectStore.getState().returnToCurrent()}
              >
                {t('picker.backToCurrent')}
                <ChevronRight size={ICON_SIZE.xs} aria-hidden />
              </Button>
            )}
          </div>

          {/* 一个框两件事：粘贴路径直接打开（从文件管理器复制过来永远比一层层点快），
              输入名字就筛最近项目；回车打开路径、唯一匹配项，或当前选中的那一个 */}
          <form
            role="search"
            onSubmit={(e) => {
              e.preventDefault()
              if (switching) return
              if (target) void openPath(target.kind === 'path' ? target.path : target.entry.path)
              else if (!pathMode) openSelected()
            }}
          >
            <div
              className={cn(
                FIELD_BOX,
                FIELD_FOCUS_WITHIN,
                'flex h-9 items-center gap-2 pl-3 pr-1 text-base',
              )}
            >
              <Search size={ICON_SIZE.md} aria-hidden className="shrink-0 text-ink-3" />
              <input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                onKeyDown={(e) => {
                  // 从框里 ↓ 直接落到索引（选中的那一行）
                  if (e.key !== 'ArrowDown' || e.altKey || e.metaKey || e.ctrlKey) return
                  const row =
                    listRef.current?.querySelector<HTMLElement>('[data-selected] [data-roving]:not(:disabled)') ??
                    listRef.current?.querySelector<HTMLElement>('[data-roving]:not(:disabled)')
                  if (!row) return
                  e.preventDefault()
                  row.focus()
                }}
                placeholder={t('picker.pathPlaceholder')}
                aria-label={t('picker.pathLabel')}
                className="h-full min-w-0 flex-1 bg-transparent text-base text-ink outline-none placeholder:text-ink-3"
                spellCheck={false}
                autoComplete="off"
              />
              {pathMode ? (
                <Button
                  type="submit"
                  size="md"
                  className="shrink-0 text-ink-2"
                  disabled={!target || switching}
                  aria-label={
                    target?.kind === 'recent' ? t('picker.openMatch', { name: target.entry.name }) : undefined
                  }
                >
                  <CornerDownLeft size={ICON_SIZE.sm} aria-hidden />
                  {t('picker.openButton')}
                </Button>
              ) : (
                <span className="flex shrink-0 items-center gap-0.5">
                  {/* 静默钮（ghost）：一屏的黑色填充钮只留给详情卡的「打开」；窄于 560px 只剩图标，文字留给读屏 */}
                  <Button size="md" className="text-ink-2" disabled={switching} onClick={entry.startCreate}>
                    <FolderPlus size={ICON_SIZE.md} aria-hidden />
                    <span className="max-[559px]:sr-only">{t('picker.create')}</span>
                  </Button>
                  <span aria-hidden className="h-4 w-px bg-border" />
                  <Button size="md" className="text-ink-2" disabled={switching} onClick={entry.startOpen}>
                    <FolderOpen size={ICON_SIZE.md} aria-hidden />
                    <span className="max-[559px]:sr-only">{t('picker.browse')}</span>
                  </Button>
                </span>
              )}
            </div>
          </form>

          {error && (
            <p role="alert" className="mt-2 text-xs leading-relaxed text-danger">
              {error}
            </p>
          )}
        </header>

        {recent.length > 0 ? (
          <section aria-label={t('picker.recentLabel')} className="flex min-h-0 flex-1 flex-col pt-5">
            {/* 只有这一块滚：`min-h-0` 让它在 flex 列里真的能收缩，否则它会把
                自己撑到内容高度、把滚动交还给页面（= 改造前的样子）。-mx-2 / px-2：给行的悬停底留出左右各 8px 而不被裁掉 */}
            <div
              data-recent-scroll
              ref={roving.ref}
              onFocus={roving.onFocus}
              onKeyDown={roving.onKeyDown}
              className="-mx-2 min-h-0 flex-1 overflow-y-auto px-2 pb-4"
            >
              {shown.length === 0 ? (
                // 路径态下列表本来就筛空了（路径不是名字）：不说「没有匹配」，框尾的「打开 ↵」就是下一步
                !pathMode && (
                  <EmptyState art="search" title={t('picker.filterNoMatch', { query: typed.trim() })} data-picker-no-match />
                )
              ) : (
                <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-[248px_minmax(0,1fr)]">
                  <div className="-mx-2 min-w-0">
                    {/* 一列一个 Tab 停靠点，↑↓ 走行、Enter 打开（行是 `ProjectIndexRow`） */}
                    <ul className="flex flex-col">
                      {available.map((r) => (
                        <ProjectIndexRow
                          key={r.path}
                          entry={r}
                          hint={hints.get(r.path)}
                          when={whenOf(r)}
                          selected={selected?.path === r.path}
                          busy={busyPath === r.path}
                          switching={switching}
                          onSelect={() => setSelectedPath(r.path)}
                          onOpen={() => void openPath(r.path)}
                        />
                      ))}
                    </ul>
                    {missing.length > 0 && (
                      <section aria-label={t('picker.missingGroup')} className="mt-4">
                        <div className="mb-1 flex items-center justify-between gap-2 px-2 text-xs text-ink-3">
                          <span className="tabular-nums">{t('picker.missingCount', { count: missing.length })}</span>
                          <button
                            type="button"
                            onClick={() => void removeMany(missing.map((r) => r.path))}
                            className="rounded-xs text-ink-2 underline underline-offset-2 outline-none hover:text-ink focus-visible:focus-ring"
                          >
                            {t('picker.removeAllMissing')}
                          </button>
                        </div>
                        <ul className="flex flex-col">
                          {missing.map((r) => (
                            <ProjectIndexRow
                              key={r.path}
                              entry={r}
                              hint={hints.get(r.path)}
                              when={whenOf(r)}
                              selected={selected?.path === r.path}
                              busy={false}
                              switching={false}
                              onSelect={() => setSelectedPath(r.path)}
                              onOpen={() => {}}
                            />
                          ))}
                        </ul>
                      </section>
                    )}
                  </div>
                  {selected && (
                    <ProjectDetail
                      key={selected.path}
                      entry={selected}
                      opened={selected.last_opened > 0 ? formatRelativeTime(selected.last_opened) : null}
                      busy={busyPath === selected.path}
                      switching={switching}
                      onOpen={() => void openPath(selected.path)}
                      onRemove={() => void remove(selected.path)}
                    />
                  )}
                </div>
              )}
            </div>
          </section>
        ) : (
          <div className="min-h-0 flex-1" />
        )}

        <TutorialEntry />
        {entry.dialogs}
      </main>
    </div>
  )
}

/**
 * 详情卡（索引页右栏）：选中项目的「翻开页」——缩略图占位（最近列表不带画布数据，画不出缩略图：用 `.hatch-box`）、
 * 名字、路径（教程副本只说「教程项目」）、上次打开；一颗黑色「打开」，旁边两个静默链接：
 * 「在 Finder 中打开」（只有桌面壳做得到，浏览器模式不摆）与「从列表移除」（只动列表、不删磁盘）。
 * 目录已不存在的项目没有可打开的东西：主按钮换成「从列表移除」，没有别的动作。
 */
function ProjectDetail({
  entry,
  opened,
  busy,
  switching,
  onOpen,
  onRemove,
}: {
  entry: RecentProject
  /** 「3 小时前」；没有记录时缺席 */
  opened: string | null
  busy: boolean
  switching: boolean
  onOpen: () => void
  onRemove: () => void
}) {
  const { t } = useTranslation('project')
  const canReveal = entry.exists && canRevealInFileManager()
  return (
    <Card data-picker-detail padding="none" className="min-w-0 sm:sticky sm:top-0">
      <div className="p-1">
        <span
          aria-hidden
          data-gone={!entry.exists || undefined}
          className="hatch-box h-37 w-full rounded-md"
        />
      </div>
      <div className="px-3 pb-4 pt-3.5">
        <h2 className="type-heading truncate" title={entry.name}>
          {entry.name}
        </h2>
        <p className="mt-0.5 select-text text-xs text-ink-3 [overflow-wrap:anywhere]">
          {entry.tutorial ? t('picker.tutorialBadge') : entry.path}
        </p>
        {!entry.exists ? (
          <p className="flex items-center gap-1 text-xs text-danger-content">
            <TriangleAlert size={ICON_SIZE.xs} aria-hidden />
            {t('picker.missingDir')}
          </p>
        ) : (
          opened && <p className="text-xs tabular-nums text-ink-3">{t('home.openedAgo', { when: opened })}</p>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-x-1 gap-y-2">
          {entry.exists ? (
            <Button
              variant="primary"
              size="lg"
              data-picker-detail-open
              disabled={busy || switching}
              onClick={onOpen}
            >
              {busy ? t('picker.opening') : t('picker.openButton')}
            </Button>
          ) : (
            <Button variant="primary" size="lg" data-picker-detail-remove title={t('picker.removeFromListTitle')} onClick={onRemove}>
              {t('picker.removeFromList')}
            </Button>
          )}
          <span className="flex-1" />
          {canReveal && (
            <Button size="md" className="text-ink-2" data-picker-detail-reveal onClick={() => revealProject(entry.path)}>
              <ExternalLink size={ICON_SIZE.sm} aria-hidden />
              {revealLabel()}
            </Button>
          )}
          {entry.exists && (
            <Button
              size="md"
              className="text-ink-2"
              data-picker-detail-remove
              title={t('picker.removeFromListTitle')}
              onClick={onRemove}
            >
              {t('picker.removeFromList')}
            </Button>
          )}
        </div>
      </div>
    </Card>
  )
}

/**
 * 「打开文件夹」「新建项目」两个入口的共同流程——Project Picker 与左栏「工作区」
 * 抽屉共用这一份，别再各写一遍。
 *
 * 桌面壳：系统目录选择器（取消不是错误，什么都不发生）；新建时选的是上级目录，
 * 再只问一个名字（审计 T03）。浏览器模式回退到服务器端目录浏览器（浏览的是
 * 后端所在机器的磁盘）。调用方把 `dialogs` 摆进自己的树里。
 */
export function useProjectEntry(
  openPath: (path: string, create?: boolean) => void,
  initialPath?: string,
) {
  const { t } = useTranslation('project')
  const [browse, setBrowse] = useState<null | 'open' | 'create'>(null)
  /** 桌面壳：系统选择器选好的上级目录，等用户起个名字 */
  const [createIn, setCreateIn] = useState<string | null>(null)

  const startOpen = () => {
    if (isDesktop()) {
      void pickDirectory(t('picker.nativePickerTitle')).then((dir) => {
        if (dir) openPath(dir)
      })
    } else setBrowse('open')
  }
  const startCreate = () => {
    if (isDesktop()) {
      void pickDirectory(t('picker.nativeCreateTitle')).then((dir) => {
        if (dir) setCreateIn(dir)
      })
    } else setBrowse('create')
  }

  const dialogs = (
    <>
      {browse && (
        <DirBrowser
          mode={browse}
          initialPath={initialPath}
          onClose={() => setBrowse(null)}
          onPick={(path, create) => {
            setBrowse(null)
            openPath(path, create)
          }}
        />
      )}
      {createIn && (
        <NewProjectNameDialog
          parent={createIn}
          onClose={() => setCreateIn(null)}
          onCreate={(name) => {
            setCreateIn(null)
            openPath(`${createIn}/${name}`, true)
          }}
        />
      )}
    </>
  )
  return { startOpen, startCreate, dialogs }
}

/**
 * 新手教程的一行入口（ADR 0040；三步原生教程 ADR 0136）。教程是内置演示、不依赖后端的
 * 教程资源，所以总是可用；切项目进行中时置灰（教程层会盖住正在切换的界面）。
 * 点下去的全部逻辑在 `lib/onboarding/tutorial.ts`，这里只显示结果。
 * 索引页上它是页脚的一条静默文字链（start / resume / restart 三态不变），不再带说明句。
 */
function TutorialEntry() {
  const { t } = useTranslation('project')
  const entry = useOnboardingStore((s) => tutorialEntry(s.status))
  const switching = useProjectStore((s) => s.switching)
  return (
    <section aria-label={t('picker.tutorialLabel')} className="flex shrink-0 justify-center pb-6 pt-1">
      <Button
        variant="ghost"
        size="md"
        className="text-ink-3"
        disabled={switching}
        data-onboarding-anchor="tutorial-entry"
        onClick={() => void runTutorialEntry('picker')}
      >
        <BookOpen size={ICON_SIZE.md} />
        {t(`picker.tutorial.${entry}`)}
      </Button>
    </section>
  )
}
