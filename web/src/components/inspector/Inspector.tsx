import {
  Copy,
  Eye,
  EyeOff,
  Image as ImageIcon,
  Lock,
  LockOpen,
  MoreHorizontal,
  MousePointerClick,
  MoveUpRight,
  Pin,
  Square,
  Trash2,
  Type as TypeIcon,
  X,
} from 'lucide-react'
import { cn, MOD } from '@/lib/utils'
import { deleteSelected, duplicateSelected, hideElement, updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelManifest } from '@/store/renderStore'
import { RIGHT_MAX, RIGHT_MIN, useUiStore, type RightTab } from '@/store/uiStore'
import {
  objectLabel,
  type ArrowObject,
  type CanvasObject,
  type PanelObject,
  type ShapeObject,
  type TextObject,
} from '@/types/document'
import { useAiStore } from '@/store/aiStore'
import { ASSISTANT_TAB_LABEL, AssistantPanel } from '../ai/AiPanel'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Menu, MenuItem, MenuSeparator } from '../ui/Menu'
import { Tip } from '../ui/Tooltip'
import { ArrangeSection } from './ArrangeSection'
import { CanvasPage } from './CanvasPage'
import { ElementInspector } from './ElementInspector'
import { PanelSection } from './PanelSection'
import { ArrowSection, ShapeSection } from './StrokeSection'
import { TextSection } from './TextSection'
import { TransformSection } from './TransformSection'
import { useSelectedObjects } from './common'

const TABS: { id: RightTab; label: string }[] = [
  { id: 'properties', label: '属性' },
  { id: 'assistant', label: ASSISTANT_TAB_LABEL },
  { id: 'canvas', label: '画布' },
]

const TYPE_ICON = {
  panel: ImageIcon,
  text: TypeIcon,
  arrow: MoveUpRight,
  shape: Square,
} as const

export function Inspector({ overlay = false }: { overlay?: boolean }) {
  const tab = useUiStore((s) => s.rightTab)
  const setTab = useUiStore((s) => s.setRightTab)
  const width = useUiStore((s) => s.rightWidth)
  const pinned = useUiStore((s) => s.rightPinned)
  const layout = useUiStore((s) => s.layout)
  const runningAi = useAiStore((s) => s.sessions.some((x) => x.status === 'running'))

  return (
    <aside
      style={{ width }}
      aria-label="右侧面板"
      className={cn(
        'relative flex shrink-0 flex-col border-l border-border bg-surface',
        overlay && 'absolute inset-y-0 right-0 z-30 shadow-pop',
      )}
    >
      <div className="flex h-9 shrink-0 items-center gap-3 px-3">
        <div role="tablist" aria-label="右侧面板模式" className="flex h-full items-center gap-3">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'relative h-full text-xs outline-none transition-colors focus-visible:focus-ring',
                tab === t.id
                  ? 'font-medium text-ink after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:rounded-full after:bg-ink'
                  : 'text-ink-3 hover:text-ink-2',
              )}
            >
              {t.label}
              {t.id === 'assistant' && runningAi && (
                <span
                  className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-accent"
                  aria-label="有任务在运行"
                />
              )}
            </button>
          ))}
        </div>
        <span className="flex-1" />
        {layout !== 'narrow' ? (
          <Tip
            label={
              pinned
                ? '常驻中：清空选择也保持展开。点击改为自动收起'
                : '自动收起中：清空选择后右栏让位给画布。点击改为常驻'
            }
            side="bottom"
          >
            <Button
              size="sm"
              active={pinned}
              aria-pressed={pinned}
              aria-label={pinned ? '右栏常驻中，点击改为自动收起' : '右栏自动收起中，点击改为常驻'}
              onClick={() => useUiStore.getState().setRightPinned(!pinned)}
            >
              <Pin size={11} className={pinned ? undefined : 'text-ink-3'} />
              <span className="text-xs">{pinned ? '常驻' : '自动收起'}</span>
            </Button>
          </Tip>
        ) : (
          <Tip label="窗口过窄：右栏以覆盖层临时显示，加宽窗口后可常驻" side="bottom">
            <span className="text-xs text-ink-3">覆盖层</span>
          </Tip>
        )}
        <Tip label="关闭" side="bottom">
          <Button
            size="icon-sm"
            className="-mr-1.5"
            aria-label="关闭右侧面板"
            onClick={() => useUiStore.getState().toggleRight()}
          >
            <X size={13} className="text-ink-3" />
          </Button>
        </Tip>
      </div>

      {tab === 'assistant' ? (
        <AssistantPanel />
      ) : tab === 'canvas' ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <CanvasPage />
        </div>
      ) : (
        <PropertiesPage />
      )}
      <WidthHandle />
    </aside>
  )
}

