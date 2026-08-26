import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { createPortal } from 'react-dom'
import { Bold, Crop, Minimize2, Pencil, SlidersHorizontal } from 'lucide-react'
import { t as translate } from '@/i18n'
import { msg, type UiMessage } from '@/i18n'
import type { ManifestElement } from '@/lib/api'
import { cn } from '@/lib/utils'
import { LineStylePicker } from '@/components/inspector/controls/LineStylePicker'
import { LegendPositionPicker } from '@/components/inspector/controls/LegendPositionPicker'
import { useElementWriter } from '@/components/inspector/elementWrite'
import { hasTextStyleBar } from '@/components/inspector/TextStyleBar'
import { StyleToggle } from '@/components/inspector/controls/textRows'
import { optionLabel, propLabel } from '@/components/inspector/roles/registry'
import { Button } from '@/components/ui/Button'
import { ColorField, NumberField } from '@/components/ui/Input'
import { Popover } from '@/components/ui/Popover'
import { Tip } from '@/components/ui/Tooltip'
import { enterElementEdit, fitPanels, updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { usePanelDisplayManifest } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import type {
  ArrowObject,
  CanvasObject,
  PanelObject,
  ShapeObject,
  TextObject,
} from '@/types/document'
import { useQuickEdit } from './quickEditStore'

/**
 * 选中对象旁的轻量上下文工具条（Quick Edit 的可发现入口）。
 *
 * 右键弹层（QuickEdit）与属性页早就有这些能力，但都是隐形入口——普通用户
 * 发现不了（审计 P9）。这条小工具条在**单选**时贴着选择框出现，按对象给
 * 3–5 个高频动作，写入与属性页**共用同一套 writer / actions**（override 经
 * setOverride、画布对象经 updateObjects），不是第二套数据通道。
 *
 * 纪律：拖动/缩放期间隐藏（pointerdown 即藏、pointerup 再现）；Esc 关闭
 * （本次选择内不再出现）；双击文字仍进内容编辑；右键菜单照旧。
 */

const qb = (key: string, values?: Record<string, unknown>) =>
  translate(`contextBar.${key}`, { ns: 'workspace', ...(values ?? {}) })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

const MARGIN = 8
/** 顶栏 + 标签条的高度：工具条不该盖到它们上面 */
const TOP_SAFE = 76

export function ContextBar() {
  useTranslation('workspace')
  const ids = useSelectionStore((s) => s.ids)
  const gids = useUiStore((s) => s.selectedGids)
  const elementPanelId = useUiStore((s) => s.elementPanelId)
  const editingText = useUiStore((s) => s.editingTextId)
  const cropTarget = useUiStore((s) => s.cropTargetId)
  const tool = useUiStore((s) => s.tool)
  const quickOpen = useQuickEdit((s) => s.target)
  const objects = useDocumentStore((s) => s.doc.objects)
  const zoom = useViewportStore((s) => s.zoom)
  const panX = useViewportStore((s) => s.panX)
  const panY = useViewportStore((s) => s.panY)
  const rightOpen = useUiStore((s) => s.rightOpen)
  const rightWidth = useUiStore((s) => s.rightWidth)
  const leftOpen = useUiStore((s) => s.leftOpen)
  const leftWidth = useUiStore((s) => s.leftWidth)
  const layout = useUiStore((s) => s.layout)

  const [dragging, setDragging] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  // 目标解析：图内编辑态看 gid，否则看画布单选
  const panel = objects.find(
    (o): o is PanelObject => o.id === elementPanelId && o.type === 'panel',
  )
  const gid = panel && gids.length === 1 ? gids[0] : null
  const obj: CanvasObject | null =
    !panel && ids.length === 1 ? (objects.find((o) => o.id === ids[0]) ?? null) : null
  // 只有真给得出高频动作才出现——一个孤零零的「全部属性」按钮不值得盖住画布
  const manifest = usePanelDisplayManifest(panel)
  const element = gid ? (manifest?.elements.find((e) => e.gid === gid) ?? null) : null
  const hasActions = panel
    ? !!element && !!element.editable.length && elementHasQuick(element)
    : !!obj
  const targetKey = panel ? `el:${panel.id}:${gid ?? ''}` : obj ? `obj:${obj.id}` : ''
  // narrow 断点下侧栏是盖在画布上的覆盖式抽屉（z-30），portal 出来的工具条
  // （z-40）会压住并拦截抽屉里的控件；抽屉本来就把属性带到了眼前，此时让位
  const overlayDrawerOpen = layout === 'narrow' && (leftOpen || rightOpen)
  const active =
    !!targetKey &&
    hasActions &&
    !editingText &&
    !cropTarget &&
    !quickOpen &&
    !overlayDrawerOpen &&
    tool === 'select' &&
    !dismissed

  // Esc 关闭只作用于**这一次选择**；选择一变就重新出现
  useEffect(() => {
    setDismissed(false)
  }, [targetKey])

  // 拖动 / 框选期间藏起来：任何画布上的 pointerdown 都算（点工具条自己除外）
  useEffect(() => {
    const down = (e: PointerEvent) => {
      const node = e.target as Element | null
      if (node?.closest?.('[data-context-bar]')) return
      if (node?.closest?.('[data-radix-popper-content-wrapper]')) return
      setDragging(true)
    }
    const up = () => setDragging(false)
    window.addEventListener('pointerdown', down, true)
    window.addEventListener('pointerup', up, true)
    return () => {
      window.removeEventListener('pointerdown', down, true)
      window.removeEventListener('pointerup', up, true)
    }
  }, [])

  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // 不抢全局 Esc 的职责（退编辑态等）：只有焦点在工具条里才拦下来
      if (ref.current?.contains(document.activeElement)) {
        e.preventDefault()
        e.stopPropagation()
      }
      setDismissed(true)
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [active])

  // 量选择框、贴上去。依赖里带上 objects/zoom/pan：对象挪动、画布缩放都重贴
  useLayoutEffect(() => {
    if (!active || dragging) {
      setPos(null)
      return
    }
    const anchor = panel
      ? ((gid &&
          document.querySelector(
            `[data-element-svg="${CSS.escape(panel.id)}"] [id="${CSS.escape(gid)}"]`,
          )) ||
        document.querySelector(`[data-object-id="${CSS.escape(panel.id)}"]`))
      : obj
        ? document.querySelector(`[data-object-id="${CSS.escape(obj.id)}"]`)
        : null
    if (!anchor) {
      setPos(null)
      return
    }
    const r = (anchor as Element).getBoundingClientRect()
    const w = ref.current?.offsetWidth ?? 220
    const h = ref.current?.offsetHeight ?? 36
    // 不盖到侧栏上：右栏里正显示着同一批属性，盖住标签比不出现更糟
    const docked = layout !== 'narrow'
    const minX = (docked && leftOpen ? 44 + leftWidth : 0) + MARGIN
    const maxX = window.innerWidth - (docked && rightOpen ? rightWidth : 0) - w - MARGIN
    const x = Math.max(minX, Math.min(r.left + r.width / 2 - w / 2, maxX))
    let y = r.top - h - MARGIN
    if (y < TOP_SAFE) y = Math.min(r.bottom + MARGIN, window.innerHeight - h - MARGIN)
    setPos({ x, y })
  }, [active, dragging, panel, gid, obj, objects, zoom, panX, panY, layout, leftOpen, leftWidth, rightOpen, rightWidth])

  if (!active) return null

  return createPortal(
    <div
      ref={ref}
      data-context-bar
      role="toolbar"
      aria-label={qb('aria')}
      style={pos ? { left: pos.x, top: pos.y } : { left: -9999, top: -9999 }}
      className={cn(
        'fixed z-40 flex items-center gap-1 rounded-md border border-border bg-surface p-1',
        'text-xs text-ink shadow-pop',
        pos ? 'animate-pop-in' : 'invisible',
      )}
      onContextMenu={(e) => e.preventDefault()}
    >
      {panel && gid ? (
        <ElementQuickActions panel={panel} gid={gid} />
      ) : obj ? (
        <ObjectQuickActions obj={obj} />
      ) : null}
      <OpenInspectorButton />
    </div>,
    document.body,
  )
}

