/**
 * 版本列表里那句「这一版变了什么」（审计 T04）。
 *
 * 改造前每行说四件事：名字（自动检查点的名字**就是日期**）、时间、对象数、
 * 图幅——日期出现两次，而"27 个对象 · 150×100mm"对着上一行的"27 个对象 ·
 * 150×100mm"，用户得自己逐字比。这里把相邻两版的差算出来，直接说出口。
 *
 * **主语必须是同一张画布。** 检查点拍的是激活画布，却按 documentId（整个
 * 项目）归档，所以时间上相邻的两版完全可能来自两张不同的画布——拿 A 的对象数
 * 减 B 的对象数得到的那个数字没有任何含义，而它看起来和真的一样。
 * 画布身份缺席（旧检查点）时**不比**：「不知道是不是同一张」是独立一档，
 * 不能当成「是」。
 *
 * 只用列表已有的元信息（`objects` / `page`），不额外拉任何一份完整快照：
 * 每条版本条目里存的是整份文档，为了一行摘要去拉 120 份是不成比例的。
 * 因此摘要只覆盖**对象数与图幅**两个维度——挪动、改字、改样式这些它量不到，
 * 措辞里必须说清楚量的是什么，不许写成「内容一致」。
 */
import { msg, type UiMessage } from '@/i18n'
import type { LayoutVersionMeta } from '@/lib/api'

const vm = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`versions.summary.${key}`, values, 'dialogs')

/**
 * `list` 里第 `index` 条**该跟谁比**。
 *
 * `list` 按时间倒序（最新在上，与抽屉里的显示顺序一致），所以往后找。
 * 找的是「同一张画布上、时间上最近的那一版」——中间夹着别的画布的检查点
 * 不影响：那些不是这条的上一版。
 */
export function comparableEarlier(
  list: readonly LayoutVersionMeta[],
  index: number,
): LayoutVersionMeta | null {
  const v = list[index]
  // 自己都不知道来自哪张画布：没有可比的对象
  if (!v?.canvasId) return null
  for (let i = index + 1; i < list.length; i++) {
    if (list[i].canvasId === v.canvasId) return list[i]
  }
  return null
}

/** 一行摘要的结构；措辞在 `versionSummaryText` 里，判据在这里 */
export type VersionSummary =
  /** 没有可比的上一版：把这一版自己的规模说出来 */
  | { kind: 'baseline'; objects: number; page: { w: number; h: number } | null }
  /** 与上一版比，对象数 / 图幅有变 */
  | {
      kind: 'changed'
      objectDelta: number
      page: { from: { w: number; h: number }; to: { w: number; h: number } } | null
    }
  /** 这两个维度都没变（**不等于内容没变**） */
  | { kind: 'sameMetrics' }

export function versionSummary(
  v: LayoutVersionMeta,
  earlier: LayoutVersionMeta | null,
): VersionSummary {
  if (!earlier) return { kind: 'baseline', objects: v.objects, page: v.page ?? null }
  const objectDelta = v.objects - earlier.objects
  // 图幅：任一侧没记（旧检查点）就是「不知道」，不许当成「没变」
  const page =
    v.page && earlier.page && (v.page.w !== earlier.page.w || v.page.h !== earlier.page.h)
      ? { from: earlier.page, to: v.page }
      : null
  if (objectDelta === 0 && !page) return { kind: 'sameMetrics' }
  return { kind: 'changed', objectDelta, page }
}

/**
 * 摘要的成文。**活得比一次渲染长的地方存描述符**，这里是渲染点，直接出文本
 * 也行——但版本抽屉里这句话与撤销标签一样会被别处引用，所以统一发 `UiMessage`。
 */
export function versionSummaryText(s: VersionSummary): UiMessage[] {
  if (s.kind === 'sameMetrics') return [vm('sameMetrics')]
  if (s.kind === 'baseline') {
    const out = [vm('baseline', { count: s.objects })]
    if (s.page) out.push(vm('page', { w: s.page.w, h: s.page.h }))
    return out
  }
  const out: UiMessage[] = []
  if (s.objectDelta > 0) out.push(vm('added', { count: s.objectDelta }))
  if (s.objectDelta < 0) out.push(vm('removed', { count: -s.objectDelta }))
  if (s.page) {
    out.push(
      vm('resized', {
        fromW: s.page.from.w,
        fromH: s.page.from.h,
        toW: s.page.to.w,
        toH: s.page.to.h,
      }),
    )
  }
  return out
}

/**
 * 自动检查点的名字由后端按时间生成（`MM-DD HH:MM`），而每一行本来就显示
 * 时间——同一个日期出现两次（审计 T04）。
 *
 * 判的是**形状**不是值：拿浏览器的本地时间去重算一遍后端那个字符串，等于把
 * 一个格式抄成两份，时区一差就两边对不上。用户自己把版本命名成这个形状时
 * 会被当成"没起名"，那是这条规则唯一的代价。
 */
const GENERATED_NAME = /^\d{2}-\d{2} \d{2}:\d{2}$/

export function versionDisplayName(v: LayoutVersionMeta): string | null {
  const name = v.name.trim()
  if (!name || GENERATED_NAME.test(name)) return null
  return name
}
