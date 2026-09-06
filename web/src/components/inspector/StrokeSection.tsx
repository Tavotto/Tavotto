import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { updateObjects } from '@/store/actions'
import type { ArrowObject, DashStyle, ShapeObject } from '@/types/document'
import { arrowHeads, legacyHead } from '@/types/document'
import { Button } from '../ui/Button'
import { Row, Section } from '../ui/Field'
import { ColorField, NumberField } from '../ui/Input'
import { ArrowHeadPicker } from './controls/ArrowPickers'
import { EffectToggle } from './controls/EffectToggle'
import { LineStylePicker } from './controls/LineStylePicker'
import { shared } from './common'

/** 本组文案 inspector:stroke.*，历史标签 inspector:history.* */
const sk = (key: string) => translate(`stroke.${key}`, { ns: 'inspector' })
const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

const DASH_VALUES: DashStyle[] = ['solid', 'dashed', 'dotted']

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

/** 与图内元素同一个线型选择器：真实线段预览 + 画布自己的显示名（§16） */
function DashRow({
  value,
  onChange,
}: {
  value: DashStyle | null
  onChange: (v: DashStyle) => void
}) {
  return (
    <Row label={sk('dash')}>
      <LineStylePicker
        value={value ?? 'solid'}
        options={DASH_VALUES}
        onChange={(v) => onChange(v as DashStyle)}
        ariaLabel={sk('dash')}
        labelOf={(v) => sk(`dashStyle.${v}`)}
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
        {/* 一条箭头最先要定的是它指向谁：端型排在线宽 / 颜色之前（审计 T28） */}
        <Row label={sk('end')}>
          <ArrowHeadPicker
            value={shared(objs, (o) => arrowHeads(o as ArrowObject).end) ?? null}
            at="end"
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
          <ArrowHeadPicker
            value={shared(objs, (o) => arrowHeads(o as ArrowObject).start) ?? null}
            at="start"
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
        <Row label={sk('color')}>
          <ColorField
            value={shared(objs, (o) => (o as ArrowObject).color) ?? '#1B1B18'}
            onChange={(v) => patch(hist('setArrowColor'), (o) => (o.color = v))}
          />
        </Row>
        <Row label={sk('lineWidth')}>
          <NumberField
            value={shared(objs, (o) => (o as ArrowObject).strokePt) ?? 1}
            mixed={shared(objs, (o) => (o as ArrowObject).strokePt) === undefined}
            step={0.25}
            min={0.1}
            max={20}
            precision={2}
            suffix="pt"
            onChange={(v) => patch(hist('setStrokeWidth'), (o) => (o.strokePt = v))}
          />
        </Row>
        <DashRow
          value={shared(objs, (o) => (o as ArrowObject).dash ?? 'solid') ?? null}
          onChange={(v) => patch(hist('setDash'), (o) => (o.dash = v === 'solid' ? undefined : v))}
        />
        {/* 整行按钮不走标签列：空标签会在左边留一块 44px 的白 */}
        <Button
          variant="outline"
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
                value={fill}
                onChange={(v) => patch(hist('setFill'), (o) => (o.fill = v))}
              />
            )}
          </Row>
        )}
        {hasFillable && fill && (
          <Row label={sk('fillOpacity')}>
            <NumberField
              value={Math.round(((shared(objs, (o) => (o as ShapeObject).fillOpacity ?? 1) ?? 1) as number) * 100)}
              step={5}
              min={0}
              max={100}
              suffix="%"
              onChange={(v) =>
                patch(hist('setFillOpacity'), (o) => {
                  const f = Math.max(0, Math.min(1, v / 100))
                  if (f < 1) o.fillOpacity = f
                  else delete o.fillOpacity
                })
              }
            />
          </Row>
        )}
        <Row label={sk('strokeColor')}>
          <ColorField
            value={shared(objs, (o) => (o as ShapeObject).color) ?? '#1B1B18'}
            onChange={(v) => patch(hist('setStrokeColor'), (o) => (o.color = v))}
          />
        </Row>
        <Row label={sk('lineWidth')}>
          <NumberField
            value={shared(objs, (o) => (o as ShapeObject).strokePt) ?? 1}
            mixed={shared(objs, (o) => (o as ShapeObject).strokePt) === undefined}
            step={0.25}
            min={0.1}
            max={20}
            precision={2}
            suffix="pt"
            onChange={(v) => patch(hist('setStrokeWidth'), (o) => (o.strokePt = v))}
          />
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
              suffix="mm"
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