/** 「全部属性」——工具条到属性页的固定出口 */
function OpenInspectorButton() {
  return (
    <Tip label={qb('openInspector')} side="bottom">
      <Button
        size="icon-sm"
        aria-label={qb('openInspector')}
        onClick={() => {
          const ui = useUiStore.getState()
          ui.setRightTab('properties')
        }}
      >
        <SlidersHorizontal size={12} />
      </Button>
    </Tip>
  )
}

const Sep = () => <span aria-hidden className="mx-0.5 h-4 w-px shrink-0 bg-border" />

/* ------------------------------- 画布对象 --------------------------------- */

function ObjectQuickActions({ obj }: { obj: CanvasObject }) {
  switch (obj.type) {
    case 'text':
      return <TextObjectActions obj={obj} />
    case 'panel':
      return <PanelObjectActions obj={obj} />
    case 'arrow':
    case 'shape':
      return <MarkObjectActions obj={obj} />
    default:
      return null
  }
}

function TextObjectActions({ obj }: { obj: TextObject }) {
  const patch = (label: UiMessage, fn: (o: TextObject) => void) =>
    updateObjects([obj.id], label, (o) => {
      if (o.type === 'text') fn(o)
    })
  return (
    <>
      <NumberField
        className="w-[64px] shrink-0"
        value={obj.sizePt}
        min={3}
        max={96}
        step={0.5}
        precision={1}
        suffix="pt"
        title={translate('textControls.size', { ns: 'inspector' })}
        onChange={(v) => patch(hist('setFontSize'), (o) => (o.sizePt = v))}
      />
      <Button
        size="icon-sm"
        active={obj.bold}
        aria-pressed={obj.bold}
        aria-label={translate('text.bold', { ns: 'inspector' })}
        onClick={() => patch(hist('toggleBold'), (o) => (o.bold = !o.bold))}
      >
        <Bold size={12} />
      </Button>
      <ColorField
        className="w-[86px] shrink-0"
        value={obj.color}
        onChange={(v) => patch(hist('setTextColor'), (o) => (o.color = v))}
      />
      <Sep />
    </>
  )
}

