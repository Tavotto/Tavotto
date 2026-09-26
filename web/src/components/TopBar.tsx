import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowUpRight,
  ChevronDown,
  Circle,
  Download,
  Maximize2,
  Ellipsis,
  Redo2,
  Slash,
  Square,
  Shapes,
  Tags,
  Type,
  Undo2,
  RotateCcwClock,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import {
  addSubLabels,
  newBlankDocument,
  openLayoutDocument,
  openRecentDocument,
  setDocumentName,
} from '@/store/actions'
import { requestRelinkMissing } from '@/lib/clipboard'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { createPackage, openPackage } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { foreignProjectLabel } from '@/lib/projectLabel'
import { currentProjectId } from '@/lib/session'
import { bindingForProject } from '@/lib/projectFile'
import { useProjectStore } from '@/store/projectStore'
import { insertShape } from '@/lib/presets'
import { PresetsDialog } from './PresetsDialog'
import { ProjectSwitcher } from './ProjectSwitcher'
import { WriteBackTopBarButton } from './inspector/UpdateSourceButton'
import { usePalette } from '@/components/CommandPalette'
import { runTutorialEntry, tutorialEntry } from '@/lib/onboarding/tutorial'
import { refreshProjectNow } from '@/store/liveSync'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { discardLocalCopy, recoverLocalCopy, useDocumentStore } from '@/store/documentStore'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { useUpdateStore } from '@/store/updateStore'
import { useViewportStore } from '@/store/viewportStore'
import { Numbers } from '@sfinterface/numbers'
import { BrandMark } from './ui/BrandMark'
import { Button } from './ui/Button'
import { Menu, MenuItem, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuSeparator } from './ui/Menu'
import { TextInput } from './ui/Input'
import { Tip } from './ui/Tooltip'
import { MOD, cn } from '@/lib/utils'
import { msg } from '@/i18n'
import { formatTime } from '@/i18n/format'
import { useFormatMessage } from '@/i18n/react'

/**
 * 标注形状收进一个菜单：顶栏留给「文字」和真正高频的动作。
 * 名字走 common:objectType / common:shape，这里只留图标与快捷键。
 * 「文字」有自己的按钮，不在这张表里。
 */
type MarkTool = 'arrow' | 'rect' | 'ellipse' | 'line'

const MARK_TOOLS: { tool: MarkTool; icon: typeof Type; key: string }[] = [
  { tool: 'arrow', icon: ArrowUpRight, key: 'A' },
  { tool: 'rect', icon: Square, key: 'R' },
  { tool: 'ellipse', icon: Circle, key: 'O' },
  { tool: 'line', icon: Slash, key: 'L' },
]

/**
 * 画布工具的显示名：箭头是对象类型，其余是形状。
 *
 * 两个分支各自收窄成自己的字面量联合——模板 key 的静态展开按参数类型走，
 * 混在一个 `Exclude<Tool,'select'>` 里会让提取器要求 `shape.arrow`、
 * `objectType.rect` 这类不存在的条目。
 */
const markToolKey = (tool: MarkTool): string =>
  tool === 'arrow' ? objectTypeKey(tool) : shapeKey(tool)

const objectTypeKey = (tool: 'arrow') => `common:objectType.${tool}`
const shapeKey = (tool: 'rect' | 'ellipse' | 'line') => `common:shape.${tool}`

/** 顶栏里插入的形状（非工具，点一下直接落一个） */
const INSERT_SHAPES = ['triangle', 'diamond', 'polygon', 'brace'] as const

const ZOOM_PRESETS = [0.5, 0.75, 1, 1.5, 2, 4]

