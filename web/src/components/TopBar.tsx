import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Check,
  ChevronDown,
  CircleAlert,
  Download,
  Ellipsis,
  HardDrive,
  Info,
  Lock,
  Redo2,
  Undo2,
  RotateCcwClock,
  TriangleAlert,
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
import { keyOf } from '@/lib/keymap'
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
import {
  discardLocalCopy,
  dismissDocNotice,
  recoverLocalCopy,
  saveNow,
  useDocumentStore,
} from '@/store/documentStore'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useUiStore } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { BrandMark } from './ui/BrandMark'
import { Button, IconButton } from './ui/Button'
import { Menu, MenuItem, MenuLabel, MenuSeparator } from './ui/Menu'
import { Popover } from './ui/Popover'
import { TextInput } from './ui/Input'
import { Tip } from './ui/Tooltip'
import type { StatusTone } from './ui/Notice'
import { ConflictActions, ConflictDetail } from './DocumentBanner'
import { MOD, cn } from '@/lib/utils'
import { msg, t as translate } from '@/i18n'
import { formatTime } from '@/i18n/format'
import { useFormatMessage } from '@/i18n/react'

export function TopBar() {
  // 写回原始文件住在「⋯」第一项；它的窗口挂在菜单外面（菜单一合上，项就卸载）
  const writeBack = useWriteBackMenuEntry()
  return (
    // `data-topbar`：顶栏的稳定机器标识（e2e 量它里面的按钮，不认 <header> 标签）
    // 顶栏坐在灰色桌面上（2026-09-30 重设计）：底色就是桌面色 `bg`，不再由 App 的级联补丁覆盖（2026-10-07 审计 §4.1）。
    // 窄于 900：两段之间的空隙与两侧内边距收紧（12 → 6、12 → 8），段内按钮间距也收到 4——600 宽下仍要放下
    // 首页胶囊 / 面包屑 / 状态芯片 / 时间线 | 撤销重做 / 导出 / 更多（e2e/topbar-narrow.spec.ts）
    <header
      data-topbar
      className="flex h-11 shrink-0 items-center justify-between gap-3 bg-bg px-3 max-[899px]:gap-1.5 max-[899px]:px-2"
    >
      <div className="flex min-w-0 flex-1 items-center gap-1.5 max-[899px]:gap-1">
        {/* 品牌并进「回到项目列表」（2026-10-07 设计审计 §10.1 P2）：左上角一颗胶囊 = 标 + 产品名，点它离开这里。
            窄于 900 只留标（名字给读屏，可达名本来就是「回到项目列表」） */}
        <HomeButton>
          <BrandMark size={18} />
          <span className="font-medium tracking-tight max-[899px]:sr-only">{PRODUCT_NAME}</span>
        </HomeButton>
        {/* 项目（图库目录）→ 文档（画布）：从大到小，与对象层级一致 */}
        <ProjectSwitcher />
        {/* 项目 / 文档是一条面包屑（2026-09-15 打磨批次 F）：一个斜杠，不是竖线加两个 chip */}
        <span aria-hidden className="type-meta shrink-0 select-none">
          /
        </span>
        <DocumentMenu />
        <SaveStateLabel />
        <TimelineButton />
      </div>

      {/* 撤销 / 重做挨着导出（2026-10-07 设计审计 §10.1 P2）：此前孤零零在顶栏正中，离它撤的东西（左边的文档）
          与紧接着要做的事（导出）都远。缩放在画布标签行、画布工具在画布底部的浮动工具条（2026-09-30 A1）；
          导出是顶栏唯一填色主动作 */}
      <div className="flex min-w-0 shrink-0 items-center justify-end gap-2 max-[899px]:gap-1">
        <UndoRedo />
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
 * 需要用户裁决 / 知道的那几件文档级的事，按急迫排第一个（顶栏状态芯片只说一件）：
 * 冲突、保存失败、本机有未恢复的编辑、上次的排版没打开、来自更新版本的排版。
 * 冲突 / 保存失败**另外**在工作面板顶上常驻一条（`DocumentBanner`，丢编辑的事不能只藏在一颗胶囊后面）；
 * 其余三件只在这里——它们不打断编辑，此前却各占一整条横幅把应用往下推（2026-10-07 设计审计 §10.1）。
 */
type DocIssue = 'conflict' | 'save_error' | 'recovery' | 'last_doc' | 'too_new'

const ISSUE_TONE: Record<DocIssue, StatusTone> = {
  conflict: 'warn',
  save_error: 'danger',
  recovery: 'info',
  last_doc: 'neutral',
  too_new: 'neutral',
}

const ISSUE_ICON: Record<DocIssue, typeof Info> = {
  conflict: TriangleAlert,
  save_error: CircleAlert,
  recovery: RotateCcwClock,
  last_doc: Info,
  too_new: Lock,
}

/** 锚点色胶囊（24 高）：底 / 字 / 环都从状态锚点派生，与 `ui/StatusPill` 同一份配色，但它是一颗按钮 */
const PILL_TONE: Record<StatusTone, string> = {
  neutral: 'bg-surface-active text-ink-2 hover:bg-surface-hover',
  info: 'bg-info-surface text-info-content inset-ring inset-ring-info-border',
  ok: 'bg-ok-surface text-ok-content inset-ring inset-ring-ok-border',
  warn: 'bg-warn-surface text-warn-content inset-ring inset-ring-warn-border',
  danger: 'bg-danger-surface text-danger-content inset-ring inset-ring-danger-border',
}

function useDocIssue(): DocIssue | null {
  const saveState = useDocumentStore((s) => s.saveState)
  const notice = useDocumentStore((s) => s.docNotice?.kind ?? null)
  const lastDoc = useProjectStore((s) => !!s.lastDocumentIssue)
  if (saveState === 'conflict') return 'conflict'
  if (saveState === 'save_error') return 'save_error'
  if (notice === 'recovery') return 'recovery'
  // 版本过新排在「上次的排版没打开」之前：启动时那份上次的排版若来自更新的版本，两件会同时在
  // （切项目接回失败记 `lastDocumentIssue`，工作台挂载的 `restoreSession` 读同一份记 `schema_too_new`），
  // 而「打开上次文档」那条重试只会再失败一次——该说的是具体原因
  if (notice === 'schema_too_new') return 'too_new'
  if (lastDoc) return 'last_doc'
  return null
}

/**
 * 顶栏的文档状态芯片（2026-10-07 设计审计 §10.1）。两根轴（ADR 0096）：`saveState` 说本机自动保存走到哪一步；
 * 绑定的项目文件说「项目里那份是不是最新」。
 *
 *   - **落定**：只有一枚图标——✓ = 已保存到项目、硬盘 = 存在本机（项目里那份落后时，文档名旁另有一枚空心环）。
 *     那句话进读屏与悬停气泡，不占顶栏的字宽；
 *   - **进行中**：持续满 `SAVE_PENDING_REVEAL_MS` 才出字（快的一轮里一直是那枚落定图标），字的格宽按会轮到的
 *     那几句里最宽的那句占位（同格叠放、只有当前那句可见），换字不推邻居；
 *   - **出事**（冲突 / 保存失败 / 未恢复的编辑 / 上次的排版没打开 / 版本过新）：锚点色 24px 胶囊，点开是一块
 *     320 宽的说明 + 出口（此前「未恢复的编辑」是贴在保存状态右边的一整句加两颗按钮）；
 *   - **读屏**只在进入「保存失败 / 外部冲突」时播报（单独的 sr-only 区），平常的保存一轮不打扰。
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
  const issue = useDocIssue()
  const localPending = saveState === 'dirty' || saveState === 'saving'
  const held = useHeldFor(localPending, SAVE_PENDING_REVEAL_MS)
  if (!hasContent && !bound && !issue) return null
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
  const shownState = bad ? saveState : revealPending ? saveState : 'settled'
  // 读屏只在出事时说：标签本身不 aria-live（每秒一轮的保存不该每秒播一次）。
  // 不带 role（`role=status` 全产品只留通知轨那一个）
  const live = (
    <span aria-live="polite" data-save-live className="sr-only">
      {bad ? text : ''}
    </span>
  )

  if (issue) {
    return (
      <span
        data-save-state
        data-save-destination={bound ? 'project' : 'local'}
        data-save-shown={shownState}
        data-doc-issue={issue}
        className="flex min-w-0 shrink items-center"
      >
        <IssuePill issue={issue} text={bad ? text : issueLabel(issue)} title={`${text} · ${title}`} />
        {live}
      </span>
    )
  }

  // 落定那枚图标：项目里那份是最新的才打 ✓；只在本机（或项目里那份落后）是一枚硬盘
  const SettledIcon = bound && !bound.dirty ? Check : HardDrive
  // 占位：会在本机自动保存一轮里轮到的那几句（落定那句 + 两句进行中）。叠在同一格里，
  // 格宽取最宽那句——只是给宽度，不可见、不给读屏。落定时整格只给读屏（芯片只剩图标）
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
      data-save-shown={shownState}
      className="flex h-6 min-w-0 shrink items-center gap-1 px-1 text-xs text-ink-3"
      // 落定时字只给读屏，悬停气泡要连状态一起说
      title={`${text} · ${title}`}
    >
      {/* 图标位常驻：落定 = ✓ / 硬盘；进行中留空位（不转圈：它本来就只在慢的那一轮出现），窄于 900 时
          进行中画一颗墨点代替那句字 */}
      <span aria-hidden className="flex size-3.5 shrink-0 items-center justify-center">
        {revealPending ? (
          <span className="size-1.5 rounded-full bg-ink-3 min-[900px]:hidden" />
        ) : (
          <SettledIcon size={ICON_SIZE.sm} />
        )}
      </span>
      <span className={cn('grid min-w-0', !revealPending && 'sr-only')}>
        <span
          data-save-text
          className={cn('col-start-1 row-start-1', revealPending && 'max-[899px]:sr-only min-[900px]:truncate')}
        >
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
      {live}
    </span>
  )
}

