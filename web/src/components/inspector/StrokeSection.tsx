import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { updateObjects } from '@/store/actions'
import type {
  ArrowObject,
  DashStyle,
  HeadSize,
  LineCap,
  LineJoin,
  ShapeObject,
} from '@/types/document'
import { arrowHeads, legacyHead, shapeStrokeOn } from '@/types/document'
import { DEFAULT_LINE_CAP, defaultLineJoin } from '@/lib/shapeGeometry'
import { Button } from '../ui/Button'
import { Row, Section } from '../ui/Field'
import { Segmented } from '../ui/Segmented'
import { SummaryRow } from '../ui/SummaryRow'
import { INSPECTOR_LABEL_W } from './layout'
import { ColorField, NO_COLOR, NumberField } from '../ui/Input'
import { Popover } from '../ui/Popover'
import { EffectToggle } from './controls/EffectToggle'
import { OptionGrid, type GridOption } from './controls/OptionGrid'
import { PickerTrigger } from './controls/PickerTrigger'
import { shared } from './common'

/** 本组文案 inspector:stroke.*，历史标签 inspector:history.* */
const sk = (key: string) => translate(`stroke.${key}`, { ns: 'inspector' })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

const DASH_VALUES: DashStyle[] = ['solid', 'dashed', 'dotted', 'longdash', 'dashdot', 'longdashdot']
/** 线型示意的段长（示意图 32px 宽，与真实比例同形不同尺） */
const DASH_GLYPH: Partial<Record<DashStyle, string>> = {
  dashed: '4 3',
  dotted: '0.5 3',
  longdash: '8 3',
  dashdot: '4 3 0.5 3',
  longdashdot: '8 3 0.5 3',
}

type ArrowHead = ReturnType<typeof arrowHeads>['end']
const HEAD_VALUES: ArrowHead[] = ['none', 'open', 'triangle', 'stealth', 'diamond', 'dot', 'bar']

/** 每种线型的形状示意：一段线，跟随文字色 */
function DashGlyph({ dash }: { dash: DashStyle }) {
  const dasharray = DASH_GLYPH[dash]
  return (
    <svg width="32" height="16" viewBox="0 0 32 16" aria-hidden="true" className="shrink-0">
      <line
        x1="3"
        y1="8"
        x2="29"
        y2="8"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray={dasharray}
      />
    </svg>
  )
}

/** 每种端型的形状示意：一段线 + 右端的端型，跟随文字色 */
function HeadGlyph({ head }: { head: ArrowHead }) {
  // 实心端型让线收在端型里（与真实几何同一个思路：线不戳出尖）
  const lineEnd =
    head === 'open' || head === 'triangle' ? 22 : head === 'stealth' ? 23 : head === 'diamond' ? 24 : head === 'dot' ? 25 : 29
  return (
    <svg width="32" height="16" viewBox="0 0 32 16" aria-hidden="true" className="shrink-0">
      <line x1="3" y1="8" x2={lineEnd} y2="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      {head === 'open' && (
        <path
          d="M22 3.5 L29 8 L22 12.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      {head === 'triangle' && <path d="M21 3.5 L30 8 L21 12.5 Z" fill="currentColor" />}
      {head === 'stealth' && <path d="M30 8 L21 3.5 L24 8 L21 12.5 Z" fill="currentColor" />}
      {head === 'diamond' && <path d="M30 8 L25.5 4 L21 8 L25.5 12 Z" fill="currentColor" />}
      {head === 'dot' && <circle cx="26.5" cy="8" r="3.5" fill="currentColor" />}
      {head === 'bar' && (
        <line x1="29" y1="3.5" x2="29" y2="12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      )}
    </svg>
  )
}

/**
 * 带图形示意的取值选择：端型与线型共用，与图内属性页的线型 / 标记 / 纹理选择器**同一副外壳**
 * （`Popover + PickerTrigger + OptionGrid`；2026-09-14 审计 S5 之前这里是一套手写的
 * combobox + listbox，弹层不走 portal，触发器与别的选择器长成两种）。
 * 每项显示「示意图 + 文案」；多选不一致时触发器显示「多个值」。
 */
function GlyphSelect<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  labelOf,
  glyphOf,
}: {
  value: T | null
  options: T[]
  onChange: (v: T) => void
  ariaLabel: string
  labelOf: (v: T) => string
  glyphOf: (v: T) => ReactNode
}) {
  const [open, setOpen] = useState(false)
  const grid: GridOption<T>[] = options.map((o) => ({
    value: o,
    label: labelOf(o),
    preview: (
      <span className="flex items-center gap-2 px-1 text-xs">
        {glyphOf(o)}
        <span className="truncate">{labelOf(o)}</span>
      </span>
    ),
    code: o,
  }))
  return (
    <Popover
      // 弹层宽 = 触发器宽（打磨 E11）：上下相邻的「线型」「标记」此前一个 216、
      // 一个 228，两种宽度挨在一起；Select 的弹层一直是跟着触发器的
      width="trigger"
      align="start"
      open={open}
      onOpenChange={setOpen}
      trigger={
        <PickerTrigger
          ariaLabel={ariaLabel}
          mixed={value === null}
          preview={value !== null && glyphOf(value)}
        >
          {value !== null && labelOf(value)}
        </PickerTrigger>
      }
    >
      <OptionGrid
        value={value}
        options={grid}
        onChange={onChange}
        onPick={() => setOpen(false)}
        previewHasLabel
        columns={1}
        ariaLabel={ariaLabel}
      />
    </Popover>
  )
}

