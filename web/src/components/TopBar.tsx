import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Check,
  ChevronDown,
  CircleAlert,
  Download,
  Ellipsis,
  Redo2,
  Undo2,
  RotateCcwClock,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  newBlankDocument,
  openLayoutDocument,
  openRecentDocument,
  setDocumentName,
  toggleTimeline,
} from '@/store/actions'
import { requestRelinkMissing } from '@/lib/clipboard'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { createPackage, openPackage } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { foreignProjectLabel } from '@/lib/projectLabel'
import { currentProjectId } from '@/lib/session'
import { bindingForProject } from '@/lib/projectFile'
import { useProjectStore } from '@/store/projectStore'
import { HomeButton, ProjectSwitcher } from './ProjectSwitcher'
import { useWriteBackMenuEntry } from './inspector/UpdateSourceButton'
import { usePalette } from '@/components/CommandPalette'
import { runTutorialEntry, tutorialEntry } from '@/lib/onboarding/tutorial'
import { refreshProjectNow } from '@/store/liveSync'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { discardLocalCopy, recoverLocalCopy, useDocumentStore } from '@/store/documentStore'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useUiStore } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { BrandMark } from './ui/BrandMark'
import { Button, IconButton } from './ui/Button'
import { Menu, MenuItem, MenuLabel, MenuSeparator } from './ui/Menu'
import { TextInput } from './ui/Input'
import { Tip } from './ui/Tooltip'
import { MOD, cn } from '@/lib/utils'
import { msg } from '@/i18n'
import { formatTime } from '@/i18n/format'
import { useFormatMessage } from '@/i18n/react'

export function TopBar() {
  // 写回原始文件住在「⋯」第一项；它的窗口挂在菜单外面（菜单一合上，项就卸载）
  const writeBack = useWriteBackMenuEntry()
  return (
    // `data-topbar`：顶栏的稳定机器标识（e2e 量它里面的按钮，不认 <header> 标签）
    // 窄于 900：三段之间的空隙与两侧内边距收紧（12 → 6、12 → 8），左 / 右段内部的按钮间距也收到 4。界面外观换新（#756）之后按钮
    // 与图标钮的内边距变大，600 宽下三段合起来溢出十几 px（时钟 / 书签钮压到撤销、标注钮压到缩放），
    // 空隙是唯一不改控件尺寸就能让出来的量（e2e/topbar-narrow.spec.ts）
    <header
      data-topbar
      className="flex h-11 shrink-0 items-center justify-between gap-3 bg-surface px-3 max-[899px]:gap-1.5 max-[899px]:px-2"
    >
      <div className="flex min-w-0 flex-1 items-center gap-1.5 max-[899px]:gap-1">
        {/* 回到项目列表：左上角是「离开这里」的位置（桌面壳用系统标题栏，红绿灯不在网页里） */}
        <HomeButton />
        <Brand />
        {/* 项目（图库目录）→ 文档（画布）：从大到小，与对象层级一致 */}
        <ProjectSwitcher />
        {/* 项目 / 文档是一条面包屑（2026-09-15 打磨批次 F）：一个斜杠，不是竖线加两个 chip */}
        <span aria-hidden className="type-meta shrink-0 select-none">
          /
        </span>
        <DocumentMenu />
        <SaveStateLabel />
        <TimelineButton />
        <RecoveryNotice />
      </div>

      <ToolCluster />

      <div className="flex min-w-0 flex-1 items-center justify-end gap-2 max-[899px]:gap-1">
        {/* 缩放进了画布标签行、画布工具进了画布底部的浮动工具条（2026-09-30 重设计 A1）；
            导出是顶栏唯一填色主动作 */}
        <ExportButton />
        <MoreMenu writeBackItem={writeBack.item} />
        {writeBack.dialog}
      </div>
    </header>
  )
}