/** 出事那几件在胶囊上的短名（冲突 / 保存失败用保存状态那两句，见上） */
function issueLabel(issue: DocIssue): string {
  switch (issue) {
    case 'recovery':
      return msgText('docBanner.recoveryTitle')
    case 'last_doc':
      return msgText('topbar.issueLastDoc')
    case 'too_new':
      return msgText('topbar.issueTooNew')
    default:
      return ''
  }
}
const msgText = (key: string) => translate(key, { ns: 'workspace' })

/**
 * 出事时的那颗胶囊 + 320 宽说明块。胶囊是一颗按钮（`data-doc-status-pill`），说明块里是那件事的全句与出口——
 * 出口与横幅上的是同一批函数（冲突三出口 `ConflictActions`、重试 `saveNow`、恢复 / 保留主版本……）。
 */
function IssuePill({ issue, text, title }: { issue: DocIssue; text: string; title: string }) {
  const { t } = useTranslation('workspace')
  const [open, setOpen] = useState(false)
  const tone = ISSUE_TONE[issue]
  const Icon = ISSUE_ICON[issue]
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      width={320}
      align="start"
      ariaLabel={text}
      trigger={
        <button
          type="button"
          data-doc-status-pill={issue}
          data-tone={tone}
          aria-label={t('topbar.issueAria', { status: text })}
          title={title}
          className={cn(
            'inline-flex h-6 min-w-0 shrink items-center gap-1 rounded-full px-2 text-xs font-medium outline-none',
            'focus-visible:focus-ring',
            PILL_TONE[tone],
          )}
        >
          <Icon size={ICON_SIZE.xs} className="shrink-0" aria-hidden />
          <span data-save-text className="truncate max-[899px]:sr-only">
            {text}
          </span>
        </button>
      }
    >
      <div data-doc-status-popover={issue} className="flex flex-col gap-2 p-1">
        <IssueBody issue={issue} close={() => setOpen(false)} />
      </div>
    </Popover>
  )
}

