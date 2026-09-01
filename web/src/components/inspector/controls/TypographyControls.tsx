import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Bold, Italic } from 'lucide-react'
import { t as translate } from '@/i18n'
import { cn } from '@/lib/utils'
import {
  displayValueOf,
  type TypographyProp,
  type TypographyValue,
} from '@/lib/typography'
import type { TypographyAdapter } from '../typographyAdapter'
import { optionLabel } from '../roles/registry'
import {
  AlignmentRow,
  FontFamilyRow,
  FontSizeRow,
  StyleToggle,
  TextColorRow,
} from './textRows'

/**
 * 排版控件：**一份实现，图内文字与画布文字共用，单选与多选共用**。
 *
 * 这是 ADR 0032 的落点。控件看不到「这是标题还是标注」「目标是一个还是
 * 三个」——两件事都由 `TypographyAdapter` 吸收，于是「标注面板没有字体这
 * 一行」「多选后 B/I 退化成文字下拉」这类分叉在结构上不可能再出现。
 *
 * 能力仍由适配器说了算：`adapter.fieldOf(prop)` 回 undefined 就整条不画
 * ——绝不摆一个「点了不生效」的控件。
 *
 * **每一行都挂 `data-prop`**：那是问题面板「定位到具体字段」的落点
 * （`lib/issueFocus.ts`）。工具条把这几条属性从平铺列表里拿走时，锚点必须
 * 一起带过来——否则定位会安静地什么都不做，而界面还宣称定位成功了。
 */

const tb = (key: string, values?: Record<string, unknown>) =>
  translate(`textBar.${key}`, { ns: 'inspector', ...(values ?? {}) })

export function TypographyControls({
  adapter,
  className,
  labelWidth = 72,
}: {
  adapter: TypographyAdapter
  className?: string
  /** 标签列宽：属性页 72（与 FieldRow 对齐），快捷编辑弹层可传 44 */
  labelWidth?: number
}) {
  useTranslation('inspector')
  const family = adapter.fieldOf('fontFamily')
  const size = adapter.fieldOf('sizePt')
  const weight = adapter.fieldOf('weight')
  const style = adapter.fieldOf('style')
  const color = adapter.fieldOf('color')
  const align = adapter.fieldOf('halign')

  const familyVal = adapter.valueOf('fontFamily')
  const sizeVal = adapter.valueOf('sizePt')
  const colorVal = adapter.valueOf('color')
  const alignVal = adapter.valueOf('halign')
  const boldState = toggleStateOf(adapter.valueOf('weight'), 'bold')
  const italicState = toggleStateOf(adapter.valueOf('style'), 'italic')

  const dirty = (prop: TypographyProp) => adapter.overrideStateOf(prop) !== 'none'
  const reset = (prop: TypographyProp) => () => adapter.reset(prop)

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {family && (
        <Anchor adapter={adapter} prop="fontFamily">
          <FontFamilyRow
            labelWidth={labelWidth}
            value={String(displayValueOf(familyVal) ?? '')}
            mixed={familyVal.kind === 'mixed'}
            options={family.options ?? []}
            unavailable={adapter.unavailableOptions('fontFamily')}
            onChange={(v) => adapter.writeOnce('fontFamily', v)}
            optionLabelOf={(o) => optionLabel('fontfamily', o)}
            overridden={dirty('fontFamily')}
            onReset={reset('fontFamily')}
          />
        </Anchor>
      )}
      {size && (
        <Anchor adapter={adapter} prop="sizePt">
          <FontSizeRow
            labelWidth={labelWidth}
            // mixed 时 NumberField 留空 + 占位符；**绝不退回 9 pt 那种默认值**
            value={sizeVal.kind === 'mixed' ? NaN : Number(displayValueOf(sizeVal) ?? 9)}
            mixed={sizeVal.kind === 'mixed'}
            min={size.min}
            max={size.max}
            step={size.step ?? 0.5}
            suffix={size.unit}
            onChange={(v) => adapter.write('sizePt', v)}
            onScrubStart={adapter.beginGesture}
            onScrubEnd={adapter.endGesture}
            overridden={dirty('sizePt')}
            onReset={reset('sizePt')}
          >
            {/* B / I 各挂各的锚点：`text-weight-policy` 报的 property path 是
                `weight`，压在字号那一格的锚点里的话定位会落到数字框上。
                包一层用 `display:contents`，按钮仍是这一行的直接 flex 项，
                版面一个像素不变。 */}
            {weight && (
              <Anchor adapter={adapter} prop="weight" inline>
                <StyleToggle
                  state={boldState}
                  label={tb('bold')}
                  hint={
                    boldState === 'mixed'
                      ? tb('boldWeight', { value: translate('element.mixedValues', { ns: 'inspector' }) })
                      : tb('boldWeight', { value: tb(boldState === 'on' ? 'weightBold' : 'weightNormal') })
                  }
                  onClick={() =>
                    adapter.writeOnce('weight', nextToggle(adapter.valueOf('weight'), 'bold', 'normal'))
                  }
                >
                  <Bold size={12} />
                </StyleToggle>
              </Anchor>
            )}
            {style && (
              <Anchor adapter={adapter} prop="style" inline>
                <StyleToggle
                  state={italicState}
                  label={tb('italic')}
                  hint={
                    italicState === 'mixed'
                      ? tb('italicStyle', { value: translate('element.mixedValues', { ns: 'inspector' }) })
                      : tb('italicStyle', { value: tb(italicState === 'on' ? 'styleItalic' : 'styleNormal') })
                  }
                  onClick={() =>
                    adapter.writeOnce('style', nextToggle(adapter.valueOf('style'), 'italic', 'normal'))
                  }
                >
                  <Italic size={12} />
                </StyleToggle>
              </Anchor>
            )}
          </FontSizeRow>
        </Anchor>
      )}
      {color && (
        <Anchor adapter={adapter} prop="color">
          <TextColorRow
            labelWidth={labelWidth}
            // 多选不一致时色块取第一个目标的真实颜色 + 旁边明写「多个值」，
            // 不像旧批量行那样谎报一个谁都不是的 #000000
            value={String(displayValueOf(colorVal) ?? firstColor(adapter))}
            mixed={colorVal.kind === 'mixed'}
            onChange={(v) => adapter.write('color', v, true)}
            onGestureEnd={adapter.endGesture}
            overridden={dirty('color')}
            onReset={reset('color')}
          />
        </Anchor>
      )}
      {align && (
        <Anchor adapter={adapter} prop="halign">
          <AlignmentRow
            labelWidth={labelWidth}
            value={
              alignVal.kind === 'mixed'
                ? null
                : ((displayValueOf(alignVal) ?? 'center') as 'left' | 'center' | 'right')
            }
            onChange={(v) => adapter.writeOnce('halign', v)}
            labels={{ left: tb('alignLeft'), center: tb('alignCenter'), right: tb('alignRight') }}
            overridden={dirty('halign')}
            onReset={reset('halign')}
          />
        </Anchor>
      )}
    </div>
  )
}