/**
 * 导出可复现项目包（.tavotto）：布局 + 引用素材 + 源脚本 + 清单。
 *
 * **它从导出对话框搬到了这里**（Prompt 12 §五）：打包出的是一个"项目"，
 * 不是一张图，和「导出这张图」放在同一个弹窗里让两件事互相冒充。搬走
 * 不等于砍掉——导入就在下面一行，一进一出终于在同一个菜单里。
 */
async function exportPackage() {
  const ui = useUiStore.getState()
  const doc = useDocumentStore.getState()
  try {
    const res = await createPackage(doc.projectMeta.name || doc.doc.name, doc.buildProject(), {})
    ui.setStatus(msg('status.packaged', { name: res.name, count: res.assets }, 'workspace'), 'done')
  } catch (e) {
    ui.setStatus(
      msg(
        'status.packageFailed',
        { error: e instanceof Error ? e.message : String(e) },
        'workspace',
      ),
      'error',
    )
  }
}

/**
 * 导入可复现项目包：选 zip → 后端检视（不写入图库）→ 作为新文档打开；
 * 有缺失素材时接到统一的重新链接对话框，绝不静默出空面板。
 */
function importPackage() {
  const input = document.createElement('input')
  input.type = 'file'
  // 包是 .tavotto（zip 容器）。`.zip` 一起收在 accept 里：检视端点按结构判断、
  // 不看扩展名，用户手里那些别的后缀的包因此仍选得中、打得开。
  // `.magplot` 是 0.7 时代导出的同结构包（P1-08 迁移路的一部分）：读取端
  // 本来就打得开，只有这个文件选择器会把它滤掉——所以列进来。写出永远是
  // .tavotto，这不是运行时兼容层回潮。
  input.accept = '.tavotto,.magplot,.zip,application/zip'
  input.onchange = async () => {
    const file = input.files?.[0]
    if (!file) return
    const ui = useUiStore.getState()
    try {
      const res = await openPackage(file)
      await openLayoutDocument(res.doc)
      const missing = requestRelinkMissing()
      const drift = res.drifted.length
      if (!missing && !drift) {
        ui.setStatus(
          msg('status.packageOpened', { createdAt: res.manifest.created_at ?? '' }, 'workspace'),
          'done',
        )
      } else if (drift) {
        ui.setStatus(msg('status.packageDrift', { count: drift }, 'workspace'), 'error')
      }
    } catch (e) {
      ui.setStatus(
        msg(
          'status.packageOpenFailed',
          { error: e instanceof Error ? e.message : String(e) },
          'workspace',
        ),
        'error',
      )
    }
  }
  input.click()
}

/** 图形标 + 实时文字 */
function Brand() {
  return (
    <span className="flex shrink-0 items-center gap-2 text-sm font-medium tracking-tight text-ink">
      <BrandMark size={20} />
      {/* 窄于 900 只留标志：顶栏左段要给面包屑与时间线两颗钮让地方（ADR 0101 §8） */}
      <span className="max-[899px]:sr-only">{PRODUCT_NAME}</span>
    </span>
  )
}

/**
 * 保存状态：贴着文档名，**报告的是保存状态机的当前状态**（R-06），
 * 不是从 `dirty` 布尔现推的两句话。
 *
 * 改造前这里只有两种说法：「保存中…」和「已自动保存 14:03」——而
 * 「保存中…」同时表示"有未保存修改"、"正在写盘"和"刚打开还没存过"三件事，
 * 「已自动保存 14:03」在写盘失败之后照样显示（`dirty` 被 flush 清掉了，
 * 失败只派了一个 4.5 秒后消失的事件）。用户看不出磁盘上到底是哪一版。
 */
/**
 * 「发现未恢复的编辑」：贴在保存状态右侧的一句话 + 两个出口，不再是整条横幅
 * （2026-09-11 用户反馈）。摘要（几张画布、几个对象、存于几点）走 title。
 */
