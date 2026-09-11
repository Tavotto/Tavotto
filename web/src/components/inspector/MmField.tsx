import type { ReactNode } from 'react'
import { useDocumentStore } from '@/store/documentStore'
import type { UiMessage } from '@/i18n'
import { cn } from '@/lib/utils'
import { NumberField } from '../ui/Input'

/**
 * mm 数值输入：`[ 393.7      mm ]`——单位坐在框里、靠右，数字右对齐；拖动改数时
 * 开事务，把连续修改合并成一条撤销记录。框撑满所在格（`fill`），所以放进
 * `GeometryGrid` 的两列里，X / Y 与 W / H 的数字和单位各排成一条竖线。
 */
export function MmField({
  label,
  value,
  onChange,
  step = 0.5,
  disabled,
  suffix = 'mm',
  historyLabel,
  min,
  title,
}: {
  label: string
  value: number | undefined
  onChange: (v: number) => void
  step?: number
  disabled?: boolean
  suffix?: string
  /** 落进撤销栈的标签（描述符：切语言后历史跟着换） */
  historyLabel: UiMessage
  min?: number
  title?: string
}) {
  return (
    <NumberField
      fill
      prefix={label}
      unit={suffix || undefined}
      value={value ?? 0}
      mixed={value === undefined}
      step={step}
      min={min}
      disabled={disabled}
      title={title}
      onChange={onChange}
      onScrubStart={() => useDocumentStore.getState().beginTxn(historyLabel)}
      onScrubEnd={() => useDocumentStore.getState().endTxn()}
    />
  )
}

/**
 * 位置与尺寸的稳定网格：两列字段中间夹一个 28px 的动作位（宽高比锁 / 横竖交换），
 * 第一行没有动作时放 `GeometrySpacer` 占位，两行的字段才对得齐。
 *
 *     X [ -92.2 mm ]      Y [ -110.8 mm ]
 *     W [ 393.7 mm ]  🔗  H [  259.1 mm ]
 *
 * 列宽是 minmax(0,1fr)：窄栏里字段一起收窄，不会把单位挤出框外。
 */
export function GeometryGrid({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-x-1.5 gap-y-1.5',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function GeometrySpacer() {
  return <span aria-hidden className="w-7" />
}
