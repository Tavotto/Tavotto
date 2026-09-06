import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/Button'
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
  return (
    <Button variant="outline" size="sm" className="w-full" data-effect-add onClick={onAdd}>
      <Plus size={11} aria-hidden />
      {addLabel}
    </Button>
  )
}
