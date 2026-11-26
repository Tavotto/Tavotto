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
} from '@/types/document'
import { arrowHeads, DEFAULT_STAR_POINTS, DEFAULT_STAR_RATIO, type PathObject, type ShapeObject } from '@/types/document'
import { DEFAULT_LINE_CAP, STROKE_MITER_LIMIT, validDashPattern } from '@/lib/shapeGeometry'
import {
  isFillStrokeMark,
  markCornerRadiusCapable,
  markDefaultJoin,
  markFillable,
  markHasNoJoin,
  headedOf,
  markHeadCapable,
  markHeads,
  markSidesCapable,
  markStarCapable,
  markPathAlignCapable,
  markStrokeAlignCapable,
  strokeAlignOf,
  markFillRuleCapable,
  markStrokeOn,
  markStrokeToggle,
  setMarkHead,
  setMarkHeadSize,
  setMarkStrokeOff,
  type FillStrokeMark,
  type HeadedMark,
} from '@/lib/markStyle'
import { Button } from '../ui/Button'
import { Row, Section } from '../ui/Field'
import { Segmented } from '../ui/Segmented'
import { SummaryRow } from '../ui/SummaryRow'
import { INSPECTOR_LABEL_W } from './layout'
import { ColorField, NO_COLOR, NumberField, TextInput } from '../ui/Input'
import { Popover } from '../ui/Popover'
import { EffectToggle } from './controls/EffectToggle'
import { GradientEditor, GradientKindSelect, gradientOf, GradientTools, RadialRows } from './controls/GradientControls'
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
  // `data-prop`：问题面板「线条太细」（画布对象那一条，ADR 0208）定位到这一栏
  return (
    <div data-prop="strokePt">
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
    </div>
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

/**
 * 起点 / 终点端型两行（箭头与含开放子路径的自由路径共用；读写只走 `lib/markStyle` 的 `markHeads` / `setMarkHead`）。
 * `patch` 只会喂进有端型字段的对象。
 */
function HeadRows<T extends HeadedMark>({
  objs,
  patch,
}: {
  objs: T[]
  patch: (label: UiMessage, fn: (o: T) => void) => void
}) {
  const heads = headedOf(objs) as T[]
  return (
    <>
      <Row label={sk('end')} labelWidth={INSPECTOR_LABEL_W}>
        <HeadSelect
          value={shared(heads, (o) => markHeads(o as T).end) ?? null}
          onChange={(v) => patch(hist('setHeadEnd'), (o) => setMarkHead(o, 'end', v))}
          ariaLabel={sk('end')}
        />
      </Row>
      <Row label={sk('start')} labelWidth={INSPECTOR_LABEL_W}>
        <HeadSelect
          value={shared(heads, (o) => markHeads(o as T).start) ?? null}
          onChange={(v) => patch(hist('setHeadStart'), (o) => setMarkHead(o, 'start', v))}
          ariaLabel={sk('start')}
        />
      </Row>
    </>
  )
}

/** 端型大小（「更多」里）：只给真有端型的那一端 */
function HeadSizeRows<T extends HeadedMark>({
  objs,
  patch,
}: {
  objs: T[]
  patch: (label: UiMessage, fn: (o: T) => void) => void
}) {
  const heads = headedOf(objs) as T[]
  const has = (o: T, end: 'start' | 'end') => markHeads(o)[end] !== 'none'
  return (
    <>
      {heads.some((o) => has(o, 'end')) && (
        <ChoiceRow
          label={sk('endSize')}
          group="size"
          values={SIZE_VALUES}
          value={shared(heads, (o) => (o as T).headSizeEnd ?? 'medium') ?? null}
          onChange={(v) => patch(hist('setHeadSizeEnd'), (o) => setMarkHeadSize(o, 'end', v))}
        />
      )}
      {heads.some((o) => has(o, 'start')) && (
        <ChoiceRow
          label={sk('startSize')}
          group="size"
          values={SIZE_VALUES}
          value={shared(heads, (o) => (o as T).headSizeStart ?? 'medium') ?? null}
          onChange={(v) => patch(hist('setHeadSizeStart'), (o) => setMarkHeadSize(o, 'start', v))}
        />
      )}
    </>
  )
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

  return (
    <>
      <AppearanceSection>
        {/* 线型放首行；端型排在线宽 / 颜色之前（审计 T28） */}
        <DashRow
          value={dash ?? null}
          onChange={(v) => patch(hist('setDash'), (o) => (o.dash = v === 'solid' ? undefined : v))}
        />
        <HeadRows objs={objs} patch={patch} />
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
          objs.some((o) => o.miterLimit != null),
          objs.some((o) => o.dashPattern != null),
        ])}
      >
        <HeadSizeRows objs={objs} patch={patch} />
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
        {objs.some((o) => o.lineJoin === 'miter') && (
          <MiterLimitRow
            value={shared(objs, (o) => (o as ArrowObject).miterLimit ?? STROKE_MITER_LIMIT)}
            onChange={(v) => patch(hist('setMiterLimit'), (o) => setMiter(o, v))}
          />
        )}
        <DashPatternRow
          value={shared(objs, (o) => (o as ArrowObject).dashPattern ?? null)}
          onChange={(v) => patch(hist('setDashPattern'), (o) => setDashPattern(o, v))}
        />
      </MoreFold>
    </>
  )
}

