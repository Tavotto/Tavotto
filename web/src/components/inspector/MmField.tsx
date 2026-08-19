import { useDocumentStore } from '@/store/documentStore'
import type { UiMessage } from '@/i18n'
import { NumberField } from '../ui/Input'

/** mm 数值输入：拖动改数时开事务，把连续修改合并成一条撤销记录。 */
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
      prefix={label}
      suffix={suffix}
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