function WidthHandle() {
  const start = (e: React.PointerEvent) => {
    e.preventDefault()
    const from = useUiStore.getState().rightWidth
    const x0 = e.clientX
    const move = (ev: PointerEvent) => useUiStore.getState().setRightWidth(from - (ev.clientX - x0))
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={`调整属性栏宽度（${RIGHT_MIN}–${RIGHT_MAX}px）`}
      tabIndex={0}
      onPointerDown={start}
      onKeyDown={(e) => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
        e.preventDefault()
        const ui = useUiStore.getState()
        ui.setRightWidth(ui.rightWidth + (e.key === 'ArrowLeft' ? 16 : -16))
      }}
      className="absolute inset-y-0 -left-1 z-20 w-2 cursor-col-resize outline-none hover:bg-accent/20 focus-visible:bg-accent/30"
    />
  )
}

/* -------------------------------------------------------------------------- */
/*  属性页                                                                     */
/* -------------------------------------------------------------------------- */

function PropertiesPage() {
  const objs = useSelectedObjects()
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const elementPanel = useDocumentStore((s) =>
    s.doc.objects.find((o) => o.id === elementPanelId && o.type === 'panel'),
  ) as PanelObject | undefined

  const panels = objs.filter((o): o is PanelObject => o.type === 'panel')
  const texts = objs.filter((o): o is TextObject => o.type === 'text')
  const arrows = objs.filter((o): o is ArrowObject => o.type === 'arrow')
  const shapes = objs.filter((o): o is ShapeObject => o.type === 'shape')
  const onlyType = (n: number) => n > 0 && objs.length === n
  // 面板选区的位置与尺寸由 PanelSection 自己出（含宽高比锁），
  // 这里再来一份 TransformSection 就重复了
  const panelsOnly = onlyType(panels.length)

  if (elementPanel) {
    return (
      <>
        <IdentityHeader panel={elementPanel} />
        <div className="min-h-0 flex-1 overflow-y-auto">
          <ElementInspector panel={elementPanel} />
        </div>
      </>
    )
  }

  if (objs.length === 0) {
    // 钉住时面板留着；未钉住时选择清空后面板本来就收起了
    return (
      <div className="flex min-h-0 flex-1 overflow-y-auto">
        <EmptyState
          icon={MousePointerClick}
          title="没有选中对象"
          hint="点画布上的面板、文字或标注开始编辑。"
        />
      </div>
    )
  }

  return (
    <>
      <IdentityHeader objs={objs} />
      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {/* 第一层：位置与尺寸等高频属性 */}
        {!panelsOnly && <TransformSection objs={objs} />}
        {/* 第二层：类型专属 */}
        {onlyType(panels.length) && <PanelSection objs={panels} />}
        {onlyType(texts.length) && <TextSection objs={texts} />}
        {onlyType(arrows.length) && <ArrowSection objs={arrows} />}
        {onlyType(shapes.length) && <ShapeSection objs={shapes} />}
        {/* 第三层：排列与层级（紧凑工具带；单选面板的对齐已在位置组里） */}
        <ArrangeSection
          count={objs.length}
          multi={objs.length > 1}
          zOnly={panelsOnly && objs.length === 1}
        />
      </div>
    </>
  )
}

/**
 * 唯一的上下文头：现在改的是谁、它处于什么状态、对它还能做什么。
 * 复制 / 显隐 / 锁定 / 删除收进右侧更多菜单，锁定与隐藏状态本身常驻显示。
 */