/**
 * 标注（箭头 / 形状）的外观分组。
 *
 * **标题不再重复对象类型**：右栏头部已经写着「箭头」/「矩形」，分组再写一遍
 * 是同一句话的第二份（审计 T28）。这里叫「外观」——它说的是这一组管什么，
 * 不是这是个什么对象。
 */
function AppearanceSection({ children }: { children: ReactNode }) {
  return (
    <Section title={sk('appearance')}>
      <div className="flex flex-col gap-1.5">{children}</div>
    </Section>
  )
}

/** 端型下拉：GlyphSelect 的端型特化，文案取 stroke.head.* */
function HeadSelect({
  value,
  onChange,
  ariaLabel,
}: {
  value: ArrowHead | null
  onChange: (v: ArrowHead) => void
  ariaLabel: string
}) {
  return (
    <GlyphSelect
      value={value}
      options={HEAD_VALUES}
      onChange={onChange}
      ariaLabel={ariaLabel}
      labelOf={(v) => sk(`head.${v}`)}
      glyphOf={(v) => <HeadGlyph head={v} />}
    />
  )
}

/** 线型下拉：与端型同一个控件，真实线段示意 + 画布自己的显示名（§16） */
function DashRow({
  value,
  onChange,
}: {
  value: DashStyle | null
  onChange: (v: DashStyle) => void
}) {
  return (
    <Row label={sk('dash')} labelWidth={INSPECTOR_LABEL_W}>
      <GlyphSelect
        value={value}
        options={DASH_VALUES}
        onChange={onChange}
        ariaLabel={sk('dash')}
        labelOf={(v) => sk(`dashStyle.${v}`)}
        glyphOf={(v) => <DashGlyph dash={v} />}
      />
    </Row>
  )
}

/**
 * 线宽：箭头与形状同一行（半列 pt 框）。多选不一致时留空 +「多个值」占位。
 * 两种标注的外观分组共用这一行，不再一边与颜色挤一行、一边与不透明度挤一行。
 */
function StrokeWidthRow({ value, onChange }: { value: number | undefined; onChange: (v: number) => void }) {
  return (
    <Row label={sk('lineWidth')} labelWidth={INSPECTOR_LABEL_W}>
      <NumberField
        half
        ariaLabel={sk('lineWidth')}
        value={value ?? 1}
        mixed={value === undefined}
        step={0.25}
        min={0.1}
        max={20}
        precision={2}
        unit="pt"
        onChange={onChange}
      />
    </Row>
  )
}

/** 描边不透明度（半列 %，与填充不透明度同一种控件）；100% 不写字段 */
function StrokeOpacityRow({
  value,
  onChange,
}: {
  value: number | undefined
  onChange: (v: number) => void
}) {
  return (
    <div data-prop="strokeOpacity">
      <Row label={sk('strokeOpacity')} labelWidth={INSPECTOR_LABEL_W}>
        <NumberField
          half
          ariaLabel={sk('strokeOpacity')}
          value={Math.round((value ?? 1) * 100)}
          mixed={value === undefined}
          step={5}
          min={0}
          max={100}
          precision={0}
          unit="%"
          onChange={onChange}
        />
      </Row>
    </div>
  )
}