function PanelObjectActions({ obj }: { obj: PanelObject }) {
  return (
    <>
      {obj.script && (
        <Button size="sm" className="gap-1 px-1.5" onClick={() => enterElementEdit(obj.id)}>
          <Pencil size={12} />
          {translate('panel.editElements', { ns: 'inspector' })}
        </Button>
      )}
      <Tip label={translate('panel.cropTip', { ns: 'inspector' })} side="bottom">
        <Button
          size="icon-sm"
          aria-label={translate('panel.crop', { ns: 'inspector' })}
          onClick={() => useUiStore.getState().setCropTarget(obj.id)}
        >
          <Crop size={12} />
        </Button>
      </Tip>
      <Tip label={translate('panel.fitTip', { ns: 'inspector' })} side="bottom">
        <Button
          size="icon-sm"
          aria-label={translate('panel.fit', { ns: 'inspector' })}
          onClick={() => fitPanels([obj.id])}
        >
          <Minimize2 size={12} />
        </Button>
      </Tip>
      <Sep />
    </>
  )
}

function MarkObjectActions({ obj }: { obj: ArrowObject | ShapeObject }) {
  const patch = (label: UiMessage, fn: (o: ArrowObject | ShapeObject) => void) =>
    updateObjects([obj.id], label, (o) => {
      if (o.type === 'arrow' || o.type === 'shape') fn(o as ArrowObject | ShapeObject)
    })
  return (
    <>
      <ColorField
        className="w-[86px] shrink-0"
        value={obj.color}
        onChange={(v) => patch(hist(obj.type === 'arrow' ? 'setArrowColor' : 'setStrokeColor'), (o) => (o.color = v))}
      />
      <NumberField
        className="w-[70px] shrink-0"
        value={obj.strokePt}
        min={0.1}
        max={20}
        step={0.25}
        precision={2}
        suffix="pt"
        title={translate('stroke.lineWidth', { ns: 'inspector' })}
        onChange={(v) => patch(hist('setStrokeWidth'), (o) => (o.strokePt = v))}
      />
      <Sep />
    </>
  )
}

/* ------------------------------- 图内元素 --------------------------------- */

/** 这些角色有专属快捷动作；其余元素不出工具条（右栏与右键仍可达一切） */
const ELEMENT_QUICK_ROLES = new Set(['line', 'linecoll', 'legend'])
const elementHasQuick = (el: ManifestElement) =>
  hasTextStyleBar(el) || ELEMENT_QUICK_ROLES.has(el.role)

function ElementQuickActions({ panel, gid }: { panel: PanelObject; gid: string }) {
  const manifest = usePanelDisplayManifest(panel)
  const el = manifest?.elements.find((e) => e.gid === gid)
  if (!el || !el.editable.length) return null
  return <ElementQuickInner panel={panel} element={el} />
}