function RecoveryNotice() {
  const { t } = useTranslation('workspace')
  const notice = useDocumentStore((s) => s.docNotice)
  if (notice?.kind !== 'recovery') return null
  const s = notice.summary
  return (
    <span
      role="status"
      className="inline-flex shrink-0 items-center gap-1.5 text-xs text-ink-2"
      // 文档名是用户内容，作为插值原样透出
      title={t('docBanner.recoveryBody', {
        name: s.name,
        canvases: s.canvases,
        objects: s.objects,
        time: formatTime(s.savedAt),
      })}
    >
      <RotateCcwClock size={ICON_SIZE.xs} className="shrink-0 text-ink-3" aria-hidden />
      <span className="truncate">{t('docBanner.recoveryTitle')}</span>
      <Button size="sm" variant="secondary" onClick={() => void recoverLocalCopy()}>
        {t('docBanner.recover')}
      </Button>
      <Button size="sm" onClick={discardLocalCopy}>
        {t('docBanner.keepMain')}
      </Button>
    </span>
  )
}

/**
 * 这份排版此刻能写回的项目文件（ADR 0096）。项目换了要重新判一次：订阅
 * `projectStore` 的项目 id 只为触发重渲染，判据本身用 `currentProjectId()`
 * （与 ⌘S 那条路同一个值——换代窗口里 `project` 字段还是旧的，#589）。
 */
function useActiveProjectFile() {
  useProjectStore((s) => s.project?.id ?? null)
  const binding = useDocumentStore((s) => s.projectFile)
  return bindingForProject(binding, currentProjectId())
}

/**
 * 「有未保存修改 / 正在保存…」要持续这么久才说出来（2026-10-07 审计 P0）。自动保存防抖 1 s，
 * 一次快的写盘从改动到落定只要一瞬——不等这一下的话顶栏每秒在三句话之间翻一轮。
 */
export const SAVE_PENDING_REVEAL_MS = 600

/** `on` 连续为真满 `ms` 才变真；一变假立刻变假。 */
function useHeldFor(on: boolean, ms: number): boolean {
  const [held, setHeld] = useState(false)
  useEffect(() => {
    if (!on) {
      setHeld(false)
      return
    }
    const timer = setTimeout(() => setHeld(true), ms)
    return () => clearTimeout(timer)
  }, [on, ms])
  return on && held
}

/**
 * 顶栏的保存状态。两根轴（ADR 0096）：`saveState` 说本机自动保存走到哪一步；
 * 绑定的项目文件说「项目里那份是不是最新」。落定之后的那句话说**去向**——
 * 「已保存到项目」「已存在本机」，tooltip 给出项目里的相对路径。
 *
 * 三条安静纪律（2026-10-07 审计 P0：自动保存每秒一轮，此前标签跟着每秒换一次字、换一次宽、
 * 播报一次）：
 *   1. 本机自动保存的「有未保存修改 / 正在保存…」持续满 `SAVE_PENDING_REVEAL_MS` 才说，
 *      快的那一轮里仍显示上一句落定的话；
 *   2. 宽度按所有会轮到的那几句里最宽的那句占位（同格叠放、只有当前那句可见），换字不推邻居；
 *   3. 读屏播报只在进入「保存失败 / 外部冲突」时说（单独的 sr-only 区），平常的保存一轮不打扰。
 */