function IssueBody({ issue, close }: { issue: DocIssue; close: () => void }) {
  const { t } = useTranslation('workspace')
  const notice = useDocumentStore((s) => s.docNotice)
  const saveIssue = useDocumentStore((s) => s.saveIssue)
  const lastDoc = useProjectStore((s) => s.lastDocumentIssue)
  const done = (fn: () => unknown) => () => {
    close()
    void fn()
  }
  const head = (title: string, body?: ReactNode) => (
    <>
      <p className="type-title text-ink">{title}</p>
      {body && <p className="type-reading text-ink-2">{body}</p>}
    </>
  )
  const row = (children: ReactNode) => <div className="flex flex-wrap items-center gap-1.5 pt-1">{children}</div>

  if (issue === 'conflict') {
    return (
      <>
        {head(
          t(saveIssue?.kind === 'stale' ? 'docBanner.conflictStale' : 'docBanner.conflictExternal'),
          <ConflictDetail disk={saveIssue?.disk} />,
        )}
        {row(<ConflictActions onAction={close} />)}
      </>
    )
  }
  if (issue === 'save_error') {
    return (
      <>
        {head(t('topbar.saveError'), t('docBanner.saveErrorBody'))}
        {row(
          <Button variant="primary" size="sm" onClick={done(saveNow)}>
            {t('docBanner.retry')}
          </Button>,
        )}
      </>
    )
  }
  if (issue === 'recovery' && notice?.kind === 'recovery') {
    const s = notice.summary
    return (
      <>
        {head(
          t('docBanner.recoveryTitle'),
          // 文档名是用户内容，作为插值原样透出
          t('docBanner.recoveryBody', {
            name: s.name,
            canvases: s.canvases,
            objects: s.objects,
            time: formatTime(s.savedAt),
          }),
        )}
        {row(
          <>
            <Button variant="primary" size="sm" data-recovery-action="recover" onClick={done(recoverLocalCopy)}>
              {t('docBanner.recover')}
            </Button>
            <Button size="sm" data-recovery-action="keep-main" onClick={done(discardLocalCopy)}>
              {t('docBanner.keepMain')}
            </Button>
          </>,
        )}
      </>
    )
  }
  if (issue === 'last_doc' && lastDoc) {
    return (
      <>
        {/* 文档名是用户内容，作为插值原样透出 */}
        {head(t('docBanner.lastDocTitle', { name: lastDoc.name || t('docBanner.lastDocUnnamed') }), t('docBanner.lastDocBody'))}
        {row(
          <>
            <Button variant="primary" size="sm" onClick={done(() => useProjectStore.getState().openLastDocument())}>
              {t('docBanner.lastDocOpen')}
            </Button>
            <Button size="sm" onClick={done(() => useProjectStore.getState().dismissLastDocumentIssue())}>
              {t('docBanner.dismiss')}
            </Button>
          </>,
        )}
      </>
    )
  }
  if (issue === 'too_new' && notice?.kind === 'schema_too_new') {
    return (
      <>
        {head(t('docBanner.tooNewTitle', { schema: notice.schema }), t('docBanner.tooNewBody'))}
        {row(
          <Button
            size="sm"
            onClick={done(() => {
              // 「上次的排版没打开」指的若是同一份：一起收掉，否则关掉这句后又冒出一个只会失败的重试
              if (lastDoc?.id === notice.docId) useProjectStore.getState().dismissLastDocumentIssue()
              dismissDocNotice()
            })}
          >
            {t('docBanner.dismiss')}
          </Button>,
        )}
      </>
    )
  }
  return null
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
      shortcut={keyOf('timeline')}
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
      // 非模态：单击开菜单、双击改名（2026-10-07 设计审计 §10.1 P2）——模态菜单一开，第二下落不回触发器上
      modal={false}
      trigger={
        <Button
          size="md"
          className="min-w-0 max-w-52 shrink text-ink-2"
          aria-label={t('topbar.documentLabel', { name })}
          data-document-menu
          title={t('topbar.documentRenameHint')}
          onDoubleClick={() => setEditing(true)}
          onKeyDown={(e) => {
            if (e.key !== 'F2') return
            e.preventDefault()
            setEditing(true)
          }}
        >
          <span className="truncate">{name}</span>
          {/* 点的词汇（2026-10-07 设计审计 §10.1 P2）：**空心环 = 还没写进项目文件**（ADR 0096，项目里那份落后于这份排版）；
              实心 accent 点只留给「有更新」（「更多」上那颗）。画布页签上不再有「未保存」点 */}
          {projectFile?.dirty && (
            <span
              data-project-file-dirty
              aria-label={t('topbar.projectFileUnsaved', { file: projectFile.file })}
              title={t('topbar.projectFileUnsaved', { file: projectFile.file })}
              className="size-1.5 shrink-0 rounded-full ring-1 ring-ink-3"
            />
          )}
          <ChevronDown size={ICON_SIZE.xs} className="shrink-0 text-ink-3" />
        </Button>
      }
    >
      <MenuItem onSelect={() => setEditing(true)} shortcut={keyOf('tabRename')}>
        {t('topbar.renameDocument')}
      </MenuItem>
      <MenuItem onSelect={newBlankDocument}>{t('topbar.newBlankDocument')}</MenuItem>

      <MenuSeparator />
      <MenuLabel>{t('topbar.projectDocuments')}</MenuLabel>
      <MenuItem
        onSelect={() => useUiStore.getState().setLayoutOpen(true, 'save')}
        shortcut={keyOf('saveAs')}
      >
        {t('topbar.saveDocumentAs')}
      </MenuItem>
      <MenuItem onSelect={() => useUiStore.getState().setLayoutOpen(true, 'load')}>
        {t('topbar.openDocument')}
      </MenuItem>
      <MenuItem onSelect={() => useUiStore.getState().setVersionsOpen(true)} shortcut={keyOf('timeline')}>
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

/** 撤销 / 重做：挨着导出（2026-10-07 设计审计 §10.1 P2） */
function UndoRedo() {
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
        shortcut={keyOf('undo')}
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
        shortcut={keyOf('redo')}
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
    <Tip label={t('topbar.exportTip')} shortcut={keyOf('export')}>
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
                // 实心 accent 点 = 有更新（点的词汇：空心环是「还没写进项目文件」，见文档名）
                data-update-dot
                className="absolute -right-0.5 -top-0.5 size-1.5 rounded-full bg-accent"
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
              <span className="size-1.5 shrink-0 rounded-full bg-accent" aria-hidden />
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
      <MenuItem onSelect={() => usePalette.getState().setOpen(true)} shortcut={keyOf('palette')}>
        {t('topbar.commandPalette')}
      </MenuItem>
      <MenuItem onSelect={() => ui().setShortcutHelpOpen(true)} shortcut={keyOf('help')}>
        {t('topbar.shortcutHelp')}
      </MenuItem>
      {/* 开始 / 继续 / 重新开始教程：文案与动作都来自 lib/onboarding/tutorial，不在这里判状态 */}
      <MenuItem onSelect={() => void runTutorialEntry('help')} data-onboarding-anchor="help-tutorial">
        {t(`topbar.tutorial.${tutorialKind}`)}
      </MenuItem>
    </Menu>
  )
}
