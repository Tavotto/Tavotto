import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowUpRight,
  ChevronDown,
  Circle,
  Download,
  Maximize2,
  Minus,
  MoreHorizontal,
  Plus,
  Redo2,
  Slash,
  Square,
  Shapes,
  Tags,
  Type,
  Undo2,
} from 'lucide-react'
import {
  addSubLabels,
  newBlankDocument,
  openLayoutDocument,
  openRecentDocument,
  setDocumentName,
} from '@/store/actions'
import { requestRelinkMissing } from '@/lib/clipboard'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { openPackage } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { insertShape } from '@/lib/presets'
import { PresetsDialog } from './PresetsDialog'
import { ProjectSwitcher } from './ProjectSwitcher'
import { WriteBackTopBarButton } from './inspector/UpdateSourceButton'
import { usePalette } from '@/components/CommandPalette'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore, type Tool } from '@/store/uiStore'
import { useUpdateStore } from '@/store/updateStore'
import { useViewportStore } from '@/store/viewportStore'
import { BrandMark } from './ui/BrandMark'
import { Button } from './ui/Button'
import { Menu, MenuItem, MenuLabel, MenuSeparator } from './ui/Menu'
import { Popover } from './ui/Popover'
import { Tip } from './ui/Tooltip'
import { MOD } from '@/lib/utils'
import { msg } from '@/i18n'
import { formatTime } from '@/i18n/format'
import { useFormatMessage } from '@/i18n/react'

/**
 * 标注形状收进一个菜单：顶栏留给「文字」和真正高频的动作。
 * 名字走 common:objectType / common:shape，这里只留图标与快捷键。
 */
const MARK_TOOLS: { tool: Exclude<Tool, 'select'>; icon: typeof Type; key: string }[] = [
  { tool: 'arrow', icon: ArrowUpRight, key: 'A' },
  { tool: 'rect', icon: Square, key: 'R' },
  { tool: 'ellipse', icon: Circle, key: 'O' },
  { tool: 'line', icon: Slash, key: 'L' },
]

/** 画布工具的显示名：箭头是对象类型，其余是形状 */
const markToolKey = (tool: Exclude<Tool, 'select'>) =>
  tool === 'arrow' || tool === 'text' ? `common:objectType.${tool}` : `common:shape.${tool}`

/** 顶栏里插入的形状（非工具，点一下直接落一个） */
const INSERT_SHAPES = ['triangle', 'diamond', 'polygon', 'brace'] as const

const ZOOM_PRESETS = [0.5, 0.75, 1, 1.5, 2, 4]

