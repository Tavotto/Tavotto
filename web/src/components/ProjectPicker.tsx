import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft,
  BookOpen,
  ChevronRight,
  CornerDownLeft,
  FolderOpen,
  FolderPlus,
  SearchX,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { backendErrorText, type RecentProject } from '@/lib/api'
import { AppMenuButton } from './AppMenuButton'
import { ContactButton } from './contact/ContactButton'
import { useRovingList } from './left/rovingList'
import { ProjectRow } from './ProjectPickerRow'
import { dragRegionProps, isDesktop, pickDirectory, WINDOW_CONTROLS_INSET, windowChrome } from '@/lib/desktop'
import { PRODUCT_NAME } from '@/lib/brand'
import {
  homeVariant,
  runTutorialEntry,
  tutorialEntry,
} from '@/lib/onboarding/tutorial'
import { disambiguateRecent, splitRecent, submitTargetFor } from '@/lib/recentProjects'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { DirBrowser, NewProjectNameDialog, TailPath } from './DirBrowser'
import { HomeView } from './home/HomeView'
import { BrandMark } from './ui/BrandMark'
import { Button } from './ui/Button'
import { EmptyState } from './ui/EmptyState'
import { TextInput } from './ui/Input'
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
 * 「全部项目」：改版前的 Project Picker 原样搬过来，多一颗「返回主页」。
 *
 * ### 版式（审计 T02）
 *
 * 品牌、新建 / 浏览 / 路径输入、教程入口固定在上方，**只有最近项目那一栏
 * 自己滚**：改造前整页一起滚，二十条最近项目就把新建 / 打开推出了视口，
 * 而这些正是用户来这一屏要按的东西。
 *
 * 路径输入框兼作筛选：输入名字就过滤最近列表，输入绝对路径就打开那条路径
 * （判据在 `lib/recentProjects.submitTargetFor`）。同名项目带可辨认的父目录，
 * 已不存在的目录收进一组，各自能移除、也能一次全移除。
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

  const { available, missing } = useMemo(() => splitRecent(recent, typed), [recent, typed])
  // 辨认后缀按**整份**列表算：筛选到只剩一个 figs 时它仍是六个里的那一个
  const hints = useMemo(() => disambiguateRecent(recent), [recent])
  const target = submitTargetFor(typed, available)
  const entry = useProjectEntry((path, create) => void openPath(path, create))
  const roving = useRovingList<HTMLDivElement>()

  return (
    // 窗框：macOS 下空白处可拖动窗口（红绿灯在左上角，这一列居中、碰不到它们）
    <div className="relative flex h-full justify-center bg-bg" {...dragRegionProps('self')}>
      {/* Windows 的应用菜单：与首页同一个位置（左上角） */}
      <AppMenuButton className="absolute left-2 top-3" />
      <main
        aria-label={t('picker.regionLabel')}
        className="flex h-full w-[460px] max-w-[calc(100vw-3rem)] flex-col"
        {...dragRegionProps('self')}
      >
        {/* 固定区：品牌 + 主入口。`shrink-0` 是这一屏的要点——列表再长也挤不动它 */}
        <header className="shrink-0 pt-3" {...dragRegionProps('self')}>
          <Button size="md" className="-ml-2.5 mb-2 text-ink-2" data-home-back onClick={onBack}>
            <ArrowLeft size={ICON_SIZE.sm} />
            {t('home.backHome')}
          </Button>
          {/* 页面底是纸色 --color-bg：灰块用 paper 档才能与背景分开 */}
          {/* 标题走 type-title（15 / 500），字距 0（全面打磨 D44，§6：负字距是自造的第七种排法） */}
          <h1 className="type-title flex items-center gap-2.5">
            <BrandMark size={24} tone="paper" />
            {PRODUCT_NAME}
          </h1>
          <p className="mt-1 text-xs leading-relaxed text-ink-3">{t('picker.tagline')}</p>

          <div className="mt-5 flex gap-2">
            {/* 页面 CTA 是 32px（lg，审计 §10.3）；一屏一颗填色主动作 */}
            <Button
              variant="primary"
              size="lg"
              disabled={switching}
              onClick={entry.startCreate}
            >
              <FolderPlus size={ICON_SIZE.md} />
              {t('picker.create')}
            </Button>
            <Button
              variant="secondary"
              size="lg"
              disabled={switching}
              onClick={entry.startOpen}
            >
              <FolderOpen size={ICON_SIZE.md} />
              {t('picker.browse')}
            </Button>
            {currentOpen && (
              <Button
                size="md"
                className="ml-auto text-ink-2"
                disabled={switching}
                onClick={() => useProjectStore.getState().returnToCurrent()}
              >
                {t('picker.backToCurrent')}
              </Button>
            )}
          </div>

          {/* 一个框两件事：粘贴路径直接打开（从文件管理器复制过来永远比一层层点快），
              输入名字就筛最近项目；回车打开路径或唯一匹配项 */}
          <form
            role="search"
            className="mt-3 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (!target || switching) return
              void openPath(target.kind === 'path' ? target.path : target.entry.path)
            }}
          >
            <TextInput
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={t('picker.pathPlaceholder')}
              aria-label={t('picker.pathLabel')}
              className="flex-1 font-mono"
              spellCheck={false}
            />
            <Button
              type="submit"
              variant="secondary"
              size="md"
              disabled={!target || switching}
              aria-label={
                target?.kind === 'recent' ? t('picker.openMatch', { name: target.entry.name }) : undefined
              }
            >
              <CornerDownLeft size={ICON_SIZE.sm} />
              {t('picker.openButton')}
            </Button>
          </form>

          {error && (
            <p role="alert" className="mt-3 text-xs leading-relaxed text-danger">
              {error}
            </p>
          )}

          <TutorialEntry />
        </header>

        {recent.length > 0 && (
          <section
            aria-label={t('picker.recentLabel')}
            className="flex min-h-0 flex-1 flex-col pb-6 pt-7"
          >
            {/* 分区小标走 type-section（全面打磨 D44） */}
            <h2 className="mb-1.5 flex h-7 shrink-0 items-center gap-1.5">
              <span className="type-section">{t('picker.recentHeading')}</span>
              {/* 计数是 type-meta（等宽数字），不是等宽字体（审计 §10.3） */}
              <span className="type-meta">{t('picker.recentCount', { count: available.length + missing.length })}</span>
            </h2>
            {/* 只有这一块滚：`min-h-0` 让它在 flex 列里真的能收缩，否则它会把
                自己撑到内容高度、把滚动交还给页面（= 改造前的样子） */}
            <div
              data-recent-scroll
              ref={roving.ref}
              onFocus={roving.onFocus}
              onKeyDown={roving.onKeyDown}
              className="-mx-2 min-h-0 flex-1 overflow-y-auto px-1"
            >
              {/* 行与左栏「项目」抽屉同一份（`ProjectRow`，这里 density="page"：52px + 文件夹记号）；
                  一列一个 Tab 停靠点，↑↓ 走行、Enter 打开 */}
              <ul className="flex flex-col">
                {available.map((r) => (
                  <ProjectRow
                    key={r.path}
                    entry={r}
                    density="page"
                    hint={hints.get(r.path)}
                    busy={busyPath === r.path}
                    switching={switching}
                    onOpen={() => void openPath(r.path)}
                    onRemove={() => void remove(r.path)}
                  />
                ))}
              </ul>
              {missing.length > 0 && (
                <MissingGroup
                  entries={missing}
                  hints={hints}
                  onRemove={(path) => void remove(path)}
                  onRemoveAll={() => void removeMany(missing.map((r) => r.path))}
                />
              )}
              {available.length + missing.length === 0 && (
                <EmptyState icon={SearchX} title={t('picker.filterNoMatch', { query: typed.trim() })} data-picker-no-match />
              )}
            </div>
          </section>
        )}

        {entry.dialogs}
      </main>
    </div>
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
 */
