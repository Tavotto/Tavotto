import { useEffect, useState, type DragEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft,
  ChevronRight,
  Ellipsis,
  FileCodeCorner,
  Folder,
  FolderOpen,
  Play,
  Zap,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import type { RecentProject } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { isDesktop, pickScriptFile } from '@/lib/desktop'
import { formatRelativeTime } from '@/i18n/format'
import { useFormatMessage } from '@/i18n/react'
import {
  loadTutorialStatus,
  openSampleProject,
  runTutorialEntry,
  tutorialEntry,
  useTutorialStore,
  type HomeVariant,
} from '@/lib/onboarding/tutorial'
import { dragHasFiles, dropTargetOf, folderForPath } from '@/lib/scriptImport'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { BrandMark } from '../ui/BrandMark'
import { Button, IconButton } from '../ui/Button'
import { Menu, MenuItem } from '../ui/Menu'
import { DirBrowser, TailPath } from '../DirBrowser'
import { FoundFiguresArt, LayoutCanvasArt, ScriptFileArt } from './StepIllustrations'

/**
 * 主页：还没有打开项目时的整屏（`App` 在 `phase === 'none'` 时显示 `ProjectPicker`，
 * 它的默认视图就是这里）。两版：
 *
 *   * **新手版**——三步说明 + 「用示例体验一次」（= 教程入口，`runTutorialEntry`）+ 「导入我的脚本」；
 *   * **老手版**——拖放区 + 「使用示例脚本试试看」（只打开示例项目、不带引导）+ 最近项目列表。
 *
 * 哪一版只由 `lib/onboarding/tutorial.homeVariant()` 判（onboarding 状态），这里不判。
 * 新建项目、按路径打开 / 筛选、失效目录、教程的「重新开始」都在「全部项目」视图里
 * （`ProjectPicker` 的另一半），这里的「浏览更多项目 / 更多 / 其他打开方式」都通到那儿。
 */
export function HomeView({
  variant,
  error,
  busyPath,
  openPath,
  onShowAll,
}: {
  variant: HomeVariant
  error: string | null
  busyPath: string | null
  openPath: (path: string) => void
  onShowAll: () => void
}) {
  const { t } = useTranslation('project')
  const importer = useScriptImport(openPath)
  const [dragging, setDragging] = useState(false)
  const currentOpen = useProjectStore((s) => s.project?.open === true)
  const switching = useProjectStore((s) => s.switching)

  // 整页都收拖放（新手版没有画出来的拖放区，放下来一样能导入）；只认带文件的拖动
  const dropHandlers = {
    onDragEnter: (e: DragEvent) => {
      if (dragHasFiles(e.dataTransfer.types)) setDragging(true)
    },
    onDragOver: (e: DragEvent) => {
      if (!dragHasFiles(e.dataTransfer.types)) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'copy'
    },
    onDragLeave: (e: DragEvent) => {
      // 离开的是整页（去了窗口外），不是在子元素之间穿行
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false)
    },
    onDrop: (e: DragEvent) => {
      if (!dragHasFiles(e.dataTransfer.types)) return
      e.preventDefault()
      setDragging(false)
      if (!switching) importer.drop(e.dataTransfer)
    },
  }

  return (
    <main
      aria-label={t('picker.regionLabel')}
      data-home-variant={variant}
      className="h-full overflow-y-auto bg-bg"
      {...dropHandlers}
    >
      <div className="mx-auto flex w-full max-w-[1000px] flex-col px-6 pb-10 pt-4">
        {/* 从设置 / 菜单「切换项目」进来时后端仍有打开的项目——允许原路返回 */}
        <div className="flex h-7 shrink-0 items-center">
          {currentOpen && (
            <Button
              size="md"
              className="-ml-2.5 text-ink-2"
              disabled={switching}
              onClick={() => useProjectStore.getState().returnToCurrent()}
            >
              <ArrowLeft size={ICON_SIZE.sm} />
              {t('picker.backToCurrent')}
            </Button>
          )}
        </div>
        {variant === 'newcomer' ? (
          <Newcomer importer={importer} />
        ) : (
          <Returning importer={importer} dragging={dragging} />
        )}
        {error && (
          <p role="alert" className="mt-3 text-center text-sm leading-relaxed text-danger">
            {error}
          </p>
        )}
        <RecentSection
          variant={variant}
          busyPath={busyPath}
          openPath={openPath}
          onShowAll={onShowAll}
        />
      </div>
      {importer.dialogs}
    </main>
  )
}

