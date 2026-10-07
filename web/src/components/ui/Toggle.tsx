import { useId } from 'react'
import { t } from '@/i18n'
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
   * 多选且取值不一致（2026-10-07 审计 §9.2 P0）：`aria-checked="mixed"`，滑块停在轨道正中、
   * 带一道短横——既不是开也不是关。之前批量行画成「关」再在旁边补一句「多个值」，
   * 控件本身在谎报。点一下把全部设为开（`onChange(true)`）。
   * ARIA 1.2 里 switch 的 mixed 会被部分读屏当成 false，所以另用 `aria-describedby`
   * 把「多个值」念出来，名字照旧由下面两个属性给。
   */
  mixed?: boolean
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
  mixed,
  id,
  'aria-label': ariaLabel,
  'aria-labelledby': ariaLabelledBy,
}: ToggleProps) {
  const mixedId = useId()
  return (
    <button
      role="switch"
      id={id}
      aria-checked={mixed ? 'mixed' : checked}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      aria-describedby={mixed ? mixedId : undefined}
      data-mixed={mixed || undefined}
      disabled={disabled}
      onClick={() => onChange(mixed ? true : !checked)}
      // 视觉轨道 16px（28×16，thumb 12；2026-09-15 全面审计 B03：24×14 在 48px 设置行里像角标，
      // OpenAI 32×19、Claude 30×18），点击区拉到 28px 高，符合最小可点面积
      className="group flex h-7 shrink-0 items-center rounded-sm px-0.5 outline-none focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-40"
    >
      <span
        className={cn(
          'relative h-4 w-7 rounded-full transition-colors duration-base',
          // 关态轨道：border-control（对白 3.48:1）。border-strong 的 1.57:1 让关着的开关在 48px 设置行里几乎看不见
          mixed || checked ? 'bg-ink' : 'bg-border-control',
        )}
      >
        {/* 滑块动 transform 不动 left：布局属性每帧重排（二审 E4）；开关的来回用对称曲线。
            白钮带 shadow-thumb（与分段选择器的 thumb 同一份）：两家的 thumb 都有 0 1px 2px 的投影，白钮才浮得起来 */}
        <span
          className={cn(
            'absolute left-0.5 top-0.5 flex h-3 w-3 items-center justify-center rounded-full bg-surface shadow-thumb transition-transform duration-base',
            // mixed：停在正中（开 / 关两端的中点），里面一道短横
            mixed ? 'translate-x-1.5' : checked ? 'translate-x-3' : 'translate-x-0',
          )}
        >
          {mixed && <span className="h-0.5 w-1.5 rounded-full bg-ink" />}
        </span>
      </span>
      {mixed && (
        <span id={mixedId} className="sr-only">
          {t('mixed')}
        </span>
      )}
    </button>
  )
}