function TutorialEntry() {
  const { t } = useTranslation('project')
  const entry = useOnboardingStore((s) => tutorialEntry(s.status))
  const switching = useProjectStore((s) => s.switching)
  return (
    <section aria-label={t('picker.tutorialLabel')} className="mt-5 flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="md"
          className="-ml-2.5 text-ink-2"
          disabled={switching}
          data-onboarding-anchor="tutorial-entry"
          onClick={() => void runTutorialEntry('picker')}
        >
          <BookOpen size={ICON_SIZE.md} />
          {t(`picker.tutorial.${entry}`)}
        </Button>
      </div>
      <p className="text-xs leading-relaxed text-ink-3">{t('picker.tutorialHint')}</p>
    </section>
  )
}

/**
 * 已不存在的目录：收成一组放在可打开的项目后面，默认折叠。
 * 组头是两个**并列**的控件（展开 / 全部移除），不把按钮套进 summary 里
 * ——嵌套交互控件在辅助技术里是读不出来的。
 */
function MissingGroup({
  entries,
  hints,
  onRemove,
  onRemoveAll,
}: {
  entries: RecentProject[]
  hints: Map<string, string>
  onRemove: (path: string) => void
  onRemoveAll: () => void
}) {
  const { t } = useTranslation('project')
  const [expanded, setExpanded] = useState(false)
  const id = 'picker-missing-group'
  return (
    <section aria-label={t('picker.missingGroup')} className="mt-4 border-t border-border pt-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={id}
          aria-label={t(expanded ? 'picker.missingGroupCollapse' : 'picker.missingGroupExpand')}
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-1 rounded-sm text-left text-xs text-ink-2 outline-none hover:text-ink focus-visible:focus-ring"
        >
          <ChevronRight
            size={ICON_SIZE.xs}
            aria-hidden
            className={cn('shrink-0 text-ink-3 transition-transform', expanded && 'rotate-90')}
          />
          <span className="truncate">{t('picker.missingGroup')}</span>
          <span className="tabular-nums text-ink-3">{t('picker.recentCount', { count: entries.length })}</span>
        </button>
        <Button size="sm" variant="secondary" className="shrink-0 text-xs" onClick={onRemoveAll}>
          {t('picker.removeAllMissing')}
        </Button>
      </div>
      {expanded && (
        <>
          <p className="mt-1 text-xs leading-relaxed text-ink-3">{t('picker.missingHint')}</p>
          <ul id={id} className="mt-1 flex flex-col">
            {entries.map((r) => (
              <ProjectRow
                key={r.path}
                entry={r}
                density="page"
                hint={hints.get(r.path)}
                busy={false}
                switching={false}
                onOpen={() => {}}
                onRemove={() => onRemove(r.path)}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
