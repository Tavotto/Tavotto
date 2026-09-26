import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  TriangleAlert,
  ArrowLeft,
  BookOpen,
  ChevronRight,
  CornerDownLeft,
  Folder,
  FolderOpen,
  FolderPlus,
  X,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { backendErrorText, type RecentProject } from '@/lib/api'
import { isDesktop, pickDirectory } from '@/lib/desktop'
import { useFormatMessage } from '@/i18n/react'
import { PRODUCT_NAME } from '@/lib/brand'
import {
  homeVariant,
  loadTutorialStatus,
  runTutorialEntry,
  tutorialEntry,
  useTutorialStore,
} from '@/lib/onboarding/tutorial'
import { disambiguateRecent, splitRecent, submitTargetFor } from '@/lib/recentProjects'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { DirBrowser, NewProjectNameDialog, TailPath } from './DirBrowser'
import { HomeView } from './home/HomeView'
import { BrandMark } from './ui/BrandMark'
import { Button, IconButton } from './ui/Button'
import { TextInput } from './ui/Input'

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

  if (view === 'home') {
    return (
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
    )
  }
  return (
    <AllProjects
      error={error}
      busyPath={busyPath}
      openPath={openPath}
      onBack={() => {
        setError(null)
        setView('home')
      }}
    />
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

  return (
    <div className="flex h-full justify-center bg-bg">
      <main
        aria-label={t('picker.regionLabel')}
        className="flex h-full w-[460px] max-w-[calc(100vw-3rem)] flex-col"
      >
        {/* 固定区：品牌 + 主入口。`shrink-0` 是这一屏的要点——列表再长也挤不动它 */}
        <header className="shrink-0 pt-3">
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
            <Button
              variant="primary"
              size="md"
              disabled={switching}
              onClick={entry.startCreate}
            >
              <FolderPlus size={ICON_SIZE.md} />
              {t('picker.create')}
            </Button>
            <Button
              variant="secondary"
              size="md"
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
            <h2 className="type-section mb-1.5 flex shrink-0 items-baseline gap-1.5">
              {t('picker.recentHeading')}
              <span className="font-mono text-ink-3">
                {t('picker.recentCount', { count: available.length + missing.length })}
              </span>
            </h2>
            {/* 只有这一块滚：`min-h-0` 让它在 flex 列里真的能收缩，否则它会把
                自己撑到内容高度、把滚动交还给页面（= 改造前的样子） */}
            <div data-recent-scroll className="min-h-0 flex-1 overflow-y-auto">
              <ul className="flex flex-col">
                {available.map((r) => (
                  <RecentRow
                    key={r.path}
                    entry={r}
                    hint={hints.get(r.path)}
                    busy={busyPath === r.path}
                    disabled={switching}
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
                <p className="py-4 text-xs text-ink-3">{t('picker.filterNoMatch', { query: typed.trim() })}</p>
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
 * 「用示例了解 Tavotto」——低干扰的一行入口（ADR 0040）。
 *
 * 状态三档：宿主没有 Tutorial API（GET 失败）→ 整行不出现；资源坏了
 * （`available:false`）→ 按钮禁用 + 一句「请重新安装」；正常 → 按钮。
 * 点下去的全部逻辑在 `lib/onboarding/tutorial.ts`，这里只显示结果。
 */
function TutorialEntry() {
  const { t } = useTranslation('project')
  const fmt = useFormatMessage()
  const status = useTutorialStore((s) => s.status)
  const busy = useTutorialStore((s) => s.busy)
  const failure = useTutorialStore((s) => s.failure)
  const entry = useOnboardingStore((s) => tutorialEntry(s.status))
  // 教程也是一次切项目（adoptOpenedProject）：切换进行中不再排第二次
  const switching = useProjectStore((s) => s.switching)

  useEffect(() => {
    void loadTutorialStatus()
  }, [])

  // 宿主没提供 Tutorial API（embedded / 老后端）：入口整行不出现
  if (failure?.reason === 'no_api' && !status) return null
  const unavailable = !!status && !status.available

  return (
    <section aria-label={t('picker.tutorialLabel')} className="mt-5 flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="md"
          className="-ml-2.5 text-ink-2"
          disabled={unavailable || busy === 'open' || switching}
          loading={busy === 'open'}
          loadingLabel={t('picker.tutorialOpening')}
          data-onboarding-anchor="tutorial-entry"
          onClick={() => void runTutorialEntry('picker')}
        >
          <BookOpen size={ICON_SIZE.md} />
          {t(`picker.tutorial.${entry}`)}
        </Button>
      </div>
      <p className="text-xs leading-relaxed text-ink-3">
        {unavailable ? t('picker.tutorialUnavailable') : t('picker.tutorialHint')}
      </p>
      {failure && failure.reason !== 'no_api' && failure.reason !== 'cancelled' && (
        <p role="alert" className="text-xs leading-relaxed text-danger">
          {fmt(failure.message)}
        </p>
      )}
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
              <RecentRow
                key={r.path}
                entry={r}
                hint={hints.get(r.path)}
                busy={false}
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

function RecentRow({
  entry,
  hint,
  busy,
  disabled = false,
  onOpen,
  onRemove,
}: {
  entry: RecentProject
  /** 同名项目的辨认后缀（`lib/recentProjects.disambiguateRecent`） */
  hint?: string
  busy: boolean
  /** 有一次切换正在进行：打开不了（移除仍可以） */
  disabled?: boolean
  onOpen: () => void
  onRemove: () => void
}) {
  const { t } = useTranslation('project')
  return (
    <li className="group flex items-center gap-2 border-b border-border py-2 last:border-b-0">
      <Folder size={ICON_SIZE.md} className="shrink-0 text-ink-3" />
      <button
        onClick={onOpen}
        disabled={busy || disabled || !entry.exists}
        className={cn(
          'min-w-0 flex-1 text-left outline-none focus-visible:focus-ring',
          entry.exists ? 'cursor-pointer' : 'cursor-default',
        )}
        aria-label={t('picker.openProject', { name: entry.name })}
        title={entry.tutorial ? undefined : entry.path}
      >
        <span className="flex items-center gap-1.5">
          <span className="truncate text-xs font-medium text-ink">{entry.name}</span>
          {!entry.exists && (
            <span className="flex shrink-0 items-center gap-1 text-xs text-danger">
              <TriangleAlert size={ICON_SIZE.xs} />
              {t('picker.missingDir')}
            </span>
          )}
          {busy && <span className="shrink-0 text-xs text-ink-3">{t('picker.opening')}</span>}
        </span>
        {/* 教程副本躺在数据目录里：显示「教程」而不是那条路径（T-104） */}
        {entry.tutorial ? (
          <span className="block text-xs text-ink-3">{t('picker.tutorialBadge')}</span>
        ) : hint ? (
          <span className="block truncate font-mono text-xs text-ink-3">{hint}</span>
        ) : (
          <TailPath path={entry.path} />
        )}
      </button>
      <IconButton
        iconSize="sm"
        tip={false}
        onClick={onRemove}
        label={t('picker.removeFromList', { name: entry.name })}
        title={t('picker.removeFromListTitle')}
        className={cn(
          'text-ink-3 transition-[opacity,background-color,color]',
          'focus-visible:opacity-100 group-hover:opacity-100',
          // 打不开的条目只剩「移除」一个动作：常驻显示，不藏在悬停后面
          entry.exists ? 'opacity-0' : 'opacity-100',
        )}
      >
        <X size={ICON_SIZE.sm} />
      </IconButton>
    </li>
  )
}