function ElementQuickInner({
  panel,
  element,
}: {
  panel: PanelObject
  element: ManifestElement
}) {
  const w = useElementWriter(panel, element)
  const role = element.role

  if (hasTextStyleBar(element)) {
    const size = w.fieldOf('fontsize')
    const bold = w.read('weight') === 'bold'
    return (
      <>
        {size && (
          <NumberField
            className="w-[64px] shrink-0"
            value={Number(w.read('fontsize') ?? 9)}
            min={size.min}
            max={size.max}
            step={size.step ?? 0.5}
            precision={1}
            suffix={size.unit}
            title={translate('textControls.size', { ns: 'inspector' })}
            onChange={(v) => w.write('fontsize', v)}
            onScrubStart={w.beginGesture}
            onScrubEnd={w.endGesture}
          />
        )}
        {/* 与属性页、批量样式**同一个**三态图标按钮：这里曾经是一份自己写的
            <Button active={bold}>，长得一样但是第二份实现——同形的两份实现
            迟早分叉，本轮那些「多选就退化」的缺陷正是这么来的 */}
        {w.has('weight') && (
          <StyleToggle
            state={bold ? 'on' : 'off'}
            label={translate('textBar.bold', { ns: 'inspector' })}
            onClick={() => w.writeOnce('weight', bold ? 'normal' : 'bold')}
          >
            <Bold size={12} />
          </StyleToggle>
        )}
        {w.has('color') && (
          <ColorField
            className="w-[86px] shrink-0"
            value={String(w.read('color') ?? '#000000')}
            onChange={(v) => w.write('color', v, true)}
            onGestureEnd={w.endGesture}
          />
        )}
        <Sep />
      </>
    )
  }

  if (role === 'line' || role === 'linecoll') {
    const ls = w.fieldOf('linestyle')
    return (
      <>
        {w.has('color') && (
          <ColorField
            className="w-[86px] shrink-0"
            value={String(w.read('color') ?? '#000000')}
            onChange={(v) => w.write('color', v, true)}
            onGestureEnd={w.endGesture}
          />
        )}
        {w.has('linewidth') && (
          <NumberField
            className="w-[70px] shrink-0"
            value={Number(w.read('linewidth') ?? 1)}
            min={0.1}
            max={12}
            step={0.1}
            precision={2}
            suffix="pt"
            title={propLabel('linewidth', role)}
            onChange={(v) => w.write('linewidth', v)}
            onScrubStart={w.beginGesture}
            onScrubEnd={w.endGesture}
          />
        )}
        {ls && (
          <Popover
            width={190}
            align="start"
            trigger={
              <Button size="sm" className="px-1.5 font-mono" aria-label={propLabel('linestyle', role)}>
                {optionLabel('linestyle', String(w.read('linestyle') ?? '-'))}
              </Button>
            }
          >
            <LineStylePicker
              value={String(w.read('linestyle') ?? '-')}
              options={ls.options ?? []}
              onChange={(v) => w.writeOnce('linestyle', v)}
              ariaLabel={propLabel('linestyle', role)}
            />
          </Popover>
        )}
        <Sep />
      </>
    )
  }

  if (role === 'legend') {
    const loc = w.fieldOf('loc')
    const size = w.fieldOf('fontsize')
    return (
      <>
        {loc && (
          <Popover
            width={196}
            align="start"
            trigger={
              <Button size="sm" className="px-1.5" aria-label={propLabel('loc', role)}>
                {optionLabel('loc', String(w.read('loc') ?? 'best'))}
              </Button>
            }
          >
            <LegendPositionPicker
              value={String(w.read('loc') ?? 'best')}
              options={loc.options ?? []}
              onChange={(v) => w.writeOnce('loc', v)}
              ariaLabel={propLabel('loc', role)}
            />
          </Popover>
        )}
        {size && (
          <NumberField
            className="w-[64px] shrink-0"
            value={Number(w.read('fontsize') ?? 8)}
            min={size.min}
            max={size.max}
            step={size.step ?? 0.5}
            precision={1}
            suffix={size.unit}
            title={propLabel('fontsize', role)}
            onChange={(v) => w.write('fontsize', v)}
            onScrubStart={w.beginGesture}
            onScrubEnd={w.endGesture}
          />
        )}
        <Sep />
      </>
    )
  }

  return null
}
