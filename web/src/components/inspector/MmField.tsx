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
    // 毫米值常见「-12.3」「393.7」这种五六位，通用的 4 字符框放不下；这里把输入框
    // 放到 6 个字符 + 内边距，单位「mm」由 NumberField 自己贴在框后，不另套固定宽度的壳
    // （之前套的 96px 壳把单位推到壳外，标签、框、单位三者之间留着一大段空白）。
    <NumberField
      className="[&_input]:w-[calc(6ch+0.75rem)]"
      prefix={label}
      suffix={suffix || undefined}
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