/* --------------------------------- 导入 ---------------------------------- */

type Importer = ReturnType<typeof useScriptImport>

/**
 * 「导入我的脚本」/ 拖放区 / 点击选择文件——三个入口一条路：得到一个目录 → `openPath`
 * （与打开任何项目同一条 `projectStore.open`）。桌面壳用原生文件选择器（只收 .py），
 * 浏览器回退到服务器端目录浏览器选脚本所在的文件夹。拖放拿不到路径时退回选择器，
 * 并说清是哪个文件（判据在 `lib/scriptImport`）。
 */
function useScriptImport(openPath: (path: string) => void) {
  const { t } = useTranslation('project')
  const [browsing, setBrowsing] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  const choose = () => {
    if (isDesktop()) {
      void pickScriptFile(t('home.import.nativeTitle')).then((path) => {
        if (path) {
          setNotice(null)
          openPath(folderForPath(path))
        }
      })
    } else setBrowsing(true)
  }

  const start = () => {
    setNotice(null)
    choose()
  }

  const drop = (dt: DataTransfer) => {
    const target = dropTargetOf(dt)
    switch (target.kind) {
      case 'path':
        setNotice(null)
        openPath(target.folder)
        return
      case 'no-path':
        setNotice({ tone: 'info', text: t('home.import.dropNoPath', { name: target.name }) })
        choose()
        return
      case 'not-script':
        setNotice({ tone: 'error', text: t('home.import.dropNotScript', { name: target.name }) })
        return
      case 'none':
        return
    }
  }

  const noticeView = notice && (
    <p
      role={notice.tone === 'error' ? 'alert' : undefined}
      aria-live={notice.tone === 'error' ? undefined : 'polite'}
      className={cn(
        'mt-3 text-center text-sm leading-relaxed',
        notice.tone === 'error' ? 'text-danger' : 'text-ink-2',
      )}
    >
      {notice.text}
    </p>
  )

  const dialogs = browsing && (
    <DirBrowser
      mode="open"
      title={t('home.import.folderTitle')}
      onClose={() => setBrowsing(false)}
      onPick={(path) => {
        setBrowsing(false)
        setNotice(null)
        openPath(path)
      }}
    />
  )
  return { start, drop, noticeView, dialogs }
}

/* ------------------------------ 示例 / 教程入口 ------------------------------ */

/**
 * 示例项目这一格的可用性（与「全部项目」里那行教程入口同一份 `useTutorialStore`）：
 * 宿主没有 Tutorial API → 按钮与「已随安装包内置」那句话都不出现；资源坏了 → 禁用 +
 * 「请重新安装」；正常 → 按钮 + 那句话。那句话只在 `status.available` 时说——它的兑现
 * 是 wheel / 桌面包里的 `resources/tutorial_project`（`tests/test_tutorial.py` 的
 * wheel 成员与 PyInstaller datas 两条用例），后端验过资源才回 `available: true`。
 */
function useSampleAvailability() {
  const status = useTutorialStore((s) => s.status)
  const busy = useTutorialStore((s) => s.busy)
  const failure = useTutorialStore((s) => s.failure)
  const switching = useProjectStore((s) => s.switching)
  useEffect(() => {
    void loadTutorialStatus()
  }, [])
  const hidden = failure?.reason === 'no_api' && !status
  const unavailable = !!status && !status.available
  return {
    hidden,
    unavailable,
    available: !!status?.available,
    opening: busy === 'open',
    disabled: unavailable || busy === 'open' || switching,
    failure: failure && failure.reason !== 'no_api' && failure.reason !== 'cancelled' ? failure : null,
  }
}

function SampleFailure({ failure }: { failure: ReturnType<typeof useSampleAvailability>['failure'] }) {
  const fmt = useFormatMessage()
  if (!failure) return null
  return (
    <p role="alert" className="mt-2 text-center text-sm leading-relaxed text-danger">
      {fmt(failure.message)}
    </p>
  )
}

/** 首屏的大号 CTA：版式同一档 primary / secondary，只是高一档（主页是落地页，不是工具栏） */
const HERO_BUTTON = 'h-9 min-w-[200px] px-5 text-base'

/* --------------------------------- 新手版 ---------------------------------- */