export function TopBar() {
  return (
    <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border bg-surface px-3">
      <div className="flex min-w-0 flex-1 items-center gap-1.5">
        <Brand />
        {/* 项目（图库目录）→ 文档（画布）：从大到小，与对象层级一致 */}
        <ProjectSwitcher />
        <span aria-hidden className="h-3.5 w-px shrink-0 bg-border" />
        <DocumentMenu />
        <AutosaveState />
      </div>

      <ToolCluster />

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
 * 导入可复现项目包：选 zip → 后端检视（不写入图库）→ 作为新文档打开；
 * 有缺失素材时接到统一的重新链接对话框，绝不静默出空面板。
 */
function importPackage() {
  const input = document.createElement('input')
  input.type = 'file'
  // 新格式 .magplot（zip 容器）；旧 .mmpack.zip 照常可选可开
  input.accept = '.magplot,.zip,application/zip'
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

/** 图形标 + 实时文字：20px compact 是规范的显式例外（阈值本该给 mini） */
function Brand() {
  return (
    <span className="flex shrink-0 items-center gap-[7px] text-sm font-semibold tracking-tight text-ink">
      <BrandMark size={20} variant="compact" />
      {PRODUCT_NAME}
    </span>
  )
}

/** 自动保存是状态而不是动作：贴着文档名，报告本机存到了哪一步 */
function AutosaveState() {
  const { t } = useTranslation('workspace')
  const dirty = useDocumentStore((s) => s.dirty)
  const lastPersisted = useDocumentStore((s) => s.lastPersisted)
  const hasContent = useDocumentStore(
    (s) => s.doc.objects.length > 0 || s.doc.guides.length > 0 || s.canvases.length > 1,
  )
  if (!hasContent) return null

  return (
    <span
      aria-live="polite"
      className="hidden shrink-0 text-xs text-ink-3 min-[900px]:inline"
      title={t('topbar.autosaveTitle')}
    >
      {dirty || !lastPersisted
        ? t('topbar.saving')
        : t('topbar.savedAt', { time: formatTime(lastPersisted) })}
    </span>
  )
}

function DocumentMenu() {
  const { t } = useTranslation('workspace')
  const name = useDocumentStore((s) => s.projectMeta.name)
  const documentId = useDocumentStore((s) => s.documentId)
  const recentDocs = useDocumentStore((s) => s.recentDocs)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(name)

  useEffect(() => setDraft(name), [name])

  const recent = useMemo(
    () => recentDocs.filter((r) => r.id !== documentId),
    [recentDocs, documentId],
  )

  if (editing) {
    return (
      <input
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
        className="h-7 w-40 rounded-sm border border-accent bg-surface px-1.5 text-xs text-ink outline-none"
      />
    )
  }

  return (
    <Menu
      trigger={
        <Button size="sm" className="max-w-52 text-ink-2" aria-label={t('topbar.documentLabel', { name })}>
          <span className="truncate">{name}</span>
          <ChevronDown size={12} className="shrink-0 text-ink-faint" />
        </Button>
      }
    >
      <MenuItem onSelect={() => setEditing(true)}>{t('topbar.renameDocument')}</MenuItem>
      <MenuItem onSelect={newBlankDocument}>{t('topbar.newBlankDocument')}</MenuItem>

      <MenuSeparator />
      <MenuLabel>{t('topbar.canvasFiles')}</MenuLabel>
      <MenuItem
        onSelect={() => useUiStore.getState().setLayoutOpen(true, 'save')}
        shortcut={`${MOD}S`}
      >
        {t('topbar.saveAsCanvasFile')}
      </MenuItem>
      <MenuItem onSelect={() => useUiStore.getState().setLayoutOpen(true, 'load')}>
        {t('topbar.loadCanvasFile')}
      </MenuItem>
      <MenuItem onSelect={() => useUiStore.getState().setVersionsOpen(true)}>
        {t('topbar.versionTimeline')}
      </MenuItem>
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
          </MenuItem>
        ))
      )}
    </Menu>
  )
}

function ToolCluster() {
  const { t } = useTranslation(['workspace', 'common'])
  const fmt = useFormatMessage()
  const tool = useUiStore((s) => s.tool)
  const setTool = useUiStore((s) => s.setTool)
  const [presetsOpen, setPresetsOpen] = useState(false)
  const canUndo = useDocumentStore((s) => s.past.length > 0)
  const canRedo = useDocumentStore((s) => s.future.length > 0)
  const undoLabel = useDocumentStore((s) => s.past.at(-1)?.label)
  const redoLabel = useDocumentStore((s) => s.future[0]?.label)
  const activeMark = MARK_TOOLS.find((m) => m.tool === tool)
  const markActive = !!activeMark
  const ActiveMark = activeMark?.icon

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
          <Undo2 size={15} />
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
          <Redo2 size={15} />
        </Button>
      </Tip>

      <span className="mx-1.5 h-5 w-px bg-border" />

      <Tip label={t('common:objectType.text')} shortcut="T">
        <Button
          size="icon"
          active={tool === 'text'}
          onClick={() => setTool(tool === 'text' ? 'select' : 'text')}
          aria-label={t('common:objectType.text')}
        >
          <Type size={15} />
        </Button>
      </Tip>

      <Menu
        width={188}
        align="center"
        trigger={
          <Button size="sm" active={markActive} aria-label={t('workspace:topbar.annotate')}>
            {ActiveMark ? <ActiveMark size={15} /> : <Shapes size={15} />}
            <span className="text-xs">{t('workspace:topbar.annotate')}</span>
            <ChevronDown size={12} className="text-ink-faint" />
          </Button>
        }
      >
        {MARK_TOOLS.map(({ tool: mark, icon: Icon, key }) => (
          <MenuItem
            key={mark}
            shortcut={key}
            onSelect={() => setTool(tool === mark ? 'select' : mark)}
          >
            <span className="flex items-center gap-2">
              <Icon size={13} className={tool === mark ? 'text-accent' : 'text-ink-3'} />
              {t(markToolKey(mark))}
            </span>
          </MenuItem>
        ))}
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
          <Tags size={15} />
        </Button>
      </Tip>
    </div>
  )
}