/** 斜接上限：只在选了尖角时出现（圆角 / 斜角下它不起作用）；等于缺省 10 就删字段 */
function MiterLimitRow({ value, onChange }: { value: number | undefined; onChange: (v: number) => void }) {
  return (
    <div data-prop="miterLimit">
      <Row label={sk('miterLimit')} labelWidth={INSPECTOR_LABEL_W}>
        <NumberField
          half
          ariaLabel={sk('miterLimit')}
          value={value ?? STROKE_MITER_LIMIT}
          mixed={value === undefined}
          step={1}
          min={1}
          max={100}
          precision={1}
          onChange={onChange}
        />
      </Row>
    </div>
  )
}

/** 「4 2」「4, 2, 1, 2」→ 数组；读不成合法虚线（`validDashPattern`）回 null */
const parseDash = (text: string): number[] | null =>
  validDashPattern(
    text
      .split(/[\s,，、]+/)
      .filter(Boolean)
      .map(Number),
  )

/**
 * 自定义虚线（pt，线段 / 间隙交替）：一个文本框，写「4 2」这样的数；清空 = 回到上面选的线型。
 * 有它时上面的「线型」不再起作用（导出与画布同一条：`validDashPattern` 优先）。读不成数就标红、不写。
 */
function DashPatternRow({ value, onChange }: { value: number[] | undefined | null; onChange: (v: number[] | null) => void }) {
  const shown = value ? value.join(' ') : ''
  const [draft, setDraft] = useState<string | null>(null)
  const text = draft ?? shown
  const invalid = draft != null && draft.trim() !== '' && parseDash(draft) == null
  const commit = () => {
    if (draft == null) return
    const t = draft.trim()
    if (t === '') onChange(null)
    else {
      const p = parseDash(t)
      if (!p) return
      onChange(p)
    }
    setDraft(null)
  }
  return (
    <div data-prop="dashPattern">
      <Row label={sk('dashPattern')} labelWidth={INSPECTOR_LABEL_W}>
        <TextInput
          aria-label={sk('dashPattern')}
          title={sk('dashPatternTip')}
          placeholder={sk('dashPatternPlaceholder')}
          suffix="pt"
          align="left"
          value={value === null && draft == null ? '' : text}
          invalid={invalid}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') setDraft(null)
          }}
        />
      </Row>
    </div>
  )
}

const ALIGN_VALUES: ('inside' | 'center' | 'outside')[] = ['inside', 'center', 'outside']