function Newcomer({ importer }: { importer: Importer }) {
  const { t } = useTranslation('project')
  const sample = useSampleAvailability()
  const entry = useOnboardingStore((s) => tutorialEntry(s.status))
  const switching = useProjectStore((s) => s.switching)
  const steps: { title: string; body: string; art: ReactNode }[] = [
    { title: t('home.newcomer.step1Title'), body: t('home.newcomer.step1Body', { product: PRODUCT_NAME }), art: <ScriptFileArt /> },
    { title: t('home.newcomer.step2Title'), body: t('home.newcomer.step2Body', { product: PRODUCT_NAME }), art: <FoundFiguresArt /> },
    { title: t('home.newcomer.step3Title'), body: t('home.newcomer.step3Body'), art: <LayoutCanvasArt /> },
  ]
  return (
    <>
      <header className="flex flex-col items-center pt-4 text-center">
        <Wordmark />
        <h1 className="mt-6 text-[24px] font-medium leading-tight text-ink">{t('home.newcomer.title')}</h1>
        <p className="mt-3 max-w-[34em] text-base leading-relaxed text-ink-2">{t('home.newcomer.lead')}</p>
      </header>

      <ol aria-label={t('home.newcomer.stepsLabel')} className="mt-8 grid grid-cols-1 gap-8 md:grid-cols-3">
        {steps.map((s, i) => (
          <li
            key={i}
            className="relative flex flex-col rounded-md border border-border bg-surface p-4"
          >
            <div className="flex items-start gap-3">
              <span
                aria-hidden
                className="flex size-8 shrink-0 items-center justify-center rounded-full bg-field text-lg font-medium text-ink"
              >
                {i + 1}
              </span>
              <div className="min-w-0">
                <h2 className="text-lg font-medium text-ink">{s.title}</h2>
                <p className="mt-1 text-sm leading-relaxed text-ink-2">{s.body}</p>
              </div>
            </div>
            <div className="mt-4 h-[120px] rounded-sm bg-bg">{s.art}</div>
            {i < steps.length - 1 && (
              <ChevronRight
                size={ICON_SIZE.lg}
                aria-hidden
                className="absolute -right-7 top-1/2 hidden -translate-y-1/2 text-ink-faint md:block"
              />
            )}
          </li>
        ))}
      </ol>

      <div className="mt-4 rounded-md bg-field px-6 py-4 text-center">
        <p className="flex items-center justify-center gap-1.5 text-lg font-medium text-ink">
          <Zap size={ICON_SIZE.md} aria-hidden />
          {t('home.newcomer.tipTitle')}
        </p>
        <p className="mt-1 text-sm leading-relaxed text-ink-2">{t('home.newcomer.tipBody')}</p>
      </div>

      <div className="mt-6 flex flex-wrap justify-center gap-3">
        {!sample.hidden && (
          <Button
            variant="primary"
            className={HERO_BUTTON}
            disabled={sample.disabled}
            loading={sample.opening}
            loadingLabel={t('picker.tutorialOpening')}
            data-onboarding-anchor="tutorial-entry"
            onClick={() => void runTutorialEntry('picker')}
          >
            <Play size={ICON_SIZE.md} />
            {t(entry === 'resume' ? 'home.newcomer.tryResume' : 'home.newcomer.tryStart')}
          </Button>
        )}
        <Button
          variant="secondary"
          className={HERO_BUTTON}
          disabled={switching}
          data-home-import
          onClick={importer.start}
        >
          <FolderOpen size={ICON_SIZE.md} />
          {t('home.newcomer.import')}
        </Button>
      </div>
      {!sample.hidden && (sample.available || sample.unavailable) && (
        <p className="mt-3 text-center text-sm text-ink-3">
          {sample.unavailable ? t('picker.tutorialUnavailable') : t('home.newcomer.bundled')}
        </p>
      )}
      <SampleFailure failure={sample.failure} />
      {importer.noticeView}
    </>
  )
}

/* --------------------------------- 老手版 ---------------------------------- */