/**
 * 定位锚点。名字取自 `lib/typography.propertyPathOf()`——**检查报的字段名、
 * 这里的锚点、问题面板查的选择器是同一份表**，各写各的字符串时缺的那一处
 * 表现为「点了定位什么都没发生」。
 */
function Anchor({
  adapter,
  prop,
  children,
  inline,
}: {
  adapter: TypographyAdapter
  prop: TypographyProp
  children: ReactNode
  /** 行内锚点：用 `display:contents` 挂，不参与版面（B / I 仍是同一行的 flex 项） */
  inline?: boolean
}) {
  const path = adapter.pathOf(prop)
  if (!path) return <>{children}</>
  return inline ? (
    <span className="contents" data-prop={path}>
      {children}
    </span>
  ) : (
    <div data-prop={path}>{children}</div>
  )
}

/**
 * 三态开关（B / I）的下一个值。
 *
 * mixed 点一次 = 全开（先把它们对齐，再想要不要关），全开点一次 = 全关，
 * 全关点一次 = 全开。**没有「点一次回到 mixed」**——mixed 不是用户能选的
 * 目标状态，它只是当前事实的描述。
 */
export function nextToggle(state: TypographyValue, onValue: string, offValue: string): string {
  return displayValueOf(state) === onValue ? offValue : onValue
}

/** 三态开关当前该画成什么样。`inherit`（没设过）与 `uniform` 一样看生效值。 */
export function toggleStateOf(state: TypographyValue, onValue: string): 'on' | 'off' | 'mixed' {
  if (state.kind === 'mixed') return 'mixed'
  return displayValueOf(state) === onValue ? 'on' : 'off'
}

/** mixed 时色块显示的那个值：取第一个目标的真实颜色（不是硬编码黑） */
function firstColor(adapter: TypographyAdapter): string {
  const v = adapter.fieldOf('color')?.value
  return typeof v === 'string' ? v : '#000000'
}