function ZoomControls() {
  const { t } = useTranslation('workspace')
  const zoom = useViewportStore((s) => s.zoom)
  const page = useDocumentStore((s) => s.doc.page)
  const [open, setOpen] = useState(false)

  return (
    <div className="flex items-center rounded-sm border border-border bg-surface">
      <Tip label={t('topbar.zoomOut')} shortcut={`${MOD}−`}>
        <Button
          size="icon-sm"
          className="rounded-r-none"
          onClick={() => useViewportStore.getState().zoomBy(1 / 1.25)}
          aria-label={t('topbar.zoomOut')}
        >
          <Minus size={13} />
        </Button>
      </Tip>
      <Popover
        open={open}
        onOpenChange={setOpen}
        width={128}
        align="center"
        trigger={
          <button
            aria-label={t('topbar.zoomValue', { percent: Math.round(zoom * 100) })}
            className="h-7 w-14 border-x border-border font-mono text-xs tabular-nums text-ink outline-none hover:bg-ink/[.04] focus-visible:focus-ring"
          >
            {Math.round(zoom * 100)}%
          </button>
        }
      >
        <div className="flex flex-col">
          {ZOOM_PRESETS.map((z) => (
            <button
              key={z}
              onClick={() => {
                useViewportStore.getState().setZoomCentered(z)
                setOpen(false)
              }}
              className="flex h-7 items-center justify-between rounded-sm px-2 text-xs text-ink outline-none hover:bg-ink/[.055] focus-visible:focus-ring"
            >
              <span>{z * 100}%</span>
              {z === 1 && <span className="font-mono text-xs text-ink-3">{MOD}0</span>}
            </button>
          ))}
          <div className="my-1 h-px bg-border" />
          <button
            onClick={() => {
              useViewportStore.getState().fitAnimated(page.w, page.h)
              setOpen(false)
            }}
            className="flex h-7 items-center justify-between rounded-sm px-2 text-xs text-ink outline-none hover:bg-ink/[.055] focus-visible:focus-ring"
          >
            <span>{t('topbar.fitCanvas')}</span>
            <span className="font-mono text-xs text-ink-3">{MOD}1</span>
          </button>
        </div>
      </Popover>
      <Tip label={t('topbar.zoomIn')} shortcut={`${MOD}+`}>
        <Button
          size="icon-sm"
          className="rounded-none"
          onClick={() => useViewportStore.getState().zoomBy(1.25)}
          aria-label={t('topbar.zoomIn')}
        >
          <Plus size={13} />
        </Button>
      </Tip>
      <Tip label={t('topbar.fitCanvas')} shortcut={`${MOD}1`}>
        <Button
          size="icon-sm"
          className="rounded-l-none border-l border-border"
          onClick={() => useViewportStore.getState().fitAnimated(page.w, page.h)}
          aria-label={t('topbar.fitCanvas')}
        >
          <Maximize2 size={13} />
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
        onClick={() => useUiStore.getState().setExportOpen(true)}
      >
        <Download size={14} />
        {t('topbar.export')}
      </Button>
    </Tip>
  )
}

/** 低频全局动作收进「更多」：样式 / 版本 / 画布设置 / 帮助 */
function MoreMenu() {
  const { t } = useTranslation('workspace')
  const ui = () => useUiStore.getState()
  // 新版本不弹窗、不占顶栏位置：只在「更多」上点一个圆点，菜单里给一条入口。
  // 升级是可延后的事，不该打断正在排版的人。
  const update = useUpdateStore((s) => s.status)
  const desktopUpdate = useUpdateStore((s) => s.desktopUpdate)
  const hasUpdate = !!update?.update_available || !!desktopUpdate
  const latest = desktopUpdate?.version ?? update?.latest
  return (
    <Menu
      width={196}
      trigger={
        <Button size="icon" aria-label={t(hasUpdate ? 'topbar.moreWithUpdate' : 'topbar.more')}>
          <span className="relative">
            <MoreHorizontal size={15} className="text-ink-2" />
            {hasUpdate && (
              <span
                aria-hidden
                className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-accent"
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
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden />
              {t('topbar.updateAvailable', { version: latest })}
            </span>
          </MenuItem>
          <MenuSeparator />
        </>
      )}
      <MenuItem onSelect={() => ui().setStylesOpen(true)}>{t('topbar.paperStyles')}</MenuItem>
      <MenuSeparator />
      <MenuItem onSelect={() => ui().setRightTab('canvas')}>{t('topbar.canvasSettings')}</MenuItem>
      <MenuSeparator />
      <MenuItem onSelect={() => usePalette.getState().setOpen(true)} shortcut={`${MOD}K`}>
        {t('topbar.commandPalette')}
      </MenuItem>
      <MenuItem onSelect={() => ui().setShortcutHelpOpen(true)} shortcut="?">
        {t('topbar.shortcutHelp')}
      </MenuItem>
    </Menu>
  )
}