const CAP_VALUES: LineCap[] = ['butt', 'round', 'square']
const JOIN_VALUES: LineJoin[] = ['miter', 'round', 'bevel']
const SIZE_VALUES: HeadSize[] = ['small', 'medium', 'large']

/** 三选一的取值行（线端 / 拐角 / 端型大小共用）：文案取 stroke.<group>.<value> */
function ChoiceRow<T extends string>({
  label,
  group,
  values,
  value,
  onChange,
  disabledTitle,
}: {
  label: string
  group: string
  values: T[]
  value: T | null
  onChange: (v: T) => void
  /** 此刻选不了时的原因（走 title）；有它整行置灰 */
  disabledTitle?: string
}) {
  return (
    <Row label={label} labelWidth={INSPECTOR_LABEL_W}>
      <Segmented
        ariaLabel={label}
        value={value}
        onChange={onChange}
        items={values.map((v) => ({
          value: v,
          label: sk(`${group}.${v}`),
          disabled: !!disabledTitle,
          title: disabledTitle,
        }))}
      />
    </Row>
  )
}

/**
 * 低频描边项的折叠行「更多」（`ui/SummaryRow`，右值只写改过几项）。
 * 折叠状态是面板自己的：换选中对象不丢。
 */
function MoreFold({ changed, children }: { changed: number; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <SummaryRow
      label={sk('more')}
      value={changed > 0 ? translate('stroke.moreCount', { ns: 'inspector', n: changed }) : undefined}
      open={open}
      onToggle={() => setOpen((v) => !v)}
      data-fold="stroke-more"
    >
      <div className="flex flex-col gap-1.5">{children}</div>
    </SummaryRow>
  )
}

/** 这一组对象里有没有任何一个把这项改离缺省（折叠行右值的项数） */
const countChanged = (flags: boolean[]) => flags.filter(Boolean).length

/** 写新端型时同步维护旧 head 字段；规则在 `types/document.legacyHead` 一份 */
function syncLegacyHead(o: ArrowObject): void {
  o.head = legacyHead(arrowHeads(o))
}

