import { useMemo } from 'react'
import { Bold, CircleHelp, Crop, Italic, Minimize2, Pencil } from 'lucide-react'
import { t as translate } from '@/i18n'
import type { UiMessage } from '@/i18n'
import { fontStackOf } from '@/components/inspector/controls/fontStack'
import { StyleToggle } from '@/components/inspector/controls/textRows'
import { useCanvasTypography } from '@/components/inspector/typographyAdapter'
import { displayValueOf, nextToggle, toggleStateOf } from '@/lib/typography'
import { optionLabel } from '@/components/inspector/roles/registry'
import { Button } from '@/components/ui/Button'
import { ColorField, NumberField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Tip } from '@/components/ui/Tooltip'
import { enterElementEdit, fitPanels, updateObjects } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useProjectReadinessStore } from '@/store/projectReadinessStore'
import { useUiStore } from '@/store/uiStore'
import type {
  ArrowObject,
  CanvasObject,
  PanelObject,
  ShapeObject,
  TextObject,
} from '@/types/document'
import { Sep } from './shared'
import { hist } from './text'

/* ------------------------------- 画布对象 --------------------------------- */

export function ObjectQuickActions({ obj }: { obj: CanvasObject }) {
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

/**
 * 画布文字的浮动快捷编辑。
 *
 * **与属性页读同一个 selector、写同一个 action**（`useCanvasTypography`）：
 * 这条工具条以前是第二份实现——没有斜体、没有字体、mixed 状态无从谈起，
 * 而且 `o.bold = !o.bold` 与属性页的 `!bold` 在多选下会算出不同的结果。
 * 现在两边看到的是同一个适配器，一处改另一处当场就是新值。
 *
 * 布局按上下文不同（这里没有标签列），**数据与 action 共享**。
 */
function TextObjectActions({ obj }: { obj: TextObject }) {
  const objs = useMemo(() => [obj], [obj])
  const a = useCanvasTypography(objs)
  const family = a.fieldOf('fontFamily')
  const size = a.fieldOf('sizePt')
  const boldState = toggleStateOf(a.valueOf('weight'), 'bold')
  const italicState = toggleStateOf(a.valueOf('style'), 'italic')
  return (
    <>
      {family && (
        <Select
          className="w-[92px] shrink-0"
          ariaLabel={translate('textControls.font', { ns: 'inspector' })}
          value={String(displayValueOf(a.valueOf('fontFamily')) ?? '')}
          onChange={(v) => a.writeOnce('fontFamily', v)}
          options={(family.options ?? []).map((o) => ({
            value: o,
            label: <span style={{ fontFamily: fontStackOf(o) }}>{optionLabel('fontfamily', o)}</span>,
          }))}
        />
      )}
      {size && (
        <NumberField
          className="w-[64px] shrink-0"
          value={Number(displayValueOf(a.valueOf('sizePt')) ?? 10)}
          min={size.min}
          max={size.max}
          step={size.step ?? 0.5}
          precision={1}
          suffix={size.unit}
          title={translate('textControls.size', { ns: 'inspector' })}
          onChange={(v) => a.write('sizePt', v)}
          onScrubStart={a.beginGesture}
          onScrubEnd={a.endGesture}
        />
      )}
      <StyleToggle
        state={boldState}
        label={translate('textBar.bold', { ns: 'inspector' })}
        onClick={() => a.writeOnce('weight', nextToggle(a.valueOf('weight'), 'bold', 'normal'))}
      >
        <Bold size={12} />
      </StyleToggle>
      <StyleToggle
        state={italicState}
        label={translate('textBar.italic', { ns: 'inspector' })}
        onClick={() => a.writeOnce('style', nextToggle(a.valueOf('style'), 'italic', 'normal'))}
      >
        <Italic size={12} />
      </StyleToggle>
      <ColorField
        className="w-[86px] shrink-0"
        value={String(displayValueOf(a.valueOf('color')) ?? '#000000')}
        onChange={(v) => a.write('color', v, true)}
        onGestureEnd={a.endGesture}
      />
      <Sep />
    </>
  )
}

function PanelObjectActions({ obj }: { obj: PanelObject }) {
  // 这张图在**项目里**的接入状态（`/api/panels` 的投影，与就绪度同一次计算）。
  // runtime 面板不在就绪度的 id 空间里（ADR 0013），拿不到也不该有。
  const cap = useAssetStore((s) => s.byId[obj.fileId]?.capability)
  // 两个条件问的是两件事，缺一不可：`!obj.script` = 这张图**此刻**没有图内
  // 编辑入口（文档记着的），`cap.status !== 'editable'` = 项目里它确实还没连上
  // （后端说的）。只看后者的话，派生同步还没跑完的那一瞬间会同时出现
  // 「编辑图内元素」与「为什么不能编辑？」两个按钮。
  const explainable = !obj.script && !!cap && cap.status !== 'editable'
  return (
    <>
      {obj.script && (
        <Button size="sm" className="gap-1 px-1.5" onClick={() => enterElementEdit(obj.id)}>
          <Pencil size={12} />
          {translate('panel.editElements', { ns: 'inspector' })}
        </Button>
      )}
      {/* 只是**入口**：打开接入状态并滚到这张图。选择一个字不动、脚本一行不跑、
          不切裁剪态——用户点的是一个问题，不是一个动作 */}
      {explainable && (
        <Button
          size="sm"
          className="gap-1 px-1.5"
          onClick={() => useProjectReadinessStore.getState().focusPanel(obj.fileId)}
        >
          <CircleHelp size={12} />
          {translate('readiness.whyNotEditable', { ns: 'workspace' })}
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