function Returning({ importer, dragging }: { importer: Importer; dragging: boolean }) {
  const { t } = useTranslation('project')
  const sample = useSampleAvailability()
  const switching = useProjectStore((s) => s.switching)
  return (
    <>
      <header className="flex flex-col items-center pt-6 text-center">
        <h1>
          <Wordmark />
        </h1>
        <p className="mt-3 text-base text-ink-2">{t('home.returning.lead')}</p>
      </header>

      {/* 拖放区本身是一颗按钮：点它 / Enter / 空格 = 选择文件（键盘与读屏的等价操作）。
          真正收拖放的是整页（HomeView 的 dropHandlers），这里只负责「拖到这里」的高亮 */}
      <button
        type="button"
        data-home-dropzone
        data-dragging={dragging || undefined}
        disabled={switching}
        onClick={importer.start}
        aria-describedby="home-dropzone-hint"
        className={cn(
          'mx-auto mt-8 flex w-full max-w-[720px] flex-col items-center rounded-md border border-dashed px-6 py-10',
          'border-border-strong bg-surface-2 outline-none transition-colors',
          'hover:border-ink-3 hover:bg-surface focus-visible:focus-ring',
          'disabled:cursor-not-allowed disabled:opacity-40',
          dragging && 'border-accent bg-accent-subtle',
        )}
      >
        <span className="flex size-14 items-center justify-center rounded-md border border-border bg-surface text-ink-2">
          <FileCodeCorner size={ICON_SIZE.lg} aria-hidden />
        </span>
        <span className="mt-4 text-[20px] font-medium leading-tight text-ink">
          {dragging ? t('home.returning.dropRelease') : t('home.returning.dropTitle')}
        </span>
        <span id="home-dropzone-hint" className="mt-2 flex flex-col items-center gap-0.5 text-base text-ink-2">
          <span>{t('home.returning.dropHint')}</span>
          <span>
            {t('home.returning.dropOr')}
            <span className="underline underline-offset-2">{t('home.returning.dropChoose')}</span>
          </span>
        </span>
      </button>
      {importer.noticeView}

      {!sample.hidden && (
        <div className="mt-6 flex flex-col items-center">
          <Button
            variant="primary"
            className={HERO_BUTTON}
            disabled={sample.disabled}
            loading={sample.opening}
            loadingLabel={t('picker.tutorialOpening')}
            data-home-sample
            onClick={() => void openSampleProject('picker')}
          >
            <FileCodeCorner size={ICON_SIZE.md} />
            {t('home.returning.sample')}
          </Button>
          <p className="mt-2 text-sm text-ink-3">
            {sample.unavailable
              ? t('picker.tutorialUnavailable')
              : t('home.returning.sampleHint', { product: PRODUCT_NAME })}
          </p>
          <SampleFailure failure={sample.failure} />
        </div>
      )}
    </>
  )
}

/** 品牌标 + 产品名（两处都来自品牌常量；图形是装饰，名字是文字） */
function Wordmark() {
  return (
    <span className="flex items-center justify-center gap-3">
      <BrandMark size={40} tone="paper" />
      <span className="text-[26px] font-medium leading-none text-ink">{PRODUCT_NAME}</span>
    </span>
  )
}

/* -------------------------------- 最近项目 --------------------------------- */

/** 主页上最多摆几条：更多的在「全部项目」里 */
const PREVIEW = { newcomer: 3, returning: 5 } as const