/** 写入器：斜接上限 / 自定义虚线 / 描边对齐 / 填充规则，等于缺省就删字段 */
function setMiter(o: { miterLimit?: number }, v: number): void {
  const m = Math.max(1, v)
  if (Math.abs(m - STROKE_MITER_LIMIT) < 1e-9) delete o.miterLimit
  else o.miterLimit = m
}
function setDashPattern(o: { dashPattern?: number[] }, v: number[] | null): void {
  if (v) o.dashPattern = v
  else delete o.dashPattern
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

/**
 * 形状与自由路径（ADR 0185）的外观：两者的填充 / 描边字段逐字相同，所以是同一套控件；哪一行出现、写哪个字段
 * 全部问 `lib/markStyle` 的能力函数（这里不按对象类型分支）。路径含开放子路径时多出端型行（与箭头同一组控件）。
 */
export function ShapeSection({ objs }: { objs: FillStrokeMark[] }) {
  useTranslation('inspector')
  const ids = objs.map((o) => o.id)
  // line 只有描边；brace 只描边不填充
  const hasFillable = objs.some(markFillable)
  const allRect = objs.every(markCornerRadiusCapable)
  const allPolygon = objs.every(markSidesCapable)
  const allStar = objs.every(markStarCapable)
  const headed = headedOf(objs)
  const fill = shared(objs, (o) => (o as FillStrokeMark).fill)
  // 全都有填充、只是颜色不同：填充是开着的，色块画「多个值」（之前 shared 给 undefined，
  // 整行退回「＋添加填充」，一点就把所有人的填充刷成白色）。有的有、有的没有仍按「没开」处理
  const fillMixed = fill === undefined && objs.length > 0 && objs.every((o) => !!o.fill)
  const fillOn = !!fill || fillMixed
  const strokeColor = shared(objs, (o) => (o as FillStrokeMark).color)
  // 描边开关只对封闭形状与路径（直线 / 大括号只有描边）；开着 = 有任何一个可开关的对象有描边
  const closed = objs.filter(markStrokeToggle)
  const strokeOn = closed.length === 0 || closed.some(markStrokeOn)
  // 描边的细节行（线宽 / 线型 / 更多）只在还有描边的时候出现
  const showStroke = objs.some(markStrokeOn)
  const dash = shared(objs, (o) => (o as FillStrokeMark).dash ?? 'solid')
  const allLine = objs.every(markHasNoJoin)
  const fillOpacity = shared(objs, (o) => (o as FillStrokeMark).fillOpacity ?? 1)
  const patch = (label: UiMessage, fn: (o: FillStrokeMark) => void) =>
    updateObjects(ids, label, (o) => {
      if (isFillStrokeMark(o)) fn(o)
    })
  /** 渐变（ADR 0186）：填充渐变只给有填充的、描边渐变只给有描边的 */
  const fillables = objs.filter((o) => markFillable(o) && !!o.fill)
  const stroked = objs.filter(markStrokeOn)
  // 这一侧的渐变以第一个对象为准（多选时编辑写给全部）
  const fillGrad = fillables.map((o) => gradientOf(o, 'fill')).find(Boolean) ?? null
  const strokeGrad = stroked.map((o) => gradientOf(o, 'stroke')).find(Boolean) ?? null
  /** 径向的中心与半径在「更多」里：有径向时「更多」要在 */
  const radialOn = fillGrad?.kind === 'radial' || strokeGrad?.kind === 'radial'
  /** 描边对齐只写封闭形状与全闭合的路径；填充规则只写有填充的路径 */
  const aligned = objs.filter((o) => markStrokeAlignCapable(o) || markPathAlignCapable(o)) as (ShapeObject | PathObject)[]
  const filledPaths = objs.filter(markFillRuleCapable)
  /** 描边对齐写回：等于各自的缺省（形状内侧、路径居中）就删字段 */
  const setAlign = (v: 'inside' | 'center' | 'outside') =>
    updateObjects(ids, hist('setStrokeAlign'), (o) => {
      if (!isFillStrokeMark(o)) return
      if (markStrokeAlignCapable(o)) {
        if (v === 'inside') delete o.strokeAlign
        else o.strokeAlign = v
      } else if (markPathAlignCapable(o)) {
        if (v === 'center') delete o.strokeAlign
        else o.strokeAlign = v
      }
    })
  const patchPaths = (label: UiMessage, fn: (o: PathObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (isFillStrokeMark(o) && markFillRuleCapable(o)) fn(o)
    })
  /** 端型行只写有端型的那几个（选区里混着封闭形状时，它们一个字不动） */
  const patchHeaded = (label: UiMessage, fn: (o: HeadedMark) => void) =>
    updateObjects(ids, label, (o) => {
      if (markHeadCapable(o)) fn(o)
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
              anchor="fill"
              onAdd={() => patch(hist('addFill'), (o) => (o.fill = '#FFFFFF'))}
              onOff={() => patch(hist('clearFill'), (o) => (o.fill = null))}
            />
            {/* 渐变时（ADR 0186）色块与十六进制不显示（降级纯色自动取首个色标），这一行是类型下拉 + 角度 + 反向 */}
            {fillOn && !fillGrad && (
              <ColorField
                ariaLabel={sk('fill')}
                mixed={fillMixed}
                value={fill ?? objs[0]?.fill ?? NO_COLOR}
                onChange={(v) => patch(hist('setFill'), (o) => (o.fill = v))}
              />
            )}
            {fillOn && <GradientKindSelect objs={fillables} target="fill" />}
            {fillOn && fillGrad && <GradientTools objs={fillables} target="fill" />}
          </Row>
        )}
        {/* 色带常显，紧跟在填充那一行下面（PPT 式：点空白加色标、拖动、往下拖出删除） */}
        {hasFillable && fillOn && fillGrad && <GradientEditor objs={fillables} target="fill" />}
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
              onOff={() => patch(hist('clearStroke'), setMarkStrokeOff)}
            />
          )}
          {(!hasFillable || strokeOn) && !strokeGrad && (
            <ColorField
              ariaLabel={sk('strokeColor')}
              mixed={strokeColor === undefined}
              value={strokeColor ?? objs[0]?.color ?? NO_COLOR}
              onChange={(v) => patch(hist('setStrokeColor'), (o) => (o.color = v))}
            />
          )}
          {showStroke && <GradientKindSelect objs={stroked} target="stroke" />}
          {showStroke && strokeGrad && <GradientTools objs={stroked} target="stroke" />}
        </Row>
        {showStroke && strokeGrad && <GradientEditor objs={stroked} target="stroke" />}
        {showStroke && (
          <StrokeWidthRow
            value={shared(objs, (o) => (o as FillStrokeMark).strokePt)}
            onChange={(v) => patch(hist('setStrokeWidth'), (o) => (o.strokePt = v))}
          />
        )}
        {showStroke && (
          <DashRow
            value={dash ?? null}
            onChange={(v) => patch(hist('setDash'), (o) => (o.dash = v === 'solid' ? undefined : v))}
          />
        )}
        {showStroke && headed.length > 0 && <HeadRows objs={headed} patch={patchHeaded} />}
        {allRect && (
          <Row label={sk('cornerRadius')} labelWidth={INSPECTOR_LABEL_W}>
            <NumberField
              half
              ariaLabel={sk('cornerRadius')}
              value={shared(objs, (o) => (o as FillStrokeMark & { cornerRadius?: number }).cornerRadius ?? 0) ?? 0}
              step={0.5}
              min={0}
              max={50}
              precision={1}
              unit="mm"
              onChange={(v) =>
                patch(hist('setCornerRadius'), (o) => {
                  if (!markCornerRadiusCapable(o)) return
                  if (v > 0) o.cornerRadius = v
                  else delete o.cornerRadius
                })
              }
            />
          </Row>
        )}
        {allPolygon && (
          // 多边形是边数、星形是角数（同一个字段 `sides`）；只有星形的选区叫「角数」
          <Row label={sk(allStar ? 'points' : 'sides')} labelWidth={INSPECTOR_LABEL_W}>
            <NumberField
              half
              ariaLabel={sk(allStar ? 'points' : 'sides')}
              value={
                shared(objs, (o) => (o as ShapeObject).sides ?? ((o as ShapeObject).shape === 'star' ? DEFAULT_STAR_POINTS : 6)) ??
                (allStar ? DEFAULT_STAR_POINTS : 6)
              }
              step={1}
              min={3}
              max={12}
              onChange={(v) =>
                patch(hist('setSides'), (o) => {
                  if (markSidesCapable(o)) o.sides = Math.round(v)
                })
              }
            />
          </Row>
        )}
        {allStar && (
          // 星形的内径比（ADR 0185 M1）：百分数，等于缺省就不写字段
          <Row label={<span title={sk('starRatioTip')}>{sk('starRatio')}</span>} labelWidth={INSPECTOR_LABEL_W}>
            <NumberField
              half
              ariaLabel={sk('starRatio')}
              value={Math.round((shared(objs, (o) => (o as ShapeObject).starRatio ?? DEFAULT_STAR_RATIO) ?? DEFAULT_STAR_RATIO) * 100)}
              mixed={shared(objs, (o) => (o as ShapeObject).starRatio ?? DEFAULT_STAR_RATIO) === undefined}
              step={1}
              min={5}
              max={95}
              precision={0}
              unit="%"
              onChange={(v) =>
                patch(hist('setStarRatio'), (o) => {
                  if (!markStarCapable(o)) return
                  const r = Math.max(0.05, Math.min(0.95, v / 100))
                  if (Math.abs(r - DEFAULT_STAR_RATIO) < 1e-9) delete o.starRatio
                  else o.starRatio = r
                })
              }
            />
          </Row>
        )}
    </AppearanceSection>
    {(showStroke || filledPaths.length > 0 || radialOn) && (
      // 低频描边项收进一行「更多」：描边不透明度、线端、拐角（ADR 0174）
      <MoreFold
        changed={countChanged([
          headed.some((o) => o.headSizeStart != null || o.headSizeEnd != null),
          objs.some((o) => o.strokeOpacity != null),
          objs.some((o) => o.lineCap != null && o.lineCap !== DEFAULT_LINE_CAP),
          objs.some((o) => o.lineJoin != null && o.lineJoin !== markDefaultJoin(o)),
          objs.some((o) => o.miterLimit != null),
          objs.some((o) => o.dashPattern != null),
          objs.some((o) => (o.type === 'shape' || o.type === 'path') && o.strokeAlign != null),
          objs.some((o) => o.type === 'path' && o.fillRule != null),
        ])}
      >
        {/* 径向渐变的中心与半径（ADR 0186）；色标与角度在色带那里 */}
        <RadialRows objs={fillables} target="fill" />
        <RadialRows objs={stroked} target="stroke" />
        {showStroke && headed.length > 0 && <HeadSizeRows objs={headed} patch={patchHeaded} />}
        {showStroke && aligned.length > 0 && (
          // 描边对齐（封闭形状缺省内侧、外沿贴框；全闭合的路径缺省居中，ADR 0185 M2）：内侧 / 居中 / 外侧
          <ChoiceRow
            label={sk('alignRow')}
            group="align"
            values={ALIGN_VALUES}
            value={shared(aligned, (o) => strokeAlignOf(o as ShapeObject | PathObject)) ?? null}
            onChange={setAlign}
          />
        )}
        {filledPaths.length > 0 && (
          // 填充规则（自由路径）：非零 / 奇偶（多条子路径叠在一起时，奇偶让内圈成为洞）
          <ChoiceRow
            label={sk('fillRuleRow')}
            group="fillRule"
            values={['nonzero', 'evenodd'] as const}
            value={shared(filledPaths, (o) => (o as PathObject).fillRule ?? 'nonzero') ?? null}
            onChange={(v) =>
              patchPaths(hist('setFillRule'), (o) => {
                if (v === 'evenodd') o.fillRule = 'evenodd'
                else delete o.fillRule
              })
            }
          />
        )}
        {showStroke && (
        <>
        <StrokeOpacityRow
          value={shared(objs, (o) => (o as FillStrokeMark).strokeOpacity ?? 1)}
          onChange={(v) => patch(hist('setStrokeOpacity'), (o) => setOpacity(o, v))}
        />
        <LineEndRows
          cap={shared(objs, (o) => (o as FillStrokeMark).lineCap ?? DEFAULT_LINE_CAP) ?? null}
          join={shared(objs, (o) => (o as FillStrokeMark).lineJoin ?? markDefaultJoin(o as FillStrokeMark)) ?? null}
          dotted={dash === 'dotted'}
          hideJoin={allLine}
          onCap={(v) => patch(hist('setLineCap'), (o) => setCap(o, v))}
          onJoin={(v) => patch(hist('setLineJoin'), (o) => setJoin(o, v, markDefaultJoin(o)))}
        />
        {objs.some((o) => (o.lineJoin ?? markDefaultJoin(o)) === 'miter' && !markHasNoJoin(o)) && (
          <MiterLimitRow
            value={shared(objs, (o) => (o as FillStrokeMark).miterLimit ?? STROKE_MITER_LIMIT)}
            onChange={(v) => patch(hist('setMiterLimit'), (o) => setMiter(o, v))}
          />
        )}
        <DashPatternRow
          value={shared(objs, (o) => (o as FillStrokeMark).dashPattern ?? null)}
          onChange={(v) => patch(hist('setDashPattern'), (o) => setDashPattern(o, v))}
        />
        </>
        )}
      </MoreFold>
    )}
    </>
  )
}
