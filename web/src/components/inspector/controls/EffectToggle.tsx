import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { ICON_SIZE } from '@/components/ui/Icon'
import { t as translate } from '@/i18n'
import { Toggle } from '@/components/ui/Toggle'

/**
 * 图内文字效果（背景 / 描边）的开关控件。
 *
 * 关着的时候画成一条「＋添加背景」入口，开了才是真开关——与画布文字
 * `TextSection` 的「添加背景 / 添加描边」同一种操作模式（审计 T14 要求把画布
 * 那侧的模式推广到图内文字，而不是反过来）。从属参数（颜色 / 透明度 / 边框…）
 * 由展示注册表按开关状态收放，这里不管它们。
 *
 * 关掉走 `onOff`（不是普通的 `writeOnce(false)`）：调用方要把从属字段的
 * override 一并清掉，否则「用户改过的必须能看到」会把它们再摆回来。
 */
export function EffectToggle({
  on,
  label,
  addLabel,
  onAdd,
  onOff,
}: {
  on: boolean
  /** 开关的可达名（属性显示名） */
  label: string
  /** 关着时那条入口上的字（「添加背景」） */
  addLabel: string
  onAdd: () => void
  onOff: () => void
}) {
  if (on) {
    return (
      <Toggle
        checked
        aria-label={label}
        onChange={(v) => {
          if (!v) onOff()
        }}
      />
    )
  }
  // 关着：一个行内值「无」+ 一颗 ghost「添加背景」（二审 A7）。此前是整行宽的 secondary 按钮，
  // 是检查器里唯一一颗整行宽的钮，视觉分量高过它上面的所有属性行；低频入口不该更重
  return (
    <span className="flex min-w-0 items-center gap-1">
      <span className="type-control text-ink-3">{translate('text.effectNone', { ns: 'inspector' })}</span>
      <Button variant="ghost" size="sm" className="text-ink-2 hover:text-ink" data-effect-add onClick={onAdd}>
        <Plus size={ICON_SIZE.xs} aria-hidden />
        {addLabel}
      </Button>
    </span>
  )
}