export function SaveStateLabel() {
  const { t } = useTranslation('workspace')
  const saveState = useDocumentStore((s) => s.saveState)
  const lastPersisted = useDocumentStore((s) => s.lastPersisted)
  const hasContent = useDocumentStore(
    (s) => s.doc.objects.length > 0 || s.doc.guides.length > 0 || s.canvases.length > 1,
  )
  const bound = useActiveProjectFile()
  const projectOpen = useProjectStore((s) => !!s.project?.open) && currentProjectId() !== null
  const localPending = saveState === 'dirty' || saveState === 'saving'
  const held = useHeldFor(localPending, SAVE_PENDING_REVEAL_MS)
  if (!hasContent && !bound) return null
  // 还从没落过盘（新排版的第一次改动）：没有「上一句落定的话」可以继续显示，
  // 不等 600ms 直接说进行中——否则会在第一轮自动保存完成前就声称「已存在本机」
  const neverPersisted = !bound && !lastPersisted && saveState !== 'saved'
  const revealPending = held || (localPending && neverPersisted)

  const settled = saveState === 'clean' || saveState === 'saved'
  // 落定时那句话（也是快的一轮里继续显示的那句）
  const settledText = bound
    ? t(bound.dirty ? 'topbar.saveProjectPending' : 'topbar.saveProjectSaved')
    : saveState === 'saved'
      ? t('topbar.saveLocal')
      : lastPersisted
        ? t('topbar.saveLocalAt', { time: formatTime(lastPersisted) })
        : t('topbar.saveLocal')
  const text =
    saveState === 'save_error'
      ? t('topbar.saveError')
      : saveState === 'conflict'
        ? t('topbar.saveConflict')
        : revealPending
          ? t(saveState === 'saving' ? 'topbar.saveSaving' : 'topbar.saveDirty')
          : settledText
  const bad = saveState === 'save_error' || saveState === 'conflict'
  const title = bound
    ? t(bound.dirty || !settled ? 'topbar.saveTitleProjectPending' : 'topbar.saveTitleProject', {
        file: bound.file,
        mod: MOD,
      })
    : t(projectOpen ? 'topbar.saveTitleLocalInProject' : 'topbar.saveTitleLocal', { mod: MOD })

  // 窄于 900 时文字收成一个状态点（文字仍在，只是只给读屏；悬停气泡照旧）：
  // 顶栏左段放不下「新文档 · 已自动保存 14:03」再加时间线两颗钮，文字不收的话
  // 会被右边的按钮压住（#677 集成时 600 宽下实测重叠，ADR 0101 §8）
  // 项目文件落后于这份排版（ADR 0096）也算「还没落定」
  const pending = revealPending || !!bound?.dirty
  // 占位：会在本机自动保存一轮里轮到的那几句（落定那句 + 两句进行中）。叠在同一格里，
  // 格宽取最宽那句——只是给宽度，不可见、不给读屏
  // 本机那句落定话在 saved（不带时间）与快的一轮里（带上次落盘时间）是两种写法，两种都占位，
  // 否则 saved → dirty 时格宽跟着变
  const localSettled = bound
    ? []
    : [t('topbar.saveLocal'), ...(lastPersisted ? [t('topbar.saveLocalAt', { time: formatTime(lastPersisted) })] : [])]
  const reserve = [...new Set([settledText, ...localSettled, t('topbar.saveDirty'), t('topbar.saveSaving')])].filter(
    (r) => r !== text,
  )
  return (
    <span
      data-save-state
      data-save-destination={bound ? 'project' : 'local'}
      data-save-shown={bad ? saveState : revealPending ? saveState : 'settled'}
      className={cn('flex min-w-0 shrink items-center gap-1 text-xs', bad ? 'text-danger' : 'text-ink-3')}
      // 窄时文字只给读屏，悬停气泡要连状态一起说
      title={`${text} · ${title}`}
    >
      <span
        aria-hidden
        className={cn(
          'size-1.5 shrink-0 rounded-full min-[900px]:hidden',
          bad ? 'bg-danger' : pending ? 'bg-ink-3' : 'bg-ok',
        )}
      />
      {/* 落定 = ✓，出错 = 叹号，进行中留空位（不转圈：它本来就只在慢的那一轮出现）。
          位置常驻，换状态时文字不左右跳 */}
      <span aria-hidden className="hidden size-3 shrink-0 items-center justify-center min-[900px]:flex">
        {bad ? (
          <CircleAlert size={ICON_SIZE.xs} />
        ) : !pending ? (
          <Check size={ICON_SIZE.xs} />
        ) : null}
      </span>
      <span className="grid min-w-0">
        <span data-save-text className="sr-only col-start-1 row-start-1 min-[900px]:not-sr-only min-[900px]:truncate">
          {text}
        </span>
        {reserve.map((r) => (
          <span
            key={r}
            aria-hidden
            className="invisible col-start-1 row-start-1 hidden truncate min-[900px]:block"
          >
            {r}
          </span>
        ))}
      </span>
      {/* 读屏只在出事时说：标签本身不再 aria-live（每秒一轮的保存不该每秒播一次）。
          不带 role（`role=status` 全产品只留通知轨那一个） */}
      <span aria-live="polite" data-save-live className="sr-only">
        {bad ? text : ''}
      </span>
    </span>
  )
}

