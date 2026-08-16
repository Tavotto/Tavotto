import { cn } from '@/lib/utils'

export function Toggle({
  checked,
  onChange,
  disabled,
  'aria-label': ariaLabel,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  'aria-label'?: string
}) {
  return (
    <button
      role="switch"
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