export function ArrowSection({ objs }: { objs: ArrowObject[] }) {
  useTranslation('inspector')
  const ids = objs.map((o) => o.id)
  const patch = (label: UiMessage, fn: (o: ArrowObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'arrow') fn(o)
    })
  const arrowColor = shared(objs, (o) => (o as ArrowObject).color)
  const dash = shared(objs, (o) => (o as ArrowObject).dash ?? 'solid')
  const hasHead = (o: ArrowObject, end: 'start' | 'end') => arrowHeads(o)[end] !== 'none'
  const setSize = (which: 'Start' | 'End') => (v: HeadSize) =>
    patch(hist(`setHeadSize${which}`), (o) => {
      const key = `headSize${which}` as const
      if (v === 'medium') delete o[key]
      else o[key] = v
    })

  return (
    <>
      <AppearanceSection>
        {/* 线型放首行；端型排在线宽 / 颜色之前（审计 T28） */}
        <DashRow
          value={dash ?? null}
          onChange={(v) => patch(hist('setDash'), (o) => (o.dash = v === 'solid' ? undefined : v))}
        />
        <Row label={sk('end')} labelWidth={INSPECTOR_LABEL_W}>
          <HeadSelect
            value={shared(objs, (o) => arrowHeads(o as ArrowObject).end) ?? null}
            onChange={(v) =>
              patch(hist('setHeadEnd'), (o) => {
                const prev = arrowHeads(o) // 先取旧值：设了新字段后旧 head 不再参与推导
                o.headEnd = v
                o.headStart = prev.start
                syncLegacyHead(o)
              })
            }
            ariaLabel={sk('end')}
          />
        </Row>
        <Row label={sk('start')} labelWidth={INSPECTOR_LABEL_W}>
          <HeadSelect
            value={shared(objs, (o) => arrowHeads(o as ArrowObject).start) ?? null}
            onChange={(v) =>
              patch(hist('setHeadStart'), (o) => {
                const prev = arrowHeads(o)
                o.headStart = v
                o.headEnd = prev.end
                syncLegacyHead(o)
              })
            }
            ariaLabel={sk('start')}
          />
        </Row>
        {/* 与形状同一套行语法（2026-10-07 §9.2）：线宽半列一行、颜色一行（色块 + hex） */}
        <StrokeWidthRow
          value={shared(objs, (o) => (o as ArrowObject).strokePt)}
          onChange={(v) => patch(hist('setStrokeWidth'), (o) => (o.strokePt = v))}
        />
        <Row label={sk('color')} labelWidth={INSPECTOR_LABEL_W}>
          {/* 不一致时色块画成「多个值」，不退回一个假的公共色；value 只当取色盘起点 */}
          <ColorField
            ariaLabel={sk('color')}
            mixed={arrowColor === undefined}
            value={arrowColor ?? objs[0]?.color ?? NO_COLOR}
            onChange={(v) => patch(hist('setArrowColor'), (o) => (o.color = v))}
          />
        </Row>
        {/* 命令对到控件列（不是整栏宽的第二种按钮行） */}
        <Row labelWidth={INSPECTOR_LABEL_W}>
          <Button
            variant="secondary"
            size="sm"
            data-reverse-arrow
            onClick={() =>
              patch(hist('reverseArrow'), (o) => {
                const s = o.start
                o.start = o.end
                o.end = s
              })
            }
          >
            {sk('reverse')}
          </Button>
        </Row>
      </AppearanceSection>
      {/* 低频项收进一行「更多」：端型大小、描边不透明度、线端、拐角（ADR 0174） */}
      <MoreFold
        changed={countChanged([
          objs.some((o) => o.headSizeStart != null || o.headSizeEnd != null),
          objs.some((o) => o.strokeOpacity != null),
          objs.some((o) => o.lineCap != null && o.lineCap !== DEFAULT_LINE_CAP),
          objs.some((o) => o.lineJoin != null && o.lineJoin !== 'round'),
        ])}
      >
        {objs.some((o) => hasHead(o, 'end')) && (
          <ChoiceRow
            label={sk('endSize')}
            group="size"
            values={SIZE_VALUES}
            value={shared(objs, (o) => (o as ArrowObject).headSizeEnd ?? 'medium') ?? null}
            onChange={setSize('End')}
          />
        )}
        {objs.some((o) => hasHead(o, 'start')) && (
          <ChoiceRow
            label={sk('startSize')}
            group="size"
            values={SIZE_VALUES}
            value={shared(objs, (o) => (o as ArrowObject).headSizeStart ?? 'medium') ?? null}
            onChange={setSize('Start')}
          />
        )}
        <StrokeOpacityRow
          value={shared(objs, (o) => (o as ArrowObject).strokeOpacity ?? 1)}
          onChange={(v) => patch(hist('setStrokeOpacity'), (o) => setOpacity(o, v))}
        />
        <LineEndRows
          cap={shared(objs, (o) => (o as ArrowObject).lineCap ?? DEFAULT_LINE_CAP) ?? null}
          join={shared(objs, (o) => (o as ArrowObject).lineJoin ?? 'round') ?? null}
          dotted={dash === 'dotted'}
          onCap={(v) => patch(hist('setLineCap'), (o) => setCap(o, v))}
          onJoin={(v) => patch(hist('setLineJoin'), (o) => setJoin(o, v, 'round'))}
        />
      </MoreFold>
    </>
  )
}

/** 线端 + 拐角两行；点线靠圆线帽画点，线端行置灰并说原因（title） */
function LineEndRows({
  cap,
  join,
  dotted,
  hideJoin,
  onCap,
  onJoin,
}: {
  cap: LineCap | null
  join: LineJoin | null
  dotted: boolean
  /** 直线只有一段，没有拐角 */
  hideJoin?: boolean
  onCap: (v: LineCap) => void
  onJoin: (v: LineJoin) => void
}) {
  return (
    <>
      <ChoiceRow
        label={sk('capRow')}
        group="cap"
        values={CAP_VALUES}
        value={dotted ? 'round' : cap}
        onChange={onCap}
        disabledTitle={dotted ? sk('capDotted') : undefined}
      />
      {!hideJoin && (
        <ChoiceRow label={sk('joinRow')} group="join" values={JOIN_VALUES} value={join} onChange={onJoin} />
      )}
    </>
  )
}