function RecentSection({
  variant,
  busyPath,
  openPath,
  onShowAll,
}: {
  variant: HomeVariant
  busyPath: string | null
  openPath: (path: string) => void
  onShowAll: () => void
}) {
  const { t } = useTranslation('project')
  const recent = useProjectStore((s) => s.recent)
  const remove = useProjectStore((s) => s.remove)
  const switching = useProjectStore((s) => s.switching)
  // 已不存在的目录不上主页（主页的每一行都该点得开）：它们在「全部项目」里成组可移除
  const shown = recent.filter((r) => r.exists).slice(0, PREVIEW[variant])

  const allLink = (
    <Button
      size="md"
      className={cn('text-ink-2', recent.length > 0 && '-mr-2.5')}
      data-home-all
      onClick={onShowAll}
    >
      {recent.length === 0
        ? t('home.otherWays')
        : variant === 'newcomer'
          ? t('home.browseMore')
          : t('home.more')}
      <ChevronRight size={ICON_SIZE.sm} aria-hidden />
    </Button>
  )

  if (recent.length === 0) {
    // 还没有最近项目：只留一条通往「新建项目 / 按路径打开」的路
    return <div className="mt-8 flex justify-center">{allLink}</div>
  }

  return (
    <section
      aria-labelledby="home-recent-heading"
      className={cn('mt-8', variant === 'newcomer' ? 'border-t border-border pt-5' : 'mx-auto w-full max-w-[720px]')}
    >
      <div className="flex items-center justify-between">
        <h2 id="home-recent-heading" className="type-section flex items-baseline gap-1.5">
          {t('picker.recentHeading')}
          <span className="font-normal text-ink-3">{t('picker.recentCount', { count: recent.length })}</span>
        </h2>
        {allLink}
      </div>
      {variant === 'newcomer' ? (
        <ul className="mt-2 grid grid-cols-1 gap-x-6 sm:grid-cols-2 lg:grid-cols-3">
          {shown.map((r) => (
            <RecentItem
              key={r.path}
              entry={r}
              layout="card"
              busy={busyPath === r.path}
              disabled={switching}
              onOpen={() => openPath(r.path)}
              onRemove={() => void remove(r.path)}
            />
          ))}
        </ul>
      ) : (
        <ul className="mt-2 border-t border-border">
          {shown.map((r) => (
            <RecentItem
              key={r.path}
              entry={r}
              layout="row"
              busy={busyPath === r.path}
              disabled={switching}
              onOpen={() => openPath(r.path)}
              onRemove={() => void remove(r.path)}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

/**
 * 主页上的一条最近项目。主体是一颗「打开」按钮（名字 + 路径 / 时间），右侧 ⋯ 菜单收着
 * 「打开 / 从列表移除」——设计稿上没有移除的位置，放进菜单；「全部项目」里照旧有行尾的 ×。
 * 时间是**上次打开**的时间（`last_opened`，后端 `touch_recent` 记的），不是文件修改时间。
 */
function RecentItem({
  entry,
  layout,
  busy,
  disabled,
  onOpen,
  onRemove,
}: {
  entry: RecentProject
  layout: 'card' | 'row'
  busy: boolean
  disabled: boolean
  onOpen: () => void
  onRemove: () => void
}) {
  const { t } = useTranslation('project')
  const when = entry.last_opened > 0 ? formatRelativeTime(entry.last_opened) : null
  const Icon = layout === 'card' ? Folder : FileCodeCorner
  const sub = entry.tutorial ? (
    <span className="block text-sm text-ink-3">{t('picker.tutorialBadge')}</span>
  ) : layout === 'row' ? (
    <TailPath path={entry.path} className="text-sm" />
  ) : when ? (
    <span className="block text-sm text-ink-3">{t('home.openedAgo', { when })}</span>
  ) : null
  return (
    <li
      data-home-recent={entry.path}
      className={cn(
        'group flex items-center gap-2',
        layout === 'row' ? 'border-b border-border py-2' : 'py-2',
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={busy || disabled}
        aria-label={t('picker.openProject', { name: entry.name })}
        title={entry.tutorial ? undefined : entry.path}
        className={cn(
          '-ml-2 flex min-w-0 flex-1 items-center gap-3 rounded-sm px-2 py-1.5 text-left outline-none',
          'hover:bg-surface-hover focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-40',
        )}
      >
        <Icon size={ICON_SIZE.lg} className="shrink-0 text-ink-3" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-base text-ink">{entry.name}</span>
            {busy && <span className="shrink-0 text-sm text-ink-3">{t('picker.opening')}</span>}
          </span>
          {sub}
        </span>
        {layout === 'row' && when && (
          <span className="shrink-0 text-sm tabular-nums text-ink-3">{when}</span>
        )}
      </button>
      <Menu
        align="end"
        width={180}
        trigger={
          <IconButton
            iconSize="sm"
            tip={false}
            label={t('home.recentMenu', { name: entry.name })}
            className={cn(
              'text-ink-3',
              // 行式列表里平时收起、悬停 / 聚焦 / 菜单开着时出现；卡片式常驻（设计稿如此）
              layout === 'row' &&
                'opacity-0 transition-[opacity,background-color] focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100',
            )}
          >
            <Ellipsis size={ICON_SIZE.sm} />
          </IconButton>
        }
      >
        <MenuItem icon={FolderOpen} disabled={busy || disabled} onSelect={onOpen}>
          {t('home.menuOpen')}
        </MenuItem>
        <MenuItem onSelect={onRemove}>{t('picker.removeFromListTitle')}</MenuItem>
      </Menu>
    </li>
  )
}
