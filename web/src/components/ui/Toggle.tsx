import { cn } from '@/lib/utils'

export function Toggle({
  checked,
  onChange,
  disabled,
  id,
  'aria-label': ariaLabel,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  /**
   * 给了 id 就能被一个真正的 `<label htmlFor>` 指着——点标签文字等于点开关，
   * 可达名也就是那行标签本身。**这时不要再传 `aria-label`**：它会盖掉标签，
   * 于是屏幕阅读器念的名字和用户看见的那个词分叉（审计 T39）。
   */
  id?: string
  'aria-label'?: string
}) {
  return (
    <button
      role="switch"
      id={id}
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      // 视觉轨道 14px，点击区拉到 28px 高，符合最小可点面积
      className="group flex h-7 shrink-0 items-center rounded-sm px-0.5 outline-none focus-visible:focus-ring disabled:opacity-40"
    >
      <span
        className={cn(
          'relative h-[14px] w-[24px] rounded-full transition-colors',
          checked ? 'bg-accent' : 'bg-border-strong',
        )}
      >
        <span
          className={cn(
            'absolute top-[2px] h-[10px] w-[10px] rounded-full bg-white transition-[left]',
            checked ? 'left-[12px]' : 'left-[2px]',
          )}
        />
      </span>
    </button>
  )
}
