import type { ReactNode } from 'react'

/**
 * 抽屉 / 列表的空态（2026-10-16 UI 2.0 第二轮 09 方案 A）：**列表顶部一行 ink-3 灰字 + 至多一个文字动作**，
 * 不放大图标、不放标题、不居中。动作是文字按钮，字色是灰（ink-2 + 下划线）：蓝色只留给选中 / 焦点（用户验收后裁定，2026-10）。画布中央那种「主工作区的空」仍是 `EmptyState`。
 * `title` 放那句话之外的补充说明（原先的 hint）：悬停可读，不常驻。
 */
export function EmptyLine({
  children,
  action,
  title,
  ...rest
}: {
  children: ReactNode
  action?: { label: string; onClick: () => void }
  title?: string
} & Record<`data-${string}`, string | number | boolean | undefined>) {
  return (
    <p {...rest} data-empty-line title={title} className="px-3 py-2 text-xs text-ink-3">
      {children}
      {action && (
        <>
          {' · '}
          <button
            type="button"
            onClick={action.onClick}
            className="-mx-1 rounded-sm px-1 text-ink-2 underline underline-offset-2 outline-none hover:text-ink focus-visible:focus-ring"
          >
            {action.label}
          </button>
        </>
      )}
    </p>
  )
}
