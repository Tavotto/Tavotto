/**
 * 抽屉标题旁的计数：视觉上只有数字（type-meta），完整的「N 个元素」给读屏（与 title）。
 * 两份文本同时在 DOM 里，一份 aria-hidden、一份 sr-only——数字与单位在不同语言里
 * 的顺序不一样，拆不开 i18n 串，只能整句藏起来给辅助技术。
 *
 * 左抽屉（图层 / 图内元素）与右侧的文档版本抽屉共用这一份：两个抽屉此前各写一遍，
 * 一边是 DrawerCount、一边是手写的 `text-xs text-ink-3`（2026-09-15 左栏审计 L20）。
 */
export function DrawerCount({ value, label }: { value: number; label?: string }) {
  return (
    <span className="type-meta tabular-nums" title={label}>
      <span aria-hidden={label ? true : undefined}>{value}</span>
      {label && <span className="sr-only">{label}</span>}
    </span>
  )
}
