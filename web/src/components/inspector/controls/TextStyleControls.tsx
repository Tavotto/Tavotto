import { useTranslation } from 'react-i18next'
import { Bold, Italic } from 'lucide-react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import type { TextStyleAdapter } from '../textStyleAdapter'
import { nextToggleValue, toggleStateOf } from '../textStyleModel'
import { optionLabel } from '../roles/registry'
import {
  AlignmentRow,
  FontFamilyRow,
  FontSizeRow,
  StyleToggle,
  TextColorRow,
} from './textRows'

/**
 * 图内文字的高频样式控件：**一份实现，单选与多选共用**。
 *
 * 这是本轮「同一属性同一视觉语言」的落点——`weight` 永远是 B 图标、
 * `style` 永远是 I 图标，不会因为多选了第二个对象就退化成
 * `常规 / 加粗` 的文字下拉。控件看不到目标是一个还是三个：那件事由
 * `TextStyleAdapter` 吸收（`useTextStyleAdapter(panel, elements)`）。
 *
 * 能力仍由 manifest 说了算：`adapter.fieldOf(prop)` 回 undefined 就整条不画
 * ——交集里没有的属性绝不摆一个「点了不生效」的控件。
 */

const tb = (key: string, values?: Record<string, unknown>) =>
  translate(`textBar.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function TextStyleControls({
  adapter,
  className,
  labelWidth = 72,
}: {
  adapter: TextStyleAdapter
  className?: string
  /** 标签列宽：属性页 72（与 FieldRow 对齐），快捷编辑弹层可传 44 */
  labelWidth?: number
}) {
  useTranslation('inspector')
  const family = adapter.fieldOf('fontfamily')
  const size = adapter.fieldOf('fontsize')
  const weight = adapter.fieldOf('weight')
  const style = adapter.fieldOf('style')
  const color = adapter.fieldOf('color')
  const align = adapter.fieldOf('ha')

  const familyVal = adapter.valueOf('fontfamily')
  const sizeVal = adapter.valueOf('fontsize')
  const colorVal = adapter.valueOf('color')
  const alignVal = adapter.valueOf('ha')
  const boldState = toggleStateOf(adapter.valueOf('weight'), 'bold')
  const italicState = toggleStateOf(adapter.valueOf('style'), 'italic')

  const dirty = (prop: string) => adapter.overrideStateOf(prop) !== 'none'
  const reset = (prop: string) => () => adapter.reset(prop)

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {family && (
        <FontFamilyRow
          labelWidth={labelWidth}
          value={familyVal.kind === 'uniform' ? String(familyVal.value ?? '') : ''}
          mixed={familyVal.kind === 'mixed'}
          options={family.options ?? []}
          onChange={(v) => adapter.writeOnce('fontfamily', v)}
          optionLabelOf={(o) => optionLabel('fontfamily', o)}
          overridden={dirty('fontfamily')}
          onReset={reset('fontfamily')}
        />
      )}
      {size && (
        <FontSizeRow
          labelWidth={labelWidth}
          // mixed 时 NumberField 留空 + 占位符；**绝不退回 9 pt 那种默认值**
          value={sizeVal.kind === 'uniform' ? Number(sizeVal.value ?? 9) : NaN}
          mixed={sizeVal.kind === 'mixed'}
          min={size.min}
          max={size.max}
          step={size.step ?? 0.5}
          suffix={size.unit}
          onChange={(v) => adapter.write('fontsize', v)}
          onScrubStart={adapter.beginGesture}
          onScrubEnd={adapter.endGesture}
          overridden={dirty('fontsize')}
          onReset={reset('fontsize')}
        >
          {weight && (
            <StyleToggle
              state={boldState}
              label={tb('bold')}
              hint={
                boldState === 'mixed'
                  ? tb('boldWeight', { value: translate('element.mixedValues', { ns: 'inspector' }) })
                  : tb('boldWeight', { value: tb(boldState === 'on' ? 'weightBold' : 'weightNormal') })
              }
              onClick={() =>
                adapter.writeOnce(
                  'weight',
                  nextToggleValue(adapter.valueOf('weight'), 'bold', 'normal'),
                )
              }
            >
              <Bold size={12} />
            </StyleToggle>
          )}
          {style && (
            <StyleToggle
              state={italicState}
              label={tb('italic')}
              hint={
                italicState === 'mixed'
                  ? tb('italicStyle', { value: translate('element.mixedValues', { ns: 'inspector' }) })
                  : tb('italicStyle', { value: tb(italicState === 'on' ? 'styleItalic' : 'styleNormal') })
              }
              onClick={() =>
                adapter.writeOnce(
                  'style',
                  nextToggleValue(adapter.valueOf('style'), 'italic', 'normal'),
                )
              }
            >
              <Italic size={12} />
            </StyleToggle>
          )}
        </FontSizeRow>
      )}
      {color && (
        <TextColorRow
          labelWidth={labelWidth}
          // 多选不一致时色块取第一个目标的真实颜色 + 旁边明写「多个值」，
          // 不像旧批量行那样谎报一个谁都不是的 #000000
          value={colorVal.kind === 'uniform' ? String(colorVal.value ?? '#000000') : firstColor(adapter)}
          mixed={colorVal.kind === 'mixed'}
          onChange={(v) => adapter.write('color', v, true)}
          onGestureEnd={adapter.endGesture}
          overridden={dirty('color')}
          onReset={reset('color')}
        />
      )}
      {/* 对齐只在单选时出现：`ha` 没进 TEXT_STYLE_PROPS，批量适配器结构上
          就拿不到它（同一个 left 在图标题与 Y 轴标题上语义不同） */}
      {align && (
        <AlignmentRow
          labelWidth={labelWidth}
          value={
            alignVal.kind === 'uniform'
              ? (String(alignVal.value ?? 'center') as 'left' | 'center' | 'right')
              : null
          }
          onChange={(v) => adapter.writeOnce('ha', v)}
          labels={{ left: tb('alignLeft'), center: tb('alignCenter'), right: tb('alignRight') }}
          overridden={dirty('ha')}
          onReset={reset('ha')}
        />
      )}
    </div>
  )
}

/** mixed 时色块显示的那个值：取第一个目标的真实颜色（不是硬编码黑） */
function firstColor(adapter: TextStyleAdapter): string {
  const f = adapter.fieldOf('color')
  const v = f?.value
  return typeof v === 'string' ? v : '#000000'
}
