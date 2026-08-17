import { useEffect, useMemo, useState } from 'react'
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
import { formatClock, MOD } from '@/lib/utils'

/** 标注形状收进一个菜单：顶栏留给「文字」和真正高频的动作 */
const MARK_TOOLS: { tool: Exclude<Tool, 'select'>; icon: typeof Type; label: string; key: string }[] = [
  { tool: 'arrow', icon: ArrowUpRight, label: '箭头', key: 'A' },
  { tool: 'rect', icon: Square, label: '矩形', key: 'R' },
  { tool: 'ellipse', icon: Circle, label: '椭圆', key: 'O' },
  { tool: 'line', icon: Slash, label: '直线', key: 'L' },
]

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
        ui.setStatus(`已打开项目包（${res.manifest.created_at ?? ''}），素材全部就位`)
      } else if (drift) {
        ui.setStatus(
          `已打开项目包；${drift} 个素材与打包时内容不一致（可能已被改动），请核对`,
          'error',
        )
      }
    } catch (e) {
      ui.setStatus(`项目包打开失败：${e instanceof Error ? e.message : e}`, 'error')
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
      title="改动会自动保存到本机磁盘；要留一个命名版本请用「保存为画布文件」"
    >
      {dirty || !lastPersisted ? '保存中…' : `已自动保存 ${formatClock(lastPersisted)}`}
    </span>
  )
}

function DocumentMenu() {
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
        aria-label="文档名"
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
        <Button size="sm" className="max-w-52 text-ink-2" aria-label={`文档：${name}`}>
          <span className="truncate">{name}</span>
          <ChevronDown size={12} className="shrink-0 text-ink-faint" />
        </Button>
      }
    >
      <MenuItem onSelect={() => setEditing(true)}>重命名文档…</MenuItem>
      <MenuItem onSelect={newBlankDocument}>新建空白文档</MenuItem>

      <MenuSeparator />
      <MenuLabel>画布文件</MenuLabel>
      <MenuItem
        onSelect={() => useUiStore.getState().setLayoutOpen(true, 'save')}
        shortcut={`${MOD}S`}
      >
        保存为画布文件…
      </MenuItem>
      <MenuItem onSelect={() => useUiStore.getState().setLayoutOpen(true, 'load')}>
        载入画布文件…
      </MenuItem>
      <MenuItem onSelect={() => useUiStore.getState().setVersionsOpen(true)}>
        布局版本时间线…
      </MenuItem>
      <MenuItem onSelect={importPackage}>导入项目包（.magplot）…</MenuItem>

      <MenuSeparator />
      <MenuLabel>最近文档（本机）</MenuLabel>
      {recent.length === 0 ? (
        <MenuItem disabled>暂无其他文档</MenuItem>
      ) : (
        recent.map((r) => (
          <MenuItem
            key={r.id}
            onSelect={() => openRecentDocument(r.id)}
            shortcut={formatClock(r.savedAt)}
          >
            {r.name} · {(r.canvases ?? 1) > 1 ? `${r.canvases} 张画布 · ` : ''}
            {r.objects} 个对象
          </MenuItem>
        ))
      )}
    </Menu>
  )
}

function ToolCluster() {
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
      <Tip label={undoLabel ? `撤销 ${undoLabel}` : '撤销'} shortcut={`${MOD}Z`}>
        <Button size="icon" disabled={!canUndo} onClick={runUndo} aria-label="撤销">
          <Undo2 size={15} />
        </Button>
      </Tip>
      <Tip label={redoLabel ? `重做 ${redoLabel}` : '重做'} shortcut={`⇧${MOD}Z`}>
        <Button size="icon" disabled={!canRedo} onClick={runRedo} aria-label="重做">
          <Redo2 size={15} />
        </Button>
      </Tip>

      <span className="mx-1.5 h-5 w-px bg-border" />

      <Tip label="文字" shortcut="T">
        <Button
          size="icon"
          active={tool === 'text'}
          onClick={() => setTool(tool === 'text' ? 'select' : 'text')}
          aria-label="文字"
        >
          <Type size={15} />
        </Button>
      </Tip>

      <Menu
        width={188}
        align="center"
        trigger={
          <Button size="sm" active={markActive} aria-label="标注">
            {ActiveMark ? <ActiveMark size={15} /> : <Shapes size={15} />}
            <span className="text-xs">标注</span>
            <ChevronDown size={12} className="text-ink-faint" />
          </Button>
        }
      >
        {MARK_TOOLS.map(({ tool: t, icon: Icon, label, key }) => (
          <MenuItem key={t} shortcut={key} onSelect={() => setTool(tool === t ? 'select' : t)}>
            <span className="flex items-center gap-2">
              <Icon size={13} className={tool === t ? 'text-accent' : 'text-ink-3'} />
              {label}
            </span>
          </MenuItem>
        ))}
        <MenuSeparator />
        <MenuLabel>插入形状</MenuLabel>
        {(
          [
            ['triangle', '三角形'],
            ['diamond', '菱形'],
            ['polygon', '多边形'],
            ['brace', '大括号'],
          ] as const
        ).map(([kind, label]) => (
          <MenuItem key={kind} onSelect={() => insertShape(kind)}>
            {label}
          </MenuItem>
        ))}
        <MenuSeparator />
        <MenuItem onSelect={() => setPresetsOpen(true)}>科研预设与符号…</MenuItem>
      </Menu>
      <PresetsDialog open={presetsOpen} onClose={() => setPresetsOpen(false)} />

      <Tip label="按阅读顺序添加 (a)(b)(c) 标签">
        <Button size="icon" onClick={addSubLabels} aria-label="添加序号标签">
          <Tags size={15} />
        </Button>
      </Tip>
    </div>
  )
}

