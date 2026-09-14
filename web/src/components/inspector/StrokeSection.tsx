import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { updateObjects } from '@/store/actions'
import type { ArrowObject, DashStyle, ShapeObject } from '@/types/document'
import { arrowHeads, legacyHead } from '@/types/document'
import { Button } from '../ui/Button'
import { Row, Section } from '../ui/Field'
import { ColorField, NumberField } from '../ui/Input'
import { Popover } from '../ui/Popover'
import { EffectToggle } from './controls/EffectToggle'
import { OptionGrid, type GridOption } from './controls/OptionGrid'
import { PickerTrigger } from './controls/PickerTrigger'
import { shared } from './common'

/** 本组文案 inspector:stroke.*，历史标签 inspector:history.* */
const sk = (key: string) => translate(`stroke.${key}`, { ns: 'inspector' })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

const DASH_VALUES: DashStyle[] = ['solid', 'dashed', 'dotted']

type ArrowHead = ReturnType<typeof arrowHeads>['end']
const HEAD_VALUES: ArrowHead[] = ['none', 'open', 'triangle', 'bar']

/** 每种线型的形状示意：一段线，跟随文字色 */
function DashGlyph({ dash }: { dash: DashStyle }) {
  const dasharray = dash === 'dashed' ? '4 3' : dash === 'dotted' ? '0.5 3' : undefined
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
  const lineEnd = head === 'open' || head === 'triangle' ? 22 : 29
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
      width={216}
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
    <Row label={sk('dash')}>
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

  return (
    <AppearanceSection>
        {/* 线型放首行；端型排在线宽 / 颜色之前（审计 T28） */}
        <DashRow
          value={shared(objs, (o) => (o as ArrowObject).dash ?? 'solid') ?? null}
          onChange={(v) => patch(hist('setDash'), (o) => (o.dash = v === 'solid' ? undefined : v))}
        />
        <Row label={sk('end')}>
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
        <Row label={sk('start')}>
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
        {/* 线宽与颜色同一行：线宽占剩余宽度，颜色靠右 */}
        <Row label={sk('lineWidth')}>
          <div className="flex w-full items-center gap-1.5">
            <div className="min-w-0 flex-1">
              <NumberField
                value={shared(objs, (o) => (o as ArrowObject).strokePt) ?? 1}
                mixed={shared(objs, (o) => (o as ArrowObject).strokePt) === undefined}
                step={0.25}
                min={0.1}
                max={20}
                precision={2}
                unit="pt"
                onChange={(v) => patch(hist('setStrokeWidth'), (o) => (o.strokePt = v))}
              />
            </div>
            <div className="shrink-0">
              <ColorField
                ariaLabel={sk('color')}
                value={shared(objs, (o) => (o as ArrowObject).color) ?? '#1B1B18'}
                onChange={(v) => patch(hist('setArrowColor'), (o) => (o.color = v))}
              />
            </div>
          </div>
        </Row>
        {/* 整行按钮不走标签列：空标签会在左边留一块 44px 的白 */}
        <Button
          variant="secondary"
          size="sm"
          className="w-full"
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
    </AppearanceSection>
  )
}

export function ShapeSection({ objs }: { objs: ShapeObject[] }) {
  useTranslation('inspector')
  const ids = objs.map((o) => o.id)
  // line 只有描边；brace 只描边不填充
  const hasFillable = objs.some((o) => o.shape !== 'line' && o.shape !== 'brace')
  const allRect = objs.every((o) => o.shape === 'rect')
  const allPolygon = objs.every((o) => o.shape === 'polygon')
  const fill = shared(objs, (o) => (o as ShapeObject).fill)
  const patch = (label: UiMessage, fn: (o: ShapeObject) => void) =>
    updateObjects(ids, label, (o) => {
      if (o.type === 'shape') fn(o)
    })

  return (
    <AppearanceSection>
        {/*
          填充 → 描边 → 形状本身的参数（审计 T28：矩形突出填充 / 描边 / 圆角）。
          填充与画布文字的背景同一种模式：关着是「＋添加填充」，开了是真开关，
          不透明度跟在它下面——三处「添加式扩展」在产品里是同一个控件。
        */}
        {hasFillable && (
          <Row label={sk('fill')}>
            <EffectToggle
              on={!!fill}
              label={sk('fill')}
              addLabel={sk('addFill')}
              onAdd={() => patch(hist('addFill'), (o) => (o.fill = '#FFFFFF'))}
              onOff={() => patch(hist('clearFill'), (o) => (o.fill = null))}
            />
            {fill && (
              <ColorField
                ariaLabel={sk('fill')}
                value={fill}
                onChange={(v) => patch(hist('setFill'), (o) => (o.fill = v))}
              />
            )}
          </Row>
        )}
        <Row label={sk('strokeColor')}>
          <ColorField
            ariaLabel={sk('strokeColor')}
            value={shared(objs, (o) => (o as ShapeObject).color) ?? '#1B1B18'}
            onChange={(v) => patch(hist('setStrokeColor'), (o) => (o.color = v))}
          />
        </Row>
        {/* 线宽与填充不透明度同一行：两个输入框固定 40px（index.css 的 data-stroke-fields） */}
        <Row label={sk('lineWidth')}>
          <div data-stroke-fields className="flex w-full items-center gap-2">
            <NumberField
              value={shared(objs, (o) => (o as ShapeObject).strokePt) ?? 1}
              mixed={shared(objs, (o) => (o as ShapeObject).strokePt) === undefined}
              step={0.25}
              min={0.1}
              max={20}
              precision={2}
              unit="pt"
              onChange={(v) => patch(hist('setStrokeWidth'), (o) => (o.strokePt = v))}
            />
            {hasFillable && fill && (
              <label className="ml-auto flex min-w-0 items-center gap-1.5 text-xs text-ink-2">
                <span className="shrink-0">{sk('fillOpacity')}</span>
                <NumberField
                  value={Math.round(((shared(objs, (o) => (o as ShapeObject).fillOpacity ?? 1) ?? 1) as number) * 100)}
                  step={5}
                  min={0}
                  max={100}
                  unit="%"
                  onChange={(v) =>
                    patch(hist('setFillOpacity'), (o) => {
                      const f = Math.max(0, Math.min(1, v / 100))
                      if (f < 1) o.fillOpacity = f
                      else delete o.fillOpacity
                    })
                  }
                />
              </label>
            )}
          </div>
        </Row>
        <DashRow
          value={shared(objs, (o) => (o as ShapeObject).dash ?? 'solid') ?? null}
          onChange={(v) => patch(hist('setDash'), (o) => (o.dash = v === 'solid' ? undefined : v))}
        />
        {allRect && (
          <Row label={sk('cornerRadius')}>
            <NumberField
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
          <Row label={sk('sides')}>
            <NumberField
              value={shared(objs, (o) => (o as ShapeObject).sides ?? 6) ?? 6}
              step={1}
              min={3}
              max={12}
              onChange={(v) => patch(hist('setSides'), (o) => (o.sides = Math.round(v)))}
            />
          </Row>
        )}
    </AppearanceSection>
  )
}