/** 写入器：等于缺省就删字段（旧文档与新文档里「没动过」逐字相同，ADR 0174） */
function setOpacity(o: { strokeOpacity?: number }, v: number): void {
  const f = Math.max(0, Math.min(1, v / 100))
  if (f < 1) o.strokeOpacity = f
  else delete o.strokeOpacity
}
function setCap(o: { lineCap?: LineCap }, v: LineCap): void {
  if (v === DEFAULT_LINE_CAP) delete o.lineCap
  else o.lineCap = v
}
function setJoin(o: { lineJoin?: LineJoin }, v: LineJoin, fallback: LineJoin): void {
  if (v === fallback) delete o.lineJoin
  else o.lineJoin = v
}

export function ShapeSection({ objs }: { objs: ShapeObject[] }) {
  useTranslation('inspector')
  const ids = objs.map((o) => o.id)
  // line 只有描边；brace 只描边不填充
  const hasFillable = objs.some((o) => o.shape !== 'line' && o.shape !== 'brace')
  const allRect = objs.every((o) => o.shape === 'rect')
  const allPolygon = objs.every((o) => o.shape === 'polygon')
  const fill = shared(objs, (o) => (o as ShapeObject).fill)
  // 全都有填充、只是颜色不同：填充是开着的，色块画「多个值」（之前 shared 给 undefined，
  // 整行退回「＋添加填充」，一点就把所有人的填充刷成白色）。有的有、有的没有仍按「没开」处理
  const fillMixed = fill === undefined && objs.length > 0 && objs.every((o) => !!o.fill)
  const fillOn = !!fill || fillMixed
  const strokeColor = shared(objs, (o) => (o as ShapeObject).color)
  // 描边开关只对封闭形状（直线 / 大括号只有描边）；开着 = 有任何一个封闭形状有描边
  const closed = objs.filter((o) => o.shape !== 'line' && o.shape !== 'brace')
  const strokeOn = closed.length === 0 || closed.some(shapeStrokeOn)
  // 描边的细节行（线宽 / 线型 / 更多）只在还有描边的时候出现
  const showStroke = objs.some(shapeStrokeOn)
  const dash = shared(objs, (o) => (o as ShapeObject).dash ?? 'solid')
  const allLine = objs.every((o) => o.shape === 'line')
  const fillOpacity = shared(objs, (o) => (o as ShapeObject).fillOpacity ?? 1)
  const patch = (label: UiMessage, fn: (o: ShapeObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'shape') fn(o)
    })

  return (
    <>
    <AppearanceSection>
        {/*
          填充 → 描边 → 形状本身的参数（审计 T28：矩形突出填充 / 描边 / 圆角）。
          填充与画布文字的背景同一种模式：关着是「＋添加填充」，开了是真开关，
          不透明度跟在它下面——三处「添加式扩展」在产品里是同一个控件。
        */}
        {hasFillable && (
          <Row label={sk('fill')} labelWidth={INSPECTOR_LABEL_W}>
            <EffectToggle
              on={fillOn}
              label={sk('fill')}
              addLabel={sk('addFill')}
              onAdd={() => patch(hist('addFill'), (o) => (o.fill = '#FFFFFF'))}
              onOff={() => patch(hist('clearFill'), (o) => (o.fill = null))}
            />
            {fillOn && (
              <ColorField
                ariaLabel={sk('fill')}
                mixed={fillMixed}
                value={fill ?? objs[0]?.fill ?? NO_COLOR}
                onChange={(v) => patch(hist('setFill'), (o) => (o.fill = v))}
              />
            )}
          </Row>
        )}
        {hasFillable && fillOn && (
          // 填充不透明度：填充自己的一行半列（此前挤在线宽那一行的右端，靠 index.css 的 40px 框）
          <div data-prop="fillOpacity">
            <Row label={sk('fillOpacity')} labelWidth={INSPECTOR_LABEL_W}>
              <NumberField
                half
                ariaLabel={sk('fillOpacity')}
                value={Math.round((fillOpacity ?? 1) * 100)}
                mixed={fillOpacity === undefined}
                step={5}
                min={0}
                max={100}
                precision={0}
                unit="%"
                onChange={(v) =>
                  patch(hist('setFillOpacity'), (o) => {
                    const f = Math.max(0, Math.min(1, v / 100))
                    if (f < 1) o.fillOpacity = f
                    else delete o.fillOpacity
                  })
                }
              />
            </Row>
          </div>
        )}
        <Row label={sk('strokeColor')} labelWidth={INSPECTOR_LABEL_W}>
          {hasFillable && (
            // 与填充同一种开关：关着是「＋添加描边」，开着是真开关；关掉 = 无边框，
            // 画布 / 缩略图 / 所有导出都不画描边，下面的线宽 / 线型 / 更多收起
            <EffectToggle
              on={strokeOn}
              label={sk('strokeColor')}
              addLabel={sk('addStroke')}
              onAdd={() =>
                patch(hist('addStroke'), (o) => {
                  delete o.stroke
                })
              }
              onOff={() =>
                patch(hist('clearStroke'), (o) => {
                  if (o.shape !== 'line' && o.shape !== 'brace') o.stroke = false
                })
              }
            />
          )}
          {(!hasFillable || strokeOn) && (
            <ColorField
              ariaLabel={sk('strokeColor')}
              mixed={strokeColor === undefined}
              value={strokeColor ?? objs[0]?.color ?? NO_COLOR}
              onChange={(v) => patch(hist('setStrokeColor'), (o) => (o.color = v))}
            />
          )}
        </Row>
        {showStroke && (
          <StrokeWidthRow
            value={shared(objs, (o) => (o as ShapeObject).strokePt)}
            onChange={(v) => patch(hist('setStrokeWidth'), (o) => (o.strokePt = v))}
          />
        )}
        {showStroke && (
          <DashRow
            value={dash ?? null}
            onChange={(v) => patch(hist('setDash'), (o) => (o.dash = v === 'solid' ? undefined : v))}
          />
        )}
        {allRect && (
          <Row label={sk('cornerRadius')} labelWidth={INSPECTOR_LABEL_W}>
            <NumberField
              half
              ariaLabel={sk('cornerRadius')}
              value={shared(objs, (o) => (o as ShapeObject).cornerRadius ?? 0) ?? 0}
              step={0.5}
              min={0}
              max={50}
              precision={1}
              unit="mm"
              onChange={(v) =>
                patch(hist('setCornerRadius'), (o) => {
                  if (v > 0) o.cornerRadius = v
                  else delete o.cornerRadius
                })
              }
            />
          </Row>
        )}
        {allPolygon && (
          <Row label={sk('sides')} labelWidth={INSPECTOR_LABEL_W}>
            <NumberField
              half
              ariaLabel={sk('sides')}
              value={shared(objs, (o) => (o as ShapeObject).sides ?? 6) ?? 6}
              step={1}
              min={3}
              max={12}
              onChange={(v) => patch(hist('setSides'), (o) => (o.sides = Math.round(v)))}
            />
          </Row>
        )}
    </AppearanceSection>
    {showStroke && (
      // 低频描边项收进一行「更多」：描边不透明度、线端、拐角（ADR 0174）
      <MoreFold
        changed={countChanged([
          objs.some((o) => o.strokeOpacity != null),
          objs.some((o) => o.lineCap != null && o.lineCap !== DEFAULT_LINE_CAP),
          objs.some((o) => o.lineJoin != null && o.lineJoin !== defaultLineJoin(o.shape)),
        ])}
      >
        <StrokeOpacityRow
          value={shared(objs, (o) => (o as ShapeObject).strokeOpacity ?? 1)}
          onChange={(v) => patch(hist('setStrokeOpacity'), (o) => setOpacity(o, v))}
        />
        <LineEndRows
          cap={shared(objs, (o) => (o as ShapeObject).lineCap ?? DEFAULT_LINE_CAP) ?? null}
          join={shared(objs, (o) => (o as ShapeObject).lineJoin ?? defaultLineJoin((o as ShapeObject).shape)) ?? null}
          dotted={dash === 'dotted'}
          hideJoin={allLine}
          onCap={(v) => patch(hist('setLineCap'), (o) => setCap(o, v))}
          onJoin={(v) => patch(hist('setLineJoin'), (o) => setJoin(o, v, defaultLineJoin(o.shape)))}
        />
      </MoreFold>
    )}
    </>
  )
}