/**
 * 排版时间线的常驻入口（ADR 0101）：保存状态旁一颗时钟钮。
 *
 * 用户反馈「不知道有这个功能」——它此前只在排版菜单的第六项里。放在保存状态旁边，
 * 是因为两者回答的是同一件事的两半：「存到哪一步了」与「能回到哪一步」。
 * 抽屉开着时按下态（`aria-pressed`），再点一下收起。
 */
function TimelineButton() {
  const { t } = useTranslation('workspace')
  const open = useUiStore((s) => s.versionsOpen)
  return (
    <IconButton
      label={t('topbar.timelineButton')}
      shortcut={`⇧${MOD}H`}
      aria-pressed={open}
      data-timeline-button
      className={cn('shrink-0', open && 'bg-selected')}
      onClick={toggleTimeline}
    >
      <RotateCcwClock size={ICON_SIZE.md} className="text-ink-2" />
    </IconButton>
  )
}

export function DocumentMenu() {
  const { t } = useTranslation('workspace')
  const name = useDocumentStore((s) => s.projectMeta.name)
  const documentId = useDocumentStore((s) => s.documentId)
  const recentDocs = useDocumentStore((s) => s.recentDocs)
  const projectFile = useActiveProjectFile()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(name)

  useEffect(() => setDraft(name), [name])

  const recent = useMemo(
    () => recentDocs.filter((r) => r.id !== documentId),
    [recentDocs, documentId],
  )

  if (editing) {
    return (
      <TextInput
        autoFocus
        value={draft}
        aria-label={t('topbar.documentName')}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          setEditing(false)
          if (draft.trim() && draft !== name) setDocumentName(draft.trim())
        }}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'Escape') {
            setDraft(name)
            setEditing(false)
          }
        }}
        // 可编辑框只有一副（宪法第五节）：聚焦态由 fieldBox 给，不自己画一圈 accent 实线。
        // 画布页签的重命名早就是 TextInput，这里是最后一处手写的（2026-09-15 打磨 T4）
        className="w-40"
      />
    )
  }

  return (
    <Menu
      trigger={
        <Button size="md" className="min-w-0 max-w-52 shrink text-ink-2" aria-label={t('topbar.documentLabel', { name })}>
          <span className="truncate">{name}</span>
          {/* 项目里的文件落后于这份排版（ADR 0096）：排版名旁一颗圆点（画布页签上不再有「未保存」点，2026-10-07） */}
          {projectFile?.dirty && (
            <span
              data-project-file-dirty
              aria-label={t('topbar.projectFileUnsaved', { file: projectFile.file })}
              title={t('topbar.projectFileUnsaved', { file: projectFile.file })}
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-ink-3"
            />
          )}
          <ChevronDown size={ICON_SIZE.xs} className="shrink-0 text-ink-3" />
        </Button>
      }
    >
      <MenuItem onSelect={() => setEditing(true)}>{t('topbar.renameDocument')}</MenuItem>
      <MenuItem onSelect={newBlankDocument}>{t('topbar.newBlankDocument')}</MenuItem>

      <MenuSeparator />
      <MenuLabel>{t('topbar.projectDocuments')}</MenuLabel>
      <MenuItem
        onSelect={() => useUiStore.getState().setLayoutOpen(true, 'save')}
        shortcut={`⇧${MOD}S`}
      >
        {t('topbar.saveDocumentAs')}
      </MenuItem>
      <MenuItem onSelect={() => useUiStore.getState().setLayoutOpen(true, 'load')}>
        {t('topbar.openDocument')}
      </MenuItem>
      <MenuItem onSelect={() => useUiStore.getState().setVersionsOpen(true)} shortcut={`⇧${MOD}H`}>
        {t('topbar.versionTimeline')}
      </MenuItem>
      <MenuItem onSelect={() => void exportPackage()}>{t('topbar.exportPackage')}</MenuItem>
      <MenuItem onSelect={importPackage}>{t('topbar.importPackage')}</MenuItem>

      <MenuSeparator />
      <MenuLabel>{t('topbar.recentDocuments')}</MenuLabel>
      {recent.length === 0 ? (
        <MenuItem disabled>{t('topbar.noOtherDocuments')}</MenuItem>
      ) : (
        recent.map((r) => (
          <MenuItem
            key={r.id}
            onSelect={() => openRecentDocument(r.id)}
            shortcut={formatTime(r.savedAt)}
          >
            {/* 文档名是用户内容，作为插值原样透出 */}
            {(r.canvases ?? 1) > 1
              ? t('topbar.recentEntryMulti', {
                  name: r.name,
                  canvases: r.canvases,
                  count: r.objects,
                })
              : t('topbar.recentEntry', { name: r.name, count: r.objects })}
            {/* 索引跨项目共用一份：别的项目的文档要标出来，否则用户会把它当成
                本项目的一份版本打开，而它引用的素材在这个项目里根本不存在
                （审计 T04）。归属未记的旧条目**什么都不标**——那是「不知道」。 */}
            {foreignProjectLabel(r, currentProjectId()) && (
              <span className="ml-1 shrink-0 text-xs text-ink-faint">
                {foreignProjectLabel(r, currentProjectId())}
              </span>
            )}
          </MenuItem>
        ))
      )}
    </Menu>
  )
}

