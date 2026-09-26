import { Bold, Italic } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { fontStackOf } from '@/components/inspector/controls/fontStack'
import { StyleToggle } from '@/components/inspector/controls/textRows'
import { optionLabel } from '@/components/inspector/roles/registry'
import type { TypographyAdapter } from '@/components/inspector/typographyAdapter'
import { ColorField, NumberField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { FALLBACK_MIN_FONT_SIZE_PT } from '@/lib/profile'
import { PT_DECIMALS } from '@/lib/stylePresets'
import { displayValueOf, nextToggle, toggleStateOf } from '@/lib/typography'
import { cn } from '@/lib/utils'
import { Sep } from './shared'

/**
 * 浮动栏上的文字排版控件本体：字体 / 字号 / 加粗 / 斜体 / 颜色。图内文字与画布文字、
 * 单选与多选都画这一份（数据来自各自的适配器，控件看不到差别）。
 */
export function TextQuickControls({
  a,
  compact,
  familyWidth,
  sizeFallback = FALLBACK_MIN_FONT_SIZE_PT,
}: {
  a: TypographyAdapter
  compact: boolean
  familyWidth: string
  /** 读不到字号时的显示回退（画布文字的默认字号与图内不同） */
  sizeFallback?: number
}) {
  const family = a.fieldOf('fontFamily')
  const size = a.fieldOf('sizePt')
  const sizeVal = a.valueOf('sizePt')
  const colorVal = a.valueOf('color')
  const boldState = toggleStateOf(a.valueOf('weight'), 'bold')
  const italicState = toggleStateOf(a.valueOf('style'), 'italic')
  const firstColor = a.fieldOf('color')?.value
  return (
    <span className="flex items-center gap-1" data-text-quick={compact ? 'compact' : 'full'}>
      {!compact && family && (family.options?.length ?? 0) > 0 && (
        <Select
          className={cn(familyWidth, 'shrink-0')}
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
          fill
          className="w-[68px] shrink-0"
          // mixed 时留空 + 占位符，绝不退回一个默认字号（与属性页 TypographyControls 同一条）
          value={sizeVal.kind === 'mixed' ? NaN : Number(displayValueOf(sizeVal) ?? sizeFallback)}
          mixed={sizeVal.kind === 'mixed'}
          min={size.min}
          max={size.max}
          step={size.step ?? 0.5}
          precision={PT_DECIMALS}
          unit={size.unit}
          title={translate('textControls.size', { ns: 'inspector' })}
          onChange={(v) => a.write('sizePt', v)}
          onScrubStart={a.beginGesture}
          onScrubEnd={a.endGesture}
        />
      )}
      {(a.fieldOf('weight') || a.fieldOf('style')) && (
        <span className="flex items-center gap-0.5">
          {a.fieldOf('weight') && (
            <StyleToggle
              state={boldState}
              label={translate('textBar.bold', { ns: 'inspector' })}
              onClick={() => a.writeOnce('weight', nextToggle(a.valueOf('weight'), 'bold', 'normal'))}
            >
              <Bold size={ICON_SIZE.sm} />
            </StyleToggle>
          )}
          {a.fieldOf('style') && (
            <StyleToggle
              state={italicState}
              label={translate('textBar.italic', { ns: 'inspector' })}
              onClick={() => a.writeOnce('style', nextToggle(a.valueOf('style'), 'italic', 'normal'))}
            >
              <Italic size={ICON_SIZE.sm} />
            </StyleToggle>
          )}
        </span>
      )}
      {!compact && a.fieldOf('color') && (
        /* 取色是连续动作：一轮拖动经适配器的手势压成一条历史，失焦 / 安静计时收尾 */
        <ColorField
          ariaLabel={translate('textBar.color', { ns: 'inspector' })}
          value={String(
            displayValueOf(colorVal) ?? (typeof firstColor === 'string' ? firstColor : '#000000'),
          )}
          onChange={(v) => a.write('color', v, true)}
          onGestureEnd={a.endGesture}
        />
      )}
      <Sep />
    </span>
  )
}
