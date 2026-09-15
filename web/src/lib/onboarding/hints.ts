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

/** 显示一条；已经看过 / 教程进行中 / 已经有一条在显示 → 不显示 */
export function showHint(kind: HintKind): boolean {
  if (hintSeen(kind)) return false
  if (useOnboardingStore.getState().status === 'active') return false
  if (useHintStore.getState().current) return false
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
  stop = () => {
    unsubActivity()
    unsubValidation()
    hintDismissTimer.cancel()
    useHintStore.setState({ current: null })
    stop = null
  }
  return stop
}