function IdentityHeader({ objs = [], panel }: { objs?: CanvasObject[]; panel?: PanelObject }) {
  const selectedGids = useUiStore((s) => s.selectedGids)
  const manifest = usePanelManifest(panel)

  if (panel) {
    const gid = selectedGids.at(-1)
    const el = gid ? manifest?.elements.find((e) => e.gid === gid) : undefined
    // gid 形如 axes_1.images_0：中段就是宿主子图，拼出「面板 / 子图 / 元素」
    const axesGid = gid?.includes('.') ? gid.split('.')[0] : undefined
    const axes = axesGid ? manifest?.elements.find((e) => e.gid === axesGid) : undefined
    const crumbs = [
      panel.name ?? panel.fileId,
      axes && axes.gid !== gid ? axes.label : null,
      el ? el.label : selectedGids.length > 1 ? `已选 ${selectedGids.length} 个元素` : null,
    ].filter(Boolean) as string[]
    const hideable =
      el && el.gid !== 'figure' && el.editable.some((f) => f.prop === 'visible')

    return (
      <header className="shrink-0 px-3 pb-2">
        <div className="flex items-center gap-1.5">
          <ImageIcon size={13} className="shrink-0 text-ink-3" />
          <h2 className="min-w-0 truncate text-xs font-medium text-ink">
            {crumbs.at(-1) ?? '图内元素'}
          </h2>
          <span className="ml-auto flex shrink-0 items-center">
            {hideable && el && (
              <Tip label="隐藏该元素（可随时恢复）" side="bottom">
                <Button
                  size="icon-sm"
                  onClick={() => {
                    hideElement(panel.id, el.gid, el.label)
                    useUiStore.getState().setSelectedGid(null)
                  }}
                  aria-label="隐藏该元素"
                >
                  <EyeOff size={12} className="text-ink-3" />
                </Button>
              </Tip>
            )}
            <Tip label="退出图内编辑" side="bottom">
              <Button
                size="icon-sm"
                className="-mr-1"
                onClick={() => useUiStore.getState().setElementPanel(null)}
                aria-label="退出图内编辑"
              >
                <X size={12} className="text-ink-3" />
              </Button>
            </Tip>
          </span>
        </div>
        {crumbs.length > 1 && (
          <p className="mt-0.5 truncate text-xs text-ink-3" title={crumbs.join(' / ')}>
            {crumbs.slice(0, -1).join(' / ')}
          </p>
        )}
      </header>
    )
  }

  const one = objs.length === 1 ? objs[0] : null
  const kinds = [...new Set(objs.map((o) => o.type))]
  const Icon = one ? TYPE_ICON[one.type] : kinds.length === 1 ? TYPE_ICON[kinds[0]] : Copy
  const locked = objs.length > 0 && objs.every((o) => o.locked)
  const hidden = objs.length > 0 && objs.every((o) => o.hidden)
  const ids = objs.map((o) => o.id)

  return (
    <header className="shrink-0 px-3 pb-2">
      <div className="flex items-center gap-1.5">
        <Icon size={13} className="shrink-0 text-ink-3" />
        <h2 className="min-w-0 truncate text-xs font-medium text-ink">
          {one ? objectLabel(one) : `已选 ${objs.length} 个对象`}
        </h2>
        {locked && <Lock size={11} className="shrink-0 text-ink-3" aria-label="已锁定" />}
        {hidden && <EyeOff size={11} className="shrink-0 text-ink-3" aria-label="已隐藏" />}
        {!one && <span className="shrink-0 text-xs text-ink-3">{summarize(objs)}</span>}
        <Menu
          width={172}
          align="end"
          trigger={
            <Button size="icon-sm" className="-mr-1 ml-auto" aria-label="对象操作">
              <MoreHorizontal size={13} className="text-ink-3" />
            </Button>
          }
        >
          <MenuItem shortcut={`${MOD}D`} onSelect={duplicateSelected}>
            <span className="flex items-center gap-2">
              <Copy size={13} className="text-ink-3" />
              复制
            </span>
          </MenuItem>
          <MenuItem
            onSelect={() =>
              updateObjects(ids, hidden ? '显示对象' : '隐藏对象', (o) => {
                o.hidden = !hidden
              })
            }
          >
            <span className="flex items-center gap-2">
              {hidden ? <Eye size={13} className="text-ink-3" /> : <EyeOff size={13} className="text-ink-3" />}
              {hidden ? '显示' : '隐藏'}
            </span>
          </MenuItem>
          <MenuItem
            onSelect={() =>
              updateObjects(ids, locked ? '解锁对象' : '锁定对象', (o) => {
                o.locked = !locked
              })
            }
          >
            <span className="flex items-center gap-2">
              {locked ? <LockOpen size={13} className="text-ink-3" /> : <Lock size={13} className="text-ink-3" />}
              {locked ? '解锁' : '锁定'}
            </span>
          </MenuItem>
          <MenuSeparator />
          <MenuItem danger shortcut="⌫" onSelect={deleteSelected}>
            <span className="flex items-center gap-2">
              <Trash2 size={13} />
              删除
            </span>
          </MenuItem>
        </Menu>
      </div>
    </header>
  )
}

function summarize(objs: CanvasObject[]): string {
  const n = (t: CanvasObject['type']) => objs.filter((o) => o.type === t).length
  const parts: string[] = []
  if (n('panel')) parts.push(`${n('panel')} 面板`)
  if (n('text')) parts.push(`${n('text')} 文字`)
  const marks = n('arrow') + n('shape')
  if (marks) parts.push(`${marks} 标注`)
  return parts.join(' · ')
}
