import type { AlignMode } from './geometry'
import { onActivity, type ActivityDetail } from './activity'
import { captureTelemetry, selectionSizeBucket } from './telemetry'

/**
 * 本地活动信号 → 遥测：**只映射白名单里的那几条**，不是把 activity bus 整个
 * 转发出去（ADR 0041 §4）。方向只有一条：活动 → 遥测（经同意态 + 后端白名单）；
 * 遥测永远不反过来驱动界面。
 *
 * 今天只映射一件事：多选浮动栏上的动作 → `context_bar_multi_used`。判据有两半：
 *   * kind 是排列 / 成组 / 取消成组之一（这三条只在动作**成功之后**才发）；
 *   * 动作是**从浮动栏**发起的（`fromContextBar()` 的同步作用域里）。同一个
 *     `alignSelectedTo` 也被属性页的排列组、命令面板调，那些不是这条事件。
 *
 * payload 里只有 action_id（闭集）与选区大小的桶——没有对象 id、没有模式之外
 * 的任何东西。
 */
export type ContextBarActionId =
  | 'align_left'
  | 'align_center'
  | 'align_right'
  | 'align_top'
  | 'align_middle'
  | 'align_bottom'
  | 'distribute_h'
  | 'distribute_v'
  | 'same_width'
  | 'same_height'
  | 'group'
  | 'ungroup'
  | 'more'

const ALIGN_ACTION: Record<AlignMode, ContextBarActionId> = {
  left: 'align_left',
  hcenter: 'align_center',
  right: 'align_right',
  top: 'align_top',
  vcenter: 'align_middle',
  bottom: 'align_bottom',
  hdist: 'distribute_h',
  vdist: 'distribute_v',
  samew: 'same_width',
  sameh: 'same_height',
}

let originDepth = 0

/** 在浮动栏的点击处理器里包住那个 action：作用域内发出的成功信号才算「来自浮动栏」 */
export function fromContextBar<T>(fn: () => T): T {
  originDepth++
  try {
    return fn()
  } finally {
    originDepth--
  }
}

/** 「更多」按钮没有对应的活动信号（它只是导航到属性页），直接记 */
export function captureContextBarMore(selectionSize: number): void {
  captureTelemetry('context_bar_multi_used', {
    action_id: 'more' satisfies ContextBarActionId,
    selection_size_bucket: selectionSizeBucket(selectionSize),
  })
}

/** 一条活动信号对应哪条遥测；映射不到 = 不发。**纯函数**，用例直接钉它 */
export function activityToTelemetry(
  detail: ActivityDetail,
  fromBar: boolean,
): { event: 'context_bar_multi_used'; properties: Record<string, string> } | null {
  if (!fromBar) return null
  let actionId: ContextBarActionId
  switch (detail.kind) {
    case 'selection.aligned':
      actionId = ALIGN_ACTION[detail.mode]
      break
    case 'selection.grouped':
      actionId = 'group'
      break
    case 'selection.ungrouped':
      actionId = 'ungroup'
      break
    default:
      return null
  }
  return {
    event: 'context_bar_multi_used',
    properties: { action_id: actionId, selection_size_bucket: selectionSizeBucket(detail.count) },
  }
}

let stop: (() => void) | null = null

/** 订阅活动信号。幂等：已经在听就回同一个 stop */
export function startActivityTelemetry(): () => void {
  if (stop) return stop
  const off = onActivity((detail) => {
    const mapped = activityToTelemetry(detail, originDepth > 0)
    if (mapped) captureTelemetry(mapped.event, mapped.properties)
  })
  stop = () => {
    off()
    stop = null
  }
  return stop
}