export function TopBar() {
  const fastEdit = useWorkspaceStore((s) => s.mode === 'fast_edit')
  return (
    <header className="flex h-11 shrink-0 items-center justify-between gap-3 bg-surface px-3">
      <div className="flex min-w-0 flex-1 items-center gap-1.5">
        <Brand />
        {/* 项目（图库目录）→ 文档（画布）：从大到小，与对象层级一致 */}
        <ProjectSwitcher />
        {/* 项目 / 文档是一条面包屑（2026-09-15 打磨批次 F）：一个斜杠，不是竖线加两个 chip */}
        <span aria-hidden className="type-meta shrink-0 select-none">
          /
        </span>
        <DocumentMenu />
        <SaveStateLabel />
        <RecoveryNotice />
      </div>

      <ToolCluster layoutTools={!fastEdit} />

      <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
        <ZoomControls />
        {/* 写回原始文件是高频动作，常驻导出左侧；导出仍是顶栏唯一填色主动作 */}
        <WriteBackTopBarButton />
        <ExportButton />
        <MoreMenu />
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
    ui.setStatus(msg('status.packaged', { name: res.name, count: res.assets }, 'workspace'))
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
      {PRODUCT_NAME}
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
 * 顶栏的保存状态。两根轴（ADR 0096）：`saveState` 说本机自动保存走到哪一步；
 * 绑定的项目文件说「项目里那份是不是最新」。落定之后的那句话说**去向**——
 * 「已保存到项目」「已存在本机」，tooltip 给出项目里的相对路径。
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
  if (!hasContent && !bound) return null

  const settled = saveState === 'clean' || saveState === 'saved'
  const text =
    saveState === 'saving'
      ? t('topbar.saveSaving')
      : saveState === 'dirty'
        ? t('topbar.saveDirty')
        : saveState === 'save_error'
          ? t('topbar.saveError')
          : saveState === 'conflict'
            ? t('topbar.saveConflict')
            : bound
              ? t(bound.dirty ? 'topbar.saveProjectPending' : 'topbar.saveProjectSaved')
              : saveState === 'saved'
                ? t('topbar.saveLocal')
                : lastPersisted
                  ? t('topbar.saveLocalAt', { time: formatTime(lastPersisted) })
                  : t('topbar.saveLocal')
  const bad = saveState === 'save_error' || saveState === 'conflict'
  const title = bound
    ? t(bound.dirty || !settled ? 'topbar.saveTitleProjectPending' : 'topbar.saveTitleProject', {
        file: bound.file,
        mod: MOD,
      })
    : t(projectOpen ? 'topbar.saveTitleLocalInProject' : 'topbar.saveTitleLocal', { mod: MOD })

  return (
    <span
      aria-live="polite"
      data-save-destination={bound ? 'project' : 'local'}
      className={cn(
        'hidden shrink-0 text-xs min-[900px]:inline',
        bad ? 'text-danger' : 'text-ink-3',
      )}
      title={title}
    >
      {text}
    </span>
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
        <Button size="md" className="max-w-52 text-ink-2" aria-label={t('topbar.documentLabel', { name })}>
          <span className="truncate">{name}</span>
          {/* 项目里的文件落后于这份排版（ADR 0096）：与画布页签的「未保存」同一颗圆点 */}
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
      <MenuItem onSelect={() => useUiStore.getState().setVersionsOpen(true)}>
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

function ToolCluster({ layoutTools }: { layoutTools: boolean }) {
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

      {layoutTools && <MarkTools />}
    </div>
  )
}

/**
 * 画布标注工具（文字 / 形状 / 子图标签）。**只在画布排版模式出现**：
 * 它们画的是画布对象，而快速编辑那一屏只有一张图，画下去看不见。
 */
function MarkTools() {
  const { t } = useTranslation(['workspace', 'common'])
  const tool = useUiStore((s) => s.tool)
  const setTool = useUiStore((s) => s.setTool)
  const [presetsOpen, setPresetsOpen] = useState(false)
  const activeMark = MARK_TOOLS.find((m) => m.tool === tool)
  const markActive = !!activeMark
  const ActiveMark = activeMark?.icon

  return (
    <>
      <span className="mx-1.5 h-5 w-px bg-border" />

      <Tip label={t('common:objectType.text')} shortcut="T">
        <Button
          size="icon"
          active={tool === 'text'}
          onClick={() => setTool(tool === 'text' ? 'select' : 'text')}
          aria-label={t('common:objectType.text')}
        >
          <Type size={ICON_SIZE.md} />
        </Button>
      </Tip>

      <Menu
        width={188}
        align="center"
        trigger={
          <Button size="md" active={markActive} aria-label={t('workspace:topbar.annotate')}>
            {ActiveMark ? <ActiveMark size={ICON_SIZE.md} filled /> : <Shapes size={ICON_SIZE.md} />}
            {t('workspace:topbar.annotate')}
            <ChevronDown size={ICON_SIZE.xs} className="text-ink-3" />
          </Button>
        }
      >
        {/* 四把工具是一组互斥取值：当前那把带勾（MenuRadioGroup），不再靠图标换个颜色
            说「选中的是我」——同一张菜单里图标的深浅还要兼职表示别的（2026-09-15 打磨 T6）。
            图标走 `MenuItem.icon` 这个唯一出处，不手写 span + 自定色 */}
        <MenuRadioGroup
          value={activeMark?.tool ?? ''}
          onValueChange={(v) => setTool(tool === v ? 'select' : (v as MarkTool))}
        >
          {MARK_TOOLS.map(({ tool: mark, icon: Icon, key }) => (
            <MenuRadioItem key={mark} value={mark} icon={Icon} shortcut={key}>
              {t(markToolKey(mark))}
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
        <MenuSeparator />
        <MenuLabel>{t('workspace:topbar.insertShape')}</MenuLabel>
        {INSERT_SHAPES.map((kind) => (
          <MenuItem key={kind} onSelect={() => insertShape(kind)}>
            {t(`common:shape.${kind}`)}
          </MenuItem>
        ))}
        <MenuSeparator />
        <MenuItem onSelect={() => setPresetsOpen(true)}>{t('workspace:topbar.presets')}</MenuItem>
      </Menu>
      <PresetsDialog open={presetsOpen} onClose={() => setPresetsOpen(false)} />

      <Tip label={t('workspace:topbar.subLabelsTip')}>
        <Button size="icon" onClick={addSubLabels} aria-label={t('workspace:topbar.addSubLabels')}>
          <Tags size={ICON_SIZE.md} />
        </Button>
      </Tip>
    </>
  )
}

function ZoomControls() {
  const { t, i18n } = useTranslation('workspace')
  const zoom = useViewportStore((s) => s.zoom)
  // 读数显示的是「用户要去的那一档」（补间的终点），不是补间中的每一帧；见 viewportStore
  const readoutZoom = useViewportStore((s) => s.readoutZoom)
  const readoutRolls = useViewportStore((s) => s.readoutRolls)
  const page = useDocumentStore((s) => s.doc.page)
  // 预设那一组是**互斥取值**：当前档带勾。缩放不是整数档时一个都不勾（「不知道是哪一档」
  // 有自己的取值，不能就近归到相邻那一档）
  const preset = ZOOM_PRESETS.find((z) => Math.abs(z - zoom) < 1e-6)

  return (
    /* 缩放是一颗文本钮「114% ⌄」+ 一颗适应画布图标钮（2026-09-15 打磨批次 F，L3）：
       此前是四格边框组，与旁边的边框钮、黑钮三种壳相邻。放大 / 缩小进了菜单，快捷键照旧。
       弹层本身从 Popover + 九行手写 button 换成 `Menu`（2026-09-15 打磨 M1）：全产品的菜单
       只有一份实现——role=menu、方向键 / 首字母跳转、内边距 4 都跟着来，不再是第二种菜单 */
    <div className="flex items-center gap-0.5">
      <Menu
        width={168}
        align="end"
        trigger={
          <Button size="md" aria-label={t('topbar.zoomValue', { percent: Math.round(zoom * 100) })} className="type-number">
            {/* 会滚的数字（@sfinterface/numbers，2026-09-15 调研后只上这一处）：一步到位的缩放
                （± / 预设 / 适应）只有变了的位滚过去，说的是「变了多少、往哪变」；滚轮 / 捏合是
                连续输入，读数即时换（duration 0），柱子不会永远在半路。静止时与普通文字像素一致。
                时长接 --duration-slow（index.css 的 --sfi-resolve），分组关掉——Tavotto 的读数不分组。
                可达名在按钮的 aria-label 上，组件自己那份读屏文本由 label 保持同一句 */}
            <Numbers
              value={Math.round(readoutZoom * 100)}
              suffix="%"
              duration={readoutRolls ? undefined : 0}
              format={{ useGrouping: false }}
              locale={i18n.language}
              label={t('topbar.zoomValue', { percent: Math.round(readoutZoom * 100) })}
              data-zoom-readout
            />
            <ChevronDown size={ICON_SIZE.xs} className="text-ink-3" />
          </Button>
        }
      >
        <MenuItem shortcut={`${MOD}+`} onSelect={() => useViewportStore.getState().zoomBy(1.25)}>
          {t('topbar.zoomIn')}
        </MenuItem>
        <MenuItem shortcut={`${MOD}−`} onSelect={() => useViewportStore.getState().zoomBy(1 / 1.25)}>
          {t('topbar.zoomOut')}
        </MenuItem>
        <MenuSeparator />
        <MenuRadioGroup
          value={preset != null ? String(preset) : undefined}
          onValueChange={(v) => useViewportStore.getState().setZoomCentered(Number(v))}
        >
          {ZOOM_PRESETS.map((z) => (
            <MenuRadioItem key={z} value={String(z)} shortcut={z === 1 ? `${MOD}0` : undefined}>
              {`${z * 100}%`}
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
        <MenuSeparator />
        <MenuItem
          shortcut={`${MOD}1`}
          onSelect={() => useViewportStore.getState().fitAnimated(page.w, page.h)}
        >
          {t('topbar.fitCanvas')}
        </MenuItem>
      </Menu>
      <Tip label={t('topbar.fitCanvas')} shortcut={`${MOD}1`}>
        <Button
          size="icon"
          onClick={() => useViewportStore.getState().fitAnimated(page.w, page.h)}
          aria-label={t('topbar.fitCanvas')}
          // e2e 的稳定锚点（选择器不认 aria-label / 文案，web/AGENTS.md）
          data-fit-canvas
        >
          <Maximize2 size={ICON_SIZE.md} />
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
        {t('topbar.export')}
      </Button>
    </Tip>
  )
}

/** 低频全局动作收进「更多」：样式 / 版本 / 画布设置 / 帮助 */
function MoreMenu() {
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
        <Button size="icon" aria-label={t(hasUpdate ? 'topbar.moreWithUpdate' : 'topbar.more')}>
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
      {hasUpdate && (
        <>
          <MenuItem onSelect={() => ui().setSettingsOpen(true, 'update')}>
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
