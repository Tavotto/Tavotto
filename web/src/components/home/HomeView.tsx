import { useEffect, useRef, useState, type DragEvent } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import {
  ArrowLeft,
  ChevronRight,
  Ellipsis,
  FileCodeCorner,
  Folder,
  FolderOpen,
  Play,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { backendErrorText, browseDirs, type RecentProject } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import {
  dragRegionProps,
  isDesktop,
  nativeFileDropAvailable,
  onNativeFileDrop,
  pickScriptFile,
  type NativeFileDrop,
  WINDOW_CONTROLS_INSET,
  windowChrome,
} from '@/lib/desktop'
import { msg } from '@/i18n'
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
import { foreignLanguageOf, type ForeignLanguage } from '@/lib/foreignScript'
import {
  createDropArbiter,
  dragHasFiles,
  dropTargetOf,
  folderForPath,
  scriptForPath,
  type DropTarget,
} from '@/lib/scriptImport'
import { cn } from '@/lib/utils'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useForeignScriptStore } from '@/store/foreignScriptStore'
import { scriptTarget, useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { BrandMark } from '../ui/BrandMark'
import { Button, IconButton } from '../ui/Button'
import { Card } from '../ui/Card'
import { dropZoneClass } from '../ui/dropZone'
import { Menu, MenuItem } from '../ui/Menu'
import { AppMenuButton } from '../AppMenuButton'
import { DirBrowser, TailPath } from '../DirBrowser'

/**
 * 主页：还没有打开项目时的整屏（`App` 在 `phase === 'none'` 时显示 `ProjectPicker`，
 * 它的默认视图就是这里）。两版：
 *
 *   * **新手版**——一句大问题 + 拖放区 + 黑色主按钮「导入我的脚本」；次按钮「用示例学一遍（带引导）」
 *     （= 教程入口，`runTutorialEntry`）+ 最近项目（没有三步说明卡与提示条：拖放区标题本身就是说明）；
 *   * **老手版**——一句叙事句（「继续 [最近项目] ，或 [打开脚本] 开始新的排版」，句中两枚 24px chip 就是
 *     两个入口，2026-10-07 设计审计 §4.2 学 OpenBitFun WelcomePanel）+ 拖放区 + 「使用示例脚本试试看」
 *     （只打开示例项目、不带引导）+ 最近项目卡片网格。
 *
 * 两版的最近项目都是 `ui/Card` interactive 网格（缩略图格 + 名字 + 路径 / 时间）；拖放区静态时是一张普通卡，
 * 只有拖着文件进来才出现 accent 虚线 + 浅底 + 外发光（`ui/dropZone`）。
 *
 * 哪一版只由 `lib/onboarding/tutorial.homeVariant()` 判（onboarding 状态），这里不判。
 * 新建项目、按路径打开 / 筛选、失效目录、教程的「重新开始」都在「全部项目」视图里
 * （`ProjectPicker` 的另一半），这里的「浏览更多项目 / 更多 / 其他打开方式」都通到那儿。
 */
/**
 * Windows 窗口按钮（`WindowControls`，固定在视口右上角）压在首页顶上那一行的右段上时要让出的
 * 宽度：这一列居中、最宽 1000、左右各 24 内边距，视口越宽列离右边越远，宽到一定程度就不用让。
 * 多算 16 给竖向滚动条（按钮压在滚动条上方，列的右缘却在滚动条左边）。
 */
const HOME_CONTROLS_INSET = `max(0px, calc(${WINDOW_CONTROLS_INSET + 16 - 24}px - max(0px, (100vw - 1000px) / 2)))`

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
  /** 打开一个项目目录；成功回 true（失败的话错误已经摆在这一屏上） */
  openPath: (path: string) => Promise<boolean>
  onShowAll: () => void
}) {
  const { t } = useTranslation('project')
  const importer = useScriptImport(openPath)
  const [dragging, setDragging] = useState(false)
  const currentOpen = useProjectStore((s) => s.project?.open === true)
  const switching = useProjectStore((s) => s.switching)

  // 整页都收拖放（两版都画着拖放区）；只认带文件的拖动
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
      {...dragRegionProps('self')}
    >
      {/* 列宽 = 内容列 720 + 两侧内边距：顶行（返回链接）与拖放区 / 最近项目共用同一条右边界。
          min-h-full + 下面 flex-1 / justify-center：窗口高时内容块在顶行以下的可用高度里垂直居中，
          矮时撑高整列、由 main 滚动（不重叠、不裁切）。各段间距统一读 --home-gap（index.css，随窗高伸缩） */}
      <div className="mx-auto flex min-h-full w-full max-w-[768px] flex-col px-6 pb-6 pt-4" {...dragRegionProps('self')}>
        {/* 从设置 / 菜单「切换项目」进来时后端仍有打开的项目——允许原路返回。
            这一行也是窗框的一部分：空白处可拖动窗口，Windows 的应用菜单在最左、右侧给窗口按钮让位 */}
        <div
          // 右上角窗口级的「联系作者」按钮（`ProjectPicker` 的 fixed 角落：右 12px、宽约 110px、高 28px，离顶 12px）会压在这一行右端的
          // 「返回当前项目」上，当且仅当内容列的右缘（居中、最宽 768）离窗口右缘不到「按钮宽 + 12 + 返回钮自己的 24 内边距」：
          // (W + 768) / 2 − 24 > W − 140 ⇒ W < 1000（Windows 另让出 138px 的窗口按钮：W < 1138）。取断点而不测量——
          // 断点以上布局完全不变；以下整行下移 28px（落在按钮 40px 的底边之下），横向位置不动（「返回链接与内容列右缘对齐」仍成立）。
          className={cn(
            'flex h-7 shrink-0 items-center justify-end',
            windowChrome() === 'menu-button' ? 'max-[1137px]:mt-7' : 'max-[999px]:mt-7',
          )}
          style={windowChrome() === 'menu-button' ? { paddingRight: HOME_CONTROLS_INSET } : undefined}
          {...dragRegionProps('self')}
        >
          <AppMenuButton className="-ml-2.5 mr-auto" />
          {currentOpen && (
            <Button
              size="md"
              className="text-ink-2"
              data-home-back-current
              disabled={switching}
              onClick={() => useProjectStore.getState().returnToCurrent()}
            >
              <ArrowLeft size={ICON_SIZE.sm} />
              {t('picker.backToCurrent')}
            </Button>
          )}
        </div>
        <div
          data-home-content
          className="flex flex-1 flex-col justify-center gap-(--home-gap) py-(--home-gap)"
          {...dragRegionProps('self')}
        >
          {variant === 'newcomer' ? (
            <Newcomer importer={importer} dragging={dragging} />
          ) : (
            <Returning importer={importer} dragging={dragging} openPath={openPath} />
          )}
          {error && (
            <p role="alert" className="text-center text-sm leading-relaxed text-danger">
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
 * 浏览器回退到服务器端目录浏览器选脚本所在的文件夹。
 *
 * 入口给出的是**一个 .py**（选择器选的、拖进来的）时，打开目录之后还要运行**这个**脚本：走脚本库 ▶ 同一条
 * 「打开并运行」（`projectPreparationStore.open(…, { run: true })`），恰好一张图就放进当前画布并进入编辑、
 * 多张由卡片让用户挑。只开目录不跑的话，同一文件夹里拖进不同脚本打开的都是同一个项目，画布上恢复的是
 * 上次的排版，用户看到的永远是第一张图（2026-10-21 诊断包）。只给目录的入口（目录浏览器、拖文件夹）不运行任何脚本。
 *
 * 拖放（ADR 0092）：macOS 桌面壳旁听系统拖放、交来 canonicalize 过的真实路径
 * （`onNativeFileDrop`），`.py` 打开它所在的文件夹、`.R` / `.r` / `.m` 打开所在文件夹并提议“生成可编辑版本”、文件夹直接当项目、别的说不收，
 * 多拖了几个说一句只开了哪个。拿不到路径的宿主（浏览器、其它平台）退回选择器并说清是
 * 哪个文件。同一次放下两条路都会到，谁先谁后不定，由 `createDropArbiter` 仲裁。
 */
function useScriptImport(openPath: (path: string) => Promise<boolean>) {
  const { t } = useTranslation('project')
  const [browsing, setBrowsing] = useState(false)
  /**
   * 浏览器拖入 R / MATLAB 脚本拿不到路径：记下文件名与语言，等用户在目录浏览器里选好文件夹后走 `openForeign`。
   * 只在这一次选择内有效——取消 / 换别的入口 / 选完（成功或不对）都清掉；`problem` = 选错了文件夹的那句话。
   */
  const [pendingForeign, setPendingForeign] = useState<{ name: string; language: ForeignLanguage } | null>(null)
  const [pickProblem, setPickProblem] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  /**
   * 壳能不能交来真实路径：`pending` = 能力探测与事件订阅还没都完成。两件事都是异步、先后不定，
   * 所以「能」只在**订阅已经装好且探测回 true** 之后才成立；`pending` 期间页面的 drop 也先等壳
   * （等不到才降级），免得壳的事件已经在路上、这边又弹一次选择器（Codex #665）。
   */
  const [native, setNative] = useState<'pending' | boolean>('pending')
  const arbiter = useRef<ReturnType<typeof createDropArbiter> | null>(null)
  if (!arbiter.current) arbiter.current = createDropArbiter({ graceMs: NATIVE_DROP_GRACE_MS })

  /**
   * 打开目录；`script`（目录里的文件名）不为空就接着「打开并运行」它。只在项目真的打开了才运行。
   * 文件名带首尾空白（` plot.py`）不运行：后端各脚本端点按约定 `strip()` 脚本名，会找不到它、
   * 或在同目录还有 `plot.py` 时静默跑错脚本（Codex #1267）——只开项目，后端统一支持之后（#1278）再放开
   */
  const openFolder = (folder: string, script: string | null): Promise<boolean> =>
    openPath(folder).then((ok) => {
      if (ok && script && script === script.trim()) void useProjectPreparationStore.getState().open(scriptTarget(script, { reuse: true }), { run: true })
      return ok
    })

  /**
   * R / MATLAB 脚本（ADR 0143 修订）：它们不是可运行的 Python 脚本，不走「打开并运行」。打开所在文件夹，
   * 然后由画布右下的入口卡提议「生成可编辑版本」（原脚本不动）。后缀只是粗判，内容由后端复核。
   */
  const openForeign = (folder: string, script: string, language: ForeignLanguage): Promise<boolean> =>
    openPath(folder).then((ok) => {
      if (ok && script === script.trim()) useForeignScriptStore.getState().setOffer({ script, language })
      return ok
    })

  const openDropped = (drop: NativeFileDrop) => {
    // 壳的事件不经过页面的 onDrop，切换中的闸要在这里再挡一次（与主页其它入口的 disabled 同一个判据）
    if (useProjectStore.getState().switching) return
    // 新手教程层盖在主页上时，系统拖进来的文件一律不认（教程层自己拒收并说明，ADR 0136）：
    // 壳本来只在页面接受了拖动时才发这条事件，这里是第二道闸
    if (useOnboardingStore.getState().surface !== 'none') return
    if (drop.kind === 'unsupported') {
      setNotice({ tone: 'error', text: t('home.import.dropNotScript', { name: drop.name }) })
      return
    }
    if (drop.kind === 'foreign_script') {
      // R / MATLAB 脚本：与 DOM 拖放、选择器同一个入口（openForeign）→ 画布右下的“生成可编辑版本”卡；语言只按后缀粗判，后端复核内容
      const language = foreignLanguageOf(drop.name)
      if (!language) {
        setNotice({ tone: 'error', text: t('home.import.dropNotScript', { name: drop.name }) })
        return
      }
      setNotice(null)
      void openForeign(drop.folder, drop.name, language).then((ok) => {
        // 单个：画布右下的入口卡自己会说话；多拖了的说只用了哪个（openedScript 的“正在运行它”对外语脚本不成立）
        if (ok && drop.ignored > 0)
          useUiStore.getState().setStatus(msg('home.import.droppedMany', { name: drop.name, total: drop.ignored + 1 }, 'project'), 'done')
      })
      return
    }
    setNotice(null)
    void openFolder(drop.folder, drop.kind === 'script' ? drop.name : null).then((ok) => {
      if (!ok) return
      // 页面已经换成编辑器：说明走通知轨。说出是哪个脚本（关联），多拖了的说只开了哪个
      const values = { name: drop.name, total: drop.ignored + 1 }
      const key = drop.ignored > 0 ? 'home.import.droppedMany' : drop.kind === 'script' ? 'home.import.openedScript' : 'home.import.openedFolder'
      useUiStore.getState().setStatus(msg(key, values, 'project'), 'done')
    })
  }

  // 只有主页挂着时订阅：编辑器里壳照样发，但没人听——画布的拖放不受影响
  useEffect(() => {
    let off: (() => void) | undefined
    let disposed = false
    // 订阅失败（事件 ACL 漏登记等）同样算「不能」，否则永远停在 pending、每次拖放都白等
    const listening = onNativeFileDrop((drop) => arbiter.current!.native(() => openDropped(drop))).then(
      (u) => {
        if (disposed) u()
        else off = u
        return true
      },
      () => false,
    )
    void Promise.all([nativeFileDropAvailable(), listening]).then(([ok, listens]) => {
      if (!disposed) setNative(ok && listens)
    })
    return () => {
      disposed = true
      off?.()
      arbiter.current?.dispose()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const choose = (pending: { name: string; language: ForeignLanguage } | null = null) => {
    setPendingForeign(pending)
    setPickProblem(null)
    if (isDesktop()) {
      void pickScriptFile(t('home.import.nativeTitle')).then((path) => {
        if (path) {
          setNotice(null)
          const foreign = foreignLanguageOf(path)
          if (foreign) void openForeign(folderForPath(path), path.replace(/^.*[\\/]/, ''), foreign)
          else void openFolder(folderForPath(path), scriptForPath(path))
        }
      })
    } else setBrowsing(true)
  }

  const start = () => {
    setNotice(null)
    choose()
  }

  /** 拿不到路径时的那条路（浏览器 / 不支持的平台，或壳这次没交来路径） */
  const degrade = (target: DropTarget) => {
    // 在壳里这条是等过 grace 才跑的：放下时没在切换，到这会儿可能已经在切了
    if (useProjectStore.getState().switching) return
    switch (target.kind) {
      case 'path':
        setNotice(null)
        void openFolder(target.folder, target.script)
        return
      case 'foreign-path':
        setNotice(null)
        void openForeign(target.folder, target.script, target.language)
        return
      case 'no-path':
      case 'foreign-no-path':
        setNotice({ tone: 'info', text: t('home.import.dropNoPath', { name: target.name, product: PRODUCT_NAME }) })
        choose(target.kind === 'foreign-no-path' ? { name: target.name, language: target.language } : null)
        return
      case 'not-script':
        setNotice({ tone: 'error', text: t('home.import.dropNotScript', { name: target.name }) })
        return
      case 'none':
        return
    }
  }

  const drop = (dt: DataTransfer) => {
    const target = dropTargetOf(dt)
    if (target.kind === 'none') return
    // 宿主自己给了 file:// 路径就直接用；能拿真实路径的壳里先等它的事件
    if (native === false || target.kind === 'path') degrade(target)
    else arbiter.current!.dom(() => degrade(target))
  }

  const noticeView = notice && (
    <p
      role={notice.tone === 'error' ? 'alert' : undefined}
      aria-live={notice.tone === 'error' ? undefined : 'polite'}
      className={cn(
        'text-center text-sm leading-relaxed',
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
      problem={pickProblem}
      onClose={() => {
        setBrowsing(false)
        setPendingForeign(null)
        setPickProblem(null)
      }}
      onPick={(path) => {
        const pending = pendingForeign
        if (!pending) {
          setBrowsing(false)
          setNotice(null)
          void openPath(path)
          return
        }
        // 先核「这个文件夹里确有这个文件」再开项目：选错了不动当前状态、对话框不关，换个文件夹再选
        void browseDirs(path, pending.name).then(
          (r) => {
            if (!r.has_file) {
              setPickProblem(t('home.import.foreignNotFound', { name: pending.name }))
              return
            }
            setBrowsing(false)
            setPendingForeign(null)
            setPickProblem(null)
            setNotice(null)
            void openForeign(path, pending.name, pending.language)
          },
          (e) => setPickProblem(backendErrorText(e)),
        )
      }}
    />
  )
  return { start, drop, native: native === true, noticeView, dialogs }
}

/** 页面的 drop 到了、壳的系统拖放事件还没到：最多等这么久再降级（IPC 通常几毫秒） */
const NATIVE_DROP_GRACE_MS = 1500

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

/**
 * 拖放区本身是一颗按钮：点它 / Enter / 空格 = 选择文件（键盘与读屏的等价操作）。
 * 真正收拖放的是整页（HomeView 的 dropHandlers），这里只负责「拖到这里」的高亮。
 * 两版共用：标题本身就是说明，不再另配步骤说明。
 *
 * 静态时是一张普通卡（`ui/Card`，没有虚线框）；拖着文件进来才是接收态（`ui/dropZone`：1.5px accent
 * 虚线 + accent-subtle 底 + 外发光，2026-10-07 设计审计 §4.2）。图标坐在 EmptyState v2 那种 40px 底座上。
 */
function DropZone({ importer, dragging }: { importer: Importer; dragging: boolean }) {
  const { t } = useTranslation('project')
  const switching = useProjectStore((s) => s.switching)
  return (
    <Card interactive={!switching} padding="none" className="mx-auto w-full max-w-[720px]">
      <button
        type="button"
        data-home-dropzone
        data-dragging={dragging || undefined}
        disabled={switching}
        onClick={importer.start}
        aria-describedby="home-dropzone-hint"
        className={cn(
          'flex w-full flex-col items-center rounded-lg px-6 py-(--home-dz-pad)',
          'disabled:cursor-not-allowed disabled:opacity-40',
          dropZoneClass(dragging),
        )}
      >
        <span
          aria-hidden
          className={cn(
            'flex size-10 items-center justify-center rounded-lg text-ink-2',
            dragging ? 'bg-surface text-accent' : 'bg-surface-hover',
          )}
        >
          <FileCodeCorner size={ICON_SIZE.lg} />
        </span>
        <span className="type-heading mt-4">
          {dragging ? t('home.returning.dropRelease') : t('home.returning.dropTitle')}
        </span>
        <span id="home-dropzone-hint" className="mt-2 flex flex-col items-center gap-0.5 text-base text-ink-2">
          {/* 拿得到真实路径（macOS 桌面壳）才说「拖进来就开」；否则如实说还要再选一次 */}
          <span>{t(importer.native ? 'home.returning.dropHint' : 'home.returning.dropHintPicker')}</span>
          <span>
            {t('home.returning.dropOr')}
            <span className="underline underline-offset-2">{t('home.returning.dropChoose')}</span>
          </span>
        </span>
      </button>
    </Card>
  )
}

/* --------------------------------- 新手版 ---------------------------------- */

function Newcomer({ importer, dragging }: { importer: Importer; dragging: boolean }) {
  const { t } = useTranslation('project')
  const entry = useOnboardingStore((s) => tutorialEntry(s.status))
  const switching = useProjectStore((s) => s.switching)
  return (
    <>
      <header className="flex flex-col items-center gap-(--home-gap-tight) text-center">
        <Wordmark size="sm" />
        <h1 className="type-display">{t('home.newcomer.title')}</h1>
      </header>

      <DropZone importer={importer} dragging={dragging} />
      {importer.noticeView}

      {/* 这一屏唯一的填色主动作（EmptyState v2 的约定：一屏一颗、32px） */}
      <div className="flex flex-wrap justify-center gap-3">
        <Button
          variant="primary"
          size="lg"
          disabled={switching}
          data-home-import
          onClick={importer.start}
        >
          <FolderOpen size={ICON_SIZE.md} />
          {t('home.newcomer.import')}
        </Button>
        {/* 三步原生教程（ADR 0136）：内置演示、不依赖后端的教程资源，所以总是可用 */}
        <Button
          variant="secondary"
          size="lg"
          disabled={switching}
          data-onboarding-anchor="tutorial-entry"
          onClick={() => void runTutorialEntry('picker')}
        >
          <Play size={ICON_SIZE.md} />
          {t(entry === 'resume' ? 'home.newcomer.tryResume' : 'home.newcomer.tryStart')}
        </Button>
      </div>
    </>
  )
}

/* --------------------------------- 老手版 ---------------------------------- */

function Returning({
  importer,
  dragging,
  openPath,
}: {
  importer: Importer
  dragging: boolean
  openPath: (path: string) => Promise<boolean>
}) {
  const { t } = useTranslation('project')
  const sample = useSampleAvailability()
  return (
    <>
      <header className="flex flex-col items-center gap-(--home-gap-tight) text-center">
        <h1>
          <Wordmark />
        </h1>
        <Narrative importer={importer} openPath={openPath} />
      </header>

      <DropZone importer={importer} dragging={dragging} />
      {importer.noticeView}

      {!sample.hidden && (
        <div className="flex flex-col items-center">
          <Button
            variant="secondary"
            size="lg"
            disabled={sample.disabled}
            loading={sample.opening}
            loadingLabel={t('picker.tutorialOpening')}
            data-home-sample
            onClick={() => void openSampleProject()}
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

/**
 * 品牌标 + 产品名（两处都来自品牌常量；图形是装饰，名字是文字）。老手版里它就是页面标题（type-display）；
 * 新手版里页面标题是那句大问题，品牌退一档（type-heading），同一屏不出现两个 24px。
 */
function Wordmark({ size = 'md' }: { size?: 'sm' | 'md' }) {
  return (
    <span className="flex items-center justify-center gap-3">
      <BrandMark size={size === 'md' ? 40 : 32} tone="paper" />
      <span className={size === 'md' ? 'type-display' : 'type-heading'}>{PRODUCT_NAME}</span>
    </span>
  )
}

/**
 * 叙事句（2026-10-07 设计审计 §4.2，学 OpenBitFun WelcomePanel）：一句 15px 的话里嵌两枚 24px 行内 chip——
 * 「继续 [最近项目] ，或 [打开脚本] 开始新的排版。」chip 就是入口本身，比两颗并排的大按钮更像一句话。
 * 最近项目 chip 直接打开**最近打开的那一个**（更多的在下面的卡片网格里，不再套一层下拉）；
 * 没有可打开的最近项目时句子只剩脚本那一半。句式整句进翻译（`Trans`），语序各语言自己定。
 */
function Narrative({ importer, openPath }: { importer: Importer; openPath: (path: string) => Promise<boolean> }) {
  const { t } = useTranslation('project')
  const recent = useProjectStore((s) => s.recent.find((r) => r.exists))
  const switching = useProjectStore((s) => s.switching)
  const script = (
    <NarrativeChip data-home-chip="script" disabled={switching} onClick={importer.start}>
      <FileCodeCorner size={ICON_SIZE.sm} aria-hidden className="text-ink-3" />
      {t('home.narrative.script')}
    </NarrativeChip>
  )
  return (
    <p className="text-xl leading-8 text-ink-2" data-home-narrative>
      {recent ? (
        <Trans
          t={t}
          i18nKey="home.narrative.withRecent"
          components={{
            recent: (
              <NarrativeChip
                data-home-chip="recent"
                disabled={switching}
                aria-label={t('picker.openProject', { name: recent.name })}
                title={recent.tutorial ? undefined : recent.path}
                onClick={() => void openPath(recent.path)}
              >
                <Folder size={ICON_SIZE.sm} aria-hidden className="text-ink-3" />
                <span className="max-w-[24ch] truncate">{recent.name}</span>
              </NarrativeChip>
            ),
            script,
          }}
        />
      ) : (
        <Trans t={t} i18nKey="home.narrative.scriptOnly" components={{ script }} />
      )}
    </p>
  )
}

/** 叙事句里的行内 chip：24px 胶囊、白底，坐在一行字的基线上（宪法第二节：chip 是 full 圆角） */
function NarrativeChip({
  className,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & Record<`data-${string}`, string>) {
  return (
    <button
      type="button"
      {...rest}
      className={cn(
        'mx-0.5 inline-flex h-6 max-w-full items-center gap-1.5 rounded-full bg-surface px-2.5 align-middle text-base text-ink',
        'outline-1 -outline-offset-1 outline-transparent transition-[outline-color,background-color]',
        'hover:outline-border focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-40',
        className,
      )}
    >
      {children}
    </button>
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
  openPath: (path: string) => Promise<boolean>
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
      className="text-ink-2"
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
    return <div className="flex justify-center">{allLink}</div>
  }

  return (
    <section
      aria-labelledby="home-recent-heading"
      className="mx-auto w-full max-w-[720px]"
    >
      <div className="flex items-center justify-between">
        <h2 id="home-recent-heading" className="type-section flex items-baseline gap-1.5">
          {t('picker.recentHeading')}
          <span className="font-normal text-ink-3">{t('picker.recentCount', { count: recent.length })}</span>
        </h2>
        {allLink}
      </div>
      {/* Card interactive 网格（2026-10-07 设计审计 §4.2 / §5）：此前新手版是三列 hover 行、老手版是带分隔线的列表 */}
      {/* 居中的等宽卡（flex-wrap + 固定列宽，不用 grid）：1–2 个项目不再孤零零靠左、右边空一大块——
          整页是居中的一列（Logo / 叙事句 / 拖放区 / 示例按钮），卡片跟着居中；列宽仍按 1 / 2 / 3 列等分，
          列数随视口：窄 1 列、sm 2 列、宽 3 列 */}
      <ul
        data-home-recent-list
        className="mt-2 flex flex-wrap justify-center gap-3"
      >
        {shown.map((r) => (
          <RecentItem
            key={r.path}
            entry={r}
            detail={variant === 'newcomer' ? 'when' : 'path'}
            columns={variant === 'newcomer' ? 'lg3' : 'sm3'}
            busy={busyPath === r.path}
            disabled={switching}
            onOpen={() => void openPath(r.path)}
            onRemove={() => void remove(r.path)}
          />
        ))}
      </ul>
    </section>
  )
}

/**
 * 主页上的一个最近项目：一张 `Card` interactive。主体是一颗「打开」按钮（缩略图格 + 名字 + 路径 / 时间），
 * 右上角 ⋯ 菜单收着「打开 / 从列表移除」；「全部项目」里照旧有行尾的 ×。
 * 缩略图格：最近项目列表（`RecentProject`）不带任何画布数据，画不出 `CanvasThumb`——放一块画布灰上的
 * 文件夹图标（项目是目录，不是单个脚本）；后端哪天给了首页画布的缩略数据，换成 `CanvasThumb` 就在这一格。
 * 时间是**上次打开**的时间（`last_opened`，后端 `touch_recent` 记的），不是文件修改时间。
 */
function RecentItem({
  entry,
  detail,
  columns,
  busy,
  disabled,
  onOpen,
  onRemove,
}: {
  entry: RecentProject
  /** 卡片第二行：新手版说「打开于 …」，老手版给路径（时间放在第三行） */
  detail: 'when' | 'path'
  /** 三列在哪个断点出现：老手版 md（768），新手版 lg（1024） */
  columns: 'sm3' | 'lg3'
  busy: boolean
  disabled: boolean
  onOpen: () => void
  onRemove: () => void
}) {
  const { t } = useTranslation('project')
  const when = entry.last_opened > 0 ? formatRelativeTime(entry.last_opened) : null
  const sub = entry.tutorial ? (
    <span className="block text-sm text-ink-3">{t('picker.tutorialBadge')}</span>
  ) : detail === 'path' ? (
    <TailPath path={entry.path} />
  ) : when ? (
    <span className="block text-sm text-ink-3">{t('home.openedAgo', { when })}</span>
  ) : null
  return (
    <li
      data-home-recent={entry.path}
      className={cn(
        'min-w-0 max-w-full w-full sm:w-[calc((100%-0.75rem)/2)]',
        columns === 'sm3' ? 'md:w-[calc((100%-1.5rem)/3)]' : 'lg:w-[calc((100%-1.5rem)/3)]',
      )}
    >
      <Card interactive lift disabled={busy || disabled} padding="none" className="group h-full min-w-0 max-w-full">
        <button
          type="button"
          onClick={onOpen}
          disabled={busy || disabled}
          aria-label={t('picker.openProject', { name: entry.name })}
          title={entry.tutorial ? undefined : entry.path}
          className="block h-full w-full min-w-0 max-w-full overflow-hidden rounded-lg p-1 pb-3 text-left outline-none disabled:cursor-not-allowed disabled:opacity-40"
        >
          {/* button 只做 block 外壳，flex 排版放进里面这层：旧 WebKit（macOS 14 / Safari 17）里 <button> 当 flex 容器时
              子项不按容器宽拉伸、按内容宽度排（缩略图格塌成细条、长路径撑出卡外） */}
          <span className="flex h-full w-full min-w-0 max-w-full flex-col gap-2">
            {/* 外 12 = 内 8 + 4：缩略图格是 md 圆角、坐在画布灰上 */}
            <span aria-hidden className="flex h-20 w-full shrink-0 items-center justify-center rounded-md bg-canvas text-ink-3">
              <Folder size={ICON_SIZE.lg} />
            </span>
            <span className="flex w-full min-w-0 max-w-full flex-col gap-0.5 overflow-hidden px-2">
              <span className="flex items-center gap-1.5 pr-6">
                <span className="truncate text-base font-medium text-ink">{entry.name}</span>
                {busy && <span className="shrink-0 text-sm text-ink-3">{t('picker.opening')}</span>}
              </span>
              {sub}
              {detail === 'path' && when && <span className="type-meta">{when}</span>}
            </span>
          </span>
        </button>
        <div className="absolute right-1.5 top-1.5">
          <Menu
            align="end"
            width={180}
            trigger={
              <IconButton
                iconSize="sm"
                tip={false}
                label={t('home.recentMenu', { name: entry.name })}
                // 平时收起、悬停 / 聚焦 / 菜单开着时出现（坐在缩略图格上，白底才看得清）
                className={cn(
                  'bg-surface text-ink-3 opacity-0 transition-[opacity,background-color] hover:bg-surface-hover focus-visible:opacity-100 data-[state=open]:opacity-100',
                  // 卡置灰（切换项目 / 正在打开）时 hover 不再把它揭出来：置灰的卡不应有任何「活着」的 hover 反应
                  !(busy || disabled) && 'group-hover:opacity-100',
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
        </div>
      </Card>
    </li>
  )
}
