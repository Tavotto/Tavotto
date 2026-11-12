import { useMemo } from 'react'
import { CircleQuestionMark, Crop, Minimize2, Pencil, RotateCcw } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import type { UiMessage } from '@/i18n'
import { useCanvasTypography } from '@/components/inspector/typographyAdapter'
import { Button } from '@/components/ui/Button'
import { ColorField, NumberField } from '@/components/ui/Input'
import { Tip } from '@/components/ui/Tooltip'
import { isContentFit, fitContentCrop } from '@/lib/panelEdgeCrop'
import { useCanEditPanel } from '@/lib/panelEditability'
import {
  beginCrop,
  enterElementEdit,
  fitPanels,
  fitPanelsToContent,
  resetPanelCrop,
  updateObjects,
} from '@/store/actions'
import { useExactPanelManifest } from '@/store/renderStore'
import { useWorkspaceStore } from '@/store/workspaceStore'
import { useAssetStore } from '@/store/assetStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import type {
  ArrowObject,
  CanvasObject,
  PanelObject,
  ShapeObject,
  TextObject,
} from '@/types/document'
import { FrameQuick } from './FrameQuick'
import { TextQuickControls } from './textQuick'
import { Sep } from './shared'
import { hist, qb } from './text'

/* ------------------------------- 画布对象 --------------------------------- */

export function ObjectQuickActions({
  obj,
  compact = false,
}: {
  obj: CanvasObject
  /** 停靠的属性页正铺着同一批文字控件时缩减（判据在 `ContextBar` 一处） */
  compact?: boolean
}) {
  switch (obj.type) {
    case 'text':
      return <TextObjectActions obj={obj} compact={compact} />
    case 'panel':
      return <PanelObjectActions obj={obj} />
    case 'arrow':
    case 'shape':
      return <MarkObjectActions obj={obj} />
    default:
      return null
  }
}

/**
 * 画布文字的浮动快捷编辑。
 *
 * **与属性页读同一个 selector、写同一个 action**（`useCanvasTypography`）：
 * 这条工具条以前是第二份实现——没有斜体、没有字体、mixed 状态无从谈起，
 * 而且 `o.bold = !o.bold` 与属性页的 `!bold` 在多选下会算出不同的结果。
 * 现在两边看到的是同一个适配器，一处改另一处当场就是新值。
 *
 * 布局按上下文不同（这里没有标签列），**数据与 action 共享**。
 *
 * `compact`：右栏属性页正开着——字体 / 字号 / B / I / 颜色 那一整行就在
 * 那里，这条再铺一遍是第二份摆放（审计 T27，与图内文字 T14 同一条判据）。
 * 缩减档只留字号 / 加粗 / 斜体，把最宽的字体下拉与取色器让给右栏。
 */
function TextObjectActions({ obj, compact }: { obj: TextObject; compact: boolean }) {
  const objs = useMemo(() => [obj], [obj])
  return <CanvasTextQuick objs={objs} compact={compact} />
}

/**
 * 画布文字的快捷排版行：单选与多选（`MultiSelectionBar` 在选区全是文字时，ADR 0089）
 * 同一份。`objs` 必须是稳定引用（调用方 memo）。
 */
export function CanvasTextQuick({ objs, compact }: { objs: TextObject[]; compact: boolean }) {
  const a = useCanvasTypography(objs)
  return <TextQuickControls a={a} compact={compact} familyWidth="w-[92px]" sizeFallback={10} />
}

