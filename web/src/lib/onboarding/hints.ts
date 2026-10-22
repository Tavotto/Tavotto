/**
 * 一次性情境提示（Prompt 21 §九）：只给真正难发现的五件事，每类只出现一次。
 *
 * | 触发 | 提示 |
 * | --- | --- |
 * | 第一次单选一张**可编辑**面板（画布模式） | 双击可进入图内编辑 |
 * | 第一次单选一张**只能排版**的面板 | 可排版；连接源脚本后可编辑图内元素 |
 * | 第一次进入快速编辑 | 修改会保存到当前文档 |
 * | 第一次多选 | 选区附近可直接对齐和分布 |
 * | 第一次出现问题 | 左侧「问题」可定位到对象和字段 |
| 第一次因第二张图进来而进入多图排版 | 纸面就是导出的 Figure 尺寸（锚在右栏「画布」页签，见下） |
 *
 * 判据来自活动信号 + store 现状；「看过没有」记在 onboardingStore（本机，
 * 不依赖遥测同意）。**教程进行中不出提示**——coachmark 已经在说话了。
 */
import { create } from 'zustand'
import { onActivity, type ActivityDetail } from '@/lib/activity'
import { createDismissTimer } from '@/lib/dismissTimer'
import { useDocumentStore } from '@/store/documentStore'
import { hintSeen, useOnboardingStore, type HintKind } from '@/store/onboardingStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { useWorkspaceStore } from '@/store/workspace'

/**
 * 带锚点的提示：不进通知轨，由锚点所在的组件（`PageBoundsHint`，挂在右栏页签条下）自己画。
 * 仍占 `useHintStore.current` 这一个槽位——与轨上的提示互斥，不叠。
 */
export const ANCHORED_HINTS: ReadonlySet<HintKind> = new Set<HintKind>(['layout_page_bounds'])

/** 提示自动收起的时长；用户也可以随手关 */
export const HINT_AUTO_DISMISS_MS = 9000

interface HintState {
  current: HintKind | null
  /** 让同一类提示（理论上不会）再显示时也能重播 */
  token: number
  dismiss: () => void
}

/**
 * 提示的自动收起计时器：指针停在 toast 上、焦点在它的 × 上、页面不可见时都不走表
 * （`lib/dismissTimer`，与状态那只同一套）。通知轨的 `Toast` 拿着它报 hold / release。
 */
export const hintDismissTimer = createDismissTimer()

export const useHintStore = create<HintState>((set) => ({
  current: null,
  token: 0,
  dismiss: () => {
    hintDismissTimer.cancel()
    set({ current: null })
  },
}))

/**
 * 锚点此刻在不在：右栏是开着的（窄屏下它是抽屉，收起时整个卸载，页签条也就不在）。
 * 读 store 不读 DOM；不在就不出现、也**不记看过**（下一次主动回排版再来）。
 */
export function anchorAvailable(kind: HintKind): boolean {
  if (kind === 'layout_page_bounds') return useUiStore.getState().rightOpen
  return true
}

/** 显示一条；已经看过 / 教程进行中 / 已经有一条在显示 / 锚点不在 → 不显示 */
export function showHint(kind: HintKind): boolean {
  if (hintSeen(kind)) return false
  if (useOnboardingStore.getState().status === 'active') return false
  if (useHintStore.getState().current) return false
  if (!anchorAvailable(kind)) return false
  useOnboardingStore.getState().markHintSeen(kind)
  useHintStore.setState((s) => ({ current: kind, token: s.token + 1 }))
  hintDismissTimer.start(HINT_AUTO_DISMISS_MS, () => useHintStore.getState().dismiss())
  return true
}

function onSignal(d: ActivityDetail): void {
  switch (d.kind) {
    case 'selection.changed': {
      if (useUiStore.getState().elementPanelId) return // 图内编辑态里的画布选区不算
      if (d.count >= 2) {
        showHint('multi_select')
        return
      }
      if (d.count !== 1 || useWorkspaceStore.getState().mode !== 'layout') return
      const id = useSelectionStore.getState().primary()
      const o = id ? useDocumentStore.getState().doc.objects.find((x) => x.id === id) : null
      if (o?.type !== 'panel') return
      showHint(o.script ? 'panel_editable' : 'panel_layout_only')
      return
    }
    case 'workspace.mode_changed':
      if (d.mode === 'fast_edit') showHint('fast_edit_entered')
      return
    case 'workspace.entered_layout':
      // 只认第二张图把单图编辑切成多图排版的那一枪：启动恢复 / 换文档 / 切画布 / 删光都不发它
      showHint('layout_page_bounds')
      return
    default:
      return
  }
}

let stop: (() => void) | null = null

/** 启动提示引擎（幂等）；随 Workspace 生命周期清理 */
export function startHintEngine(): () => void {
  if (stop) return stop
  const unsubActivity = onActivity(onSignal)
  // 「第一次发现问题」不是一个动作，是检查结果——盯 validation store
  const unsubValidation = useValidationStore.subscribe((s, prev) => {
    if (s.ready && s.issues.length > 0 && prev.issues.length === 0) showHint('problem_found')
  })
  // 锚点跟着走：右栏收起 / 被抽屉互斥收掉时，锚在它上面的提示一并收起（不留悬浮孤岛）
  const unsubUi = useUiStore.subscribe(() => {
    const cur = useHintStore.getState().current
    if (cur && ANCHORED_HINTS.has(cur) && !anchorAvailable(cur)) useHintStore.getState().dismiss()
  })
  stop = () => {
    unsubActivity()
    unsubValidation()
    unsubUi()
    hintDismissTimer.cancel()
    useHintStore.setState({ current: null })
    stop = null
  }
  return stop
}
