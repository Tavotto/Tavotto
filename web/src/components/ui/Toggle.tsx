import { cn } from '@/lib/utils'

/**
 * 开关（`<button role="switch">`）。
 *
 * ## 名字是必填的，而且必须显式给
 *
 * 2026-09-07 之前 33 个调用点里只有 2 个给了名字，其余全靠「外面包了一个
 * `<label>` / 旁边有一行可见文字」——**那对 `<button>` 不成立**。HTML-AAM 给
 * `button` 的取名方式是「name from content」，而这颗按钮的内容只有两个装饰用的
 * `<span>`；`<label>` 的关联只保证点文字能切换，不保证读屏念得出名字。
 *
 * chromium 大方地把 `<label>` 的文字算进了可达名，所以本机与 posix 腿一直是绿的；
 * webkit（Windows 腿，#299）按规范办事，axe 当场报 `button-name` critical。
 * **引擎不同，能看见的维度也不同**——「chromium 绿」不等于「没有这个缺陷」。
 *
 * 两种给法二选一，类型上强制：
 *   * `aria-labelledby` —— 行首那句可见文字有 id 时用它，名字与看见的字**同一份**，
 *     不会分叉（审计 T39 担心的正是分叉）；
 *   * `aria-label` —— 没有可见文字，或那句文字不是纯字符串时用它。**要传就传渲染
 *     那句可见文字的同一个表达式**，别另写一句同义的。
 */
type ToggleProps = {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  /**
   * 给了 id 就能被一个 `<label htmlFor>` 指着——点标签文字等于点开关。
   * 但它**不负责取名**（见上），名字仍要由下面两个属性之一给出。
   */
  id?: string
} & (
  | { 'aria-label': string; 'aria-labelledby'?: never }
  | { 'aria-labelledby': string; 'aria-label'?: never }
)

export function Toggle({
  checked,
  onChange,
  disabled,
  id,
  'aria-label': ariaLabel,
  'aria-labelledby': ariaLabelledBy,
}: ToggleProps) {
  return (
    <button
      role="switch"
      id={id}
      aria-checked={checked}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
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