function ToolCluster() {
  const { t } = useTranslation(['workspace', 'common'])
  const fmt = useFormatMessage()
  const canUndo = useDocumentStore((s) => s.past.length > 0)
  const canRedo = useDocumentStore((s) => s.future.length > 0)
  const undoLabel = useDocumentStore((s) => s.past.at(-1)?.label)
  const redoLabel = useDocumentStore((s) => s.future[0]?.label)

  // 必须走带 undoRedoBlocked 守卫的入口：拖动进行中点撤销会把事务当场结算，
  // 后续位移绕过历史（真实撞见过的数据损坏路径）
  const runUndo = () => runUndoRedo(false)
  const runRedo = () => runUndoRedo(true)

  return (
    <div className="flex shrink-0 items-center gap-0.5">
      <Tip
        label={
          undoLabel
            ? t('workspace:topbar.undoWith', { label: fmt(undoLabel) })
            : t('workspace:topbar.undo')
        }
        shortcut={`${MOD}Z`}
      >
        <Button
          size="icon"
          disabled={!canUndo}
          onClick={runUndo}
          aria-label={t('workspace:topbar.undo')}
        >
          <Undo2 size={ICON_SIZE.md} />
        </Button>
      </Tip>
      <Tip
        label={
          redoLabel
            ? t('workspace:topbar.redoWith', { label: fmt(redoLabel) })
            : t('workspace:topbar.redo')
        }
        shortcut={`⇧${MOD}Z`}
      >
        <Button
          size="icon"
          disabled={!canRedo}
          onClick={runRedo}
          aria-label={t('workspace:topbar.redo')}
        >
          <Redo2 size={ICON_SIZE.md} />
        </Button>
      </Tip>
    </div>
  )
}

function ExportButton() {
  const { t } = useTranslation('workspace')
  return (
    <Tip label={t('topbar.exportTip')} shortcut={`${MOD}E`}>
      <Button
        variant="primary"
        size="md"
        data-onboarding-anchor="export"
        onClick={() => useUiStore.getState().setExportOpen(true)}
      >
        <Download size={ICON_SIZE.md} />
        {/* 窄于 900 只留图标：文字进读屏（sr-only），按钮的可达名不变 */}
        <span className="max-[899px]:sr-only">{t('topbar.export')}</span>
      </Button>
    </Tip>
  )
}

