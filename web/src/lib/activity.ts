import type { AlignMode } from './geometry'

/**
 * 本地**活动信号**：某个真实的用户动作刚刚完成。
 *
 * 给谁用：后续的新手提示（Prompt 21 的 coachmark）要知道「用户已经自己对齐过
 * 一次了，别再提示」——它订阅这里，而不是 import 进 `store/actions`。反过来
 * 也一样：核心 action 只发一声，不 import 任何 onboarding / 提示模块。
 *
 * 它**不是遥测**：只在本进程的 `window` 上派发、不落盘、不出网、不带任何用户
 * 内容（没有对象 id、没有文字、没有文件名）——detail 里只有闭集枚举与计数。
 * 发送失败（比如没有 `window`）被吞掉：一条提示信号绝不能让业务动作失败。
 */
export const ACTIVITY_EVENT = 'tavotto:activity'

export type ActivityDetail =
  | { kind: 'selection.aligned'; mode: AlignMode; ref: 'selection' | 'page' | 'primary'; count: number }
  | { kind: 'selection.grouped'; count: number }
  | { kind: 'selection.ungrouped'; count: number }

export type ActivityKind = ActivityDetail['kind']

export function emitActivity(detail: ActivityDetail): void {
  try {
    if (typeof window === 'undefined') return
    window.dispatchEvent(new CustomEvent<ActivityDetail>(ACTIVITY_EVENT, { detail }))
  } catch {
    /* 本地信号失败不影响业务动作 */
  }
}

/** 订阅活动信号；返回取消订阅 */
export function onActivity(listener: (detail: ActivityDetail) => void): () => void {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent<ActivityDetail>).detail
    if (detail && typeof detail.kind === 'string') listener(detail)
  }
  window.addEventListener(ACTIVITY_EVENT, handler)
  return () => window.removeEventListener(ACTIVITY_EVENT, handler)
}