function PanelObjectActions({ obj }: { obj: PanelObject }) {
  const editable = useCanEditPanel(obj)
  // 这张图在**项目里**的接入状态（`/api/panels` 的投影，与就绪度同一次计算）。
  // runtime 面板不在就绪度的 id 空间里（ADR 0013），拿不到也不该有。
  const cap = useAssetStore((s) => s.byId[obj.fileId]?.capability)
  // 两个条件问的是两件事，缺一不可：`!obj.script` = 这张图**此刻**没有图内
  // 编辑入口（文档记着的），`cap.status !== 'editable'` = 项目里它确实还没连上
  // （后端说的）。只看后者的话，派生同步还没跑完的那一瞬间会同时出现
  // 「编辑图内元素」与「为什么不能编辑？」两个按钮。
  const explainable = !editable && !!cap && cap.status !== 'editable'
  return (
    <div className="flex items-center gap-1">
      {/* 单图编辑：画布尺寸与留白（ADR 0161）；排版里不挂 */}
      <FrameQuick obj={obj} />
      {editable && (
        <Button size="md" onClick={() => { void enterElementEdit(obj.id) }}>
          <Pencil size={ICON_SIZE.sm} />
          {translate('panel.editElements', { ns: 'inspector' })}
        </Button>
      )}
      {/* 只是**入口**：打开接入状态并滚到这张图。选择一个字不动、脚本一行不跑、
          不切裁剪态——用户点的是一个问题，不是一个动作 */}
      {explainable && (
        <Button
          size="md"
          onClick={() => useProjectReadinessStore.getState().focusPanel(obj.fileId, 'quickedit')}
        >
          <CircleQuestionMark size={ICON_SIZE.sm} />
          {translate('readiness.whyNotEditable', { ns: 'workspace' })}
        </Button>
      )}
      <Tip label={translate('panel.cropTip', { ns: 'inspector' })} side="bottom">
        <Button
          size="icon-sm"
          aria-label={translate('panel.crop', { ns: 'inspector' })}
          onClick={() => beginCrop(obj.id)}
        >
          <Crop size={ICON_SIZE.sm} />
        </Button>
      </Tip>
      <Tip label={translate('panel.fitTip', { ns: 'inspector' })} side="bottom">
        <Button
          size="icon-sm"
          aria-label={translate('panel.fit', { ns: 'inspector' })}
          onClick={() => fitPanels([obj.id])}
        >
          <Minimize2 size={ICON_SIZE.sm} />
        </Button>
      </Tip>
      <LayoutPanelActions obj={obj} />
      <Sep />
    </div>
  )
}

/**
 * 多图排版里选中一张面板（不在拖动）时，胶囊里的裁剪两件事（ADR 0161）：
 * 「贴合内容」一次把四边裁到内容边缘（缺 `ink_bbox` 或已贴合时置灰）、「取消裁剪」（`resetPanelCrop`）。
 * 图里的标注跟不跟着图走按位置自动定（`lib/annotationScale`），没有开关。单图编辑里不出。
 */
function LayoutPanelActions({ obj }: { obj: PanelObject }) {
  const layout = useWorkspaceStore((s) => s.mode === 'layout')
  const manifest = useExactPanelManifest(obj)
  if (!layout) return null
  const ink = manifest?.elements.find((e) => e.gid === 'figure')?.ink_bbox
  const target = fitContentCrop(ink)
  const fitted = isContentFit(obj.crop, ink)
  return (
    <>
      <Sep />
      <Tip label={target ? qb('layoutFitContentTip') : qb('layoutFitContentNone')} side="bottom">
        <Button
          size="md"
          data-layout-action="fit-content"
          disabled={!target || fitted}
          onClick={() => fitPanelsToContent([obj.id])}
        >
          {qb('layoutFitContent')}
        </Button>
      </Tip>
      <Tip label={qb('layoutResetCropTip')} side="bottom">
        <Button
          size="md"
          data-layout-action="reset-crop"
          disabled={!obj.crop}
          onClick={() => resetPanelCrop([obj.id])}
        >
          <RotateCcw size={ICON_SIZE.sm} />
          {qb('layoutResetCrop')}
        </Button>
      </Tip>
    </>
  )
}

function MarkObjectActions({ obj }: { obj: ArrowObject | ShapeObject }) {
  const patch = (label: UiMessage, fn: (o: ArrowObject | ShapeObject) => void) =>
    updateObjects([obj.id], label, (o) => {
      if (o.type === 'arrow' || o.type === 'shape') fn(o as ArrowObject | ShapeObject)
    })
  return (
    <div className="flex items-center gap-1">
      <ColorField
        ariaLabel={translate(obj.type === 'arrow' ? 'stroke.color' : 'stroke.strokeColor', { ns: 'inspector' })}
        value={obj.color}
        onChange={(v) => patch(hist(obj.type === 'arrow' ? 'setArrowColor' : 'setStrokeColor'), (o) => (o.color = v))}
      />
      <NumberField
        fill
        className="w-[76px] shrink-0"
        value={obj.strokePt}
        min={0.1}
        max={20}
        step={0.25}
        precision={2}
        unit="pt"
        title={translate('stroke.lineWidth', { ns: 'inspector' })}
        onChange={(v) => patch(hist('setStrokeWidth'), (o) => (o.strokePt = v))}
      />
      <Sep />
    </div>
  )
}