/** 低频全局动作收进「更多」：样式 / 版本 / 画布设置 / 帮助 */
function MoreMenu({ writeBackItem }: { writeBackItem: ReactNode }) {
  const { t } = useTranslation('workspace')
  const ui = () => useUiStore.getState()
  // 打招呼的是启动时那个 UpdateNoticeDialog（「稍后」按版本记住）；这里只在
  // 「更多」上点一个圆点、菜单里给一条入口——用户说过稍后之后仍留着的安静提醒。
  const update = useUpdateStore((s) => s.status)
  const desktopUpdate = useUpdateStore((s) => s.desktopUpdate)
  const hasUpdate = !!update?.update_available || !!desktopUpdate
  const latest = desktopUpdate?.version ?? update?.latest
  const tutorialKind = useOnboardingStore((s) => tutorialEntry(s.status))
  return (
    <Menu
      width={196}
      // 从触发钮右缘垂下（与缩放菜单同一规则）：默认 align=start 时 Radix 碰到视口右缘
      // 会把整块贴到窗口边上，与顶栏 12 的内边距不齐（2026-09-15 打磨 T3）
      align="end"
      trigger={
        // data-more-menu：e2e 的稳定锚点，写回入口住在这个菜单里、要先打开它
        <Button size="icon" data-more-menu aria-label={t(hasUpdate ? 'topbar.moreWithUpdate' : 'topbar.more')}>
          <span className="relative">
            <Ellipsis size={ICON_SIZE.md} />
            {hasUpdate && (
              <span
                aria-hidden
                className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-ink"
              />
            )}
          </span>
        </Button>
      }
    >
      {writeBackItem}
      <MenuSeparator />
      {hasUpdate && (
        <>
          <MenuItem onSelect={() => ui().setSettingsOpen(true, 'about')}>
            <span className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-ink" aria-hidden />
              {t('topbar.updateAvailable', { version: latest })}
            </span>
          </MenuItem>
          <MenuSeparator />
        </>
      )}
      {/* 分隔线只在真分组之间画（2026-09-15 打磨 T5）：
          〔论文样式 · 画布设置〕｜〔刷新项目 · 项目接入状态〕｜〔命令面板 · 快捷键帮助 · 教程〕
          ——七项此前被四条线切成 1/1/2/3 四组，线比组多 */}
      <MenuItem onSelect={() => ui().setStylesOpen(true)}>{t('topbar.paperStyles')}</MenuItem>
      <MenuItem onSelect={() => ui().setRightTab('canvas')}>{t('topbar.canvasSettings')}</MenuItem>
      <MenuSeparator />
      {/* 与命令面板同一批 helper：刷新走统一刷新端点，接入状态走 readiness store */}
      <MenuItem onSelect={() => void refreshProjectNow()}>{t('topbar.refreshProject')}</MenuItem>
      <MenuItem
        onSelect={() => useProjectReadinessStore.getState().openCenter({ source: 'palette' })}
      >
        {t('topbar.readiness')}
      </MenuItem>
      <MenuSeparator />
      <MenuItem onSelect={() => usePalette.getState().setOpen(true)} shortcut={`${MOD}K`}>
        {t('topbar.commandPalette')}
      </MenuItem>
      <MenuItem onSelect={() => ui().setShortcutHelpOpen(true)} shortcut="?">
        {t('topbar.shortcutHelp')}
      </MenuItem>
      {/* 开始 / 继续 / 重新开始教程：文案与动作都来自 lib/onboarding/tutorial，不在这里判状态 */}
      <MenuItem onSelect={() => void runTutorialEntry('help')} data-onboarding-anchor="help-tutorial">
        {t(`topbar.tutorial.${tutorialKind}`)}
      </MenuItem>
    </Menu>
  )
}