function ZoomControls() {
  const zoom = useViewportStore((s) => s.zoom)
  const page = useDocumentStore((s) => s.doc.page)
  const [open, setOpen] = useState(false)

  return (
    <div className="flex items-center rounded-sm border border-border bg-surface">
      <Tip label="缩小" shortcut={`${MOD}−`}>
        <Button
          size="icon-sm"
          className="rounded-r-none"
          onClick={() => useViewportStore.getState().setZoomCentered(zoom / 1.25)}
          aria-label="缩小"
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
            aria-label={`缩放 ${Math.round(zoom * 100)}%`}
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
              useViewportStore.getState().fit(page.w, page.h)
              setOpen(false)
            }}
            className="flex h-7 items-center justify-between rounded-sm px-2 text-xs text-ink outline-none hover:bg-ink/[.055] focus-visible:focus-ring"
          >
            <span>适应画布</span>
            <span className="font-mono text-xs text-ink-3">{MOD}1</span>
          </button>
        </div>
      </Popover>
      <Tip label="放大" shortcut={`${MOD}+`}>
        <Button
          size="icon-sm"
          className="rounded-none"
          onClick={() => useViewportStore.getState().setZoomCentered(zoom * 1.25)}
          aria-label="放大"
        >
          <Plus size={13} />
        </Button>
      </Tip>
      <Tip label="适应画布" shortcut={`${MOD}1`}>
        <Button
          size="icon-sm"
          className="rounded-l-none border-l border-border"
          onClick={() => useViewportStore.getState().fit(page.w, page.h)}
          aria-label="适应画布"
        >
          <Maximize2 size={13} />
        </Button>
      </Tip>
    </div>
  )
}

function ExportButton() {
  return (
    <Tip label="导出 PNG / PDF" shortcut={`${MOD}E`}>
      <Button
        variant="primary"
        size="md"
        onClick={() => useUiStore.getState().setExportOpen(true)}
      >
        <Download size={14} />
        导出
      </Button>
    </Tip>
  )
}

/** 低频全局动作收进「更多」：样式 / 版本 / 画布设置 / 帮助 */
function MoreMenu() {
  const ui = () => useUiStore.getState()
  // 新版本不弹窗、不占顶栏位置：只在「更多」上点一个圆点，菜单里给一条入口。
  // 升级是可延后的事，不该打断正在排版的人。
  const update = useUpdateStore((s) => s.status)
  const hasUpdate = !!update?.update_available
  return (
    <Menu
      width={196}
      trigger={
        <Button size="icon" aria-label={hasUpdate ? '更多（有新版本）' : '更多'}>
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
              有新版本 {update?.latest}
            </span>
          </MenuItem>
          <MenuSeparator />
        </>
      )}
      <MenuItem onSelect={() => ui().setStylesOpen(true)}>论文样式…</MenuItem>
      <MenuSeparator />
      <MenuItem onSelect={() => ui().setRightTab('canvas')}>画布设置</MenuItem>
      <MenuSeparator />
      <MenuItem onSelect={() => usePalette.getState().setOpen(true)} shortcut={`${MOD}K`}>
        命令面板
      </MenuItem>
      <MenuItem onSelect={() => ui().setShortcutHelpOpen(true)} shortcut="?">
        快捷键帮助
      </MenuItem>
    </Menu>
  )
}
