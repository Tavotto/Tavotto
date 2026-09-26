import type { LayoutVersionKind, LayoutVersionMeta } from '@/lib/api'

/**
 * 排版时间线的分组与筛选（ADR 0101）——纯函数，列表组件只管画。
 *
 * * 节点按时间**倒序**；
 * * 按**本地日历日**分组：今天 / 昨天 / 具体日期（跨年的日期带年份，由成文那一侧决定）；
 * * 「只看命名」只留命名节点，空组整组不出现。
 */

/** 一个节点的类型标记。老后端不发 `kind`：按它自己发的 `auto` 推，**不推命名**——
 *  名字是不是用户起的只有后端知道，前端猜出来的「命名」会让用户以为它不会被清理。 */
export function versionKind(v: Pick<LayoutVersionMeta, 'kind' | 'auto'>): LayoutVersionKind {
  return v.kind ?? (v.auto ? 'auto' : 'manual')
}

export type DayKey =
  | { kind: 'today' }
  | { kind: 'yesterday' }
  | { kind: 'date'; ts: number }

export interface TimelineGroup {
  /** 稳定键：本地日期 yyyy-mm-dd */
  key: string
  day: DayKey
  items: LayoutVersionMeta[]
}

const pad = (n: number) => String(n).padStart(2, '0')
const localDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

/** `now` 由调用方给（测试里钉住时刻；界面里是渲染那一刻） */
export function groupTimeline(
  versions: readonly LayoutVersionMeta[],
  opts: { namedOnly: boolean; now: number },
): TimelineGroup[] {
  const today = localDate(new Date(opts.now))
  const y = new Date(opts.now)
  y.setDate(y.getDate() - 1)
  const yesterday = localDate(y)
  const sorted = versions
    .filter((v) => !opts.namedOnly || versionKind(v) === 'named')
    .slice()
    .sort((a, b) => b.ts - a.ts)
  const groups: TimelineGroup[] = []
  for (const v of sorted) {
    const key = localDate(new Date(v.ts))
    let g = groups[groups.length - 1]
    if (!g || g.key !== key) {
      const day: DayKey =
        key === today ? { kind: 'today' } : key === yesterday ? { kind: 'yesterday' } : { kind: 'date', ts: v.ts }
      g = { key, day, items: [] }
      groups.push(g)
    }
    g.items.push(v)
  }
  return groups
}
