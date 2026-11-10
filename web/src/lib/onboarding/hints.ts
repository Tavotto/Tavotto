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
 *
 * **动画卡提示（ADR 0177）**：藏起来的手势用一张会动的小卡教（`components/hints/HintCard`）。
 * 它们不在「第一次」时说，而在**看到用户正在用笨办法时**说；用户自己用过一次那个功能就算学会、永不再提。
 *
 * | 触发（笨办法） | 卡片 | 学会 |
 * | --- | --- | --- |
 * | 画布大半移出窗口、停手 2 秒 | 双击空白处回正 | 让画布「适应」过一次（任何入口） |
 * | 多图排版里拖图边改了图幅 | 按住 ⌥ 改为裁剪 | 拖边裁剪过一次 |
 * | 多图排版里 30 秒内挪了 3 次对象（鼠标拖或方向键；很小的拖动不算）、还没有参考线 | 从标尺拖出对齐线 | 拉出过一条参考线 |
 * | 手几乎走了一条横 / 竖线（没按 ⇧） | 按住 ⇧ 只沿一个方向走 | ⇧ 拖过一次 |
 * | 吸上之后 4 秒内又来一次很小的拖动 | 按住 ⌘ / Ctrl 暂时不吸附 | ⌘ / Ctrl 拖过一次 |
 * | 20 秒内 3 次不到 2 mm 的拖动 | 方向键微移 | 用方向键挪过画布对象 |
 * | 旋转停在离 15° 档位不到 3° 处 | 按住 ⇧ 按 15° 一档转 | ⇧ 转过一次 |
 *
 * 节奏：每条最多出 `CARD_MAX_SHOWS` 次、两次之间至少隔 `CARD_MIN_GAP_MS`；每次启动最多 `CARD_SESSION_BUDGET` 张；
 * 分层解锁按 `activeDays`；与一句话提示共用 `useHintStore.current` 一个槽位。
 */
import { create } from 'zustand'
import { onActivity, type ActivityDetail } from '@/lib/activity'
import { createDismissTimer } from '@/lib/dismissTimer'
import { useDialogLayerStore } from '@/store/dialogLayerStore'
import { useDocumentStore } from '@/store/documentStore'
import { feedbackCornerBusy } from '@/store/feedbackCorner'
import { useInteractionStore } from '@/store/interactionStore'
import { hintSeen, useOnboardingStore, type HintKind } from '@/store/onboardingStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { currentVisibleRectMm, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { fastEditPanelOf } from '@/store/workspaceStore'
import { fastEditCanvasBox, stageFitFrame } from '@/store/zoomToSelection'

/**
 * 带锚点的提示：不进通知轨，由锚点所在的组件（`PageBoundsHint`，挂在右栏页签条下）自己画。
 * 仍占 `useHintStore.current` 这一个槽位——与轨上的提示互斥，不叠。
 */
export const ANCHORED_HINTS: ReadonlySet<HintKind> = new Set<HintKind>(['layout_page_bounds'])

/** 提示自动收起的时长；用户也可以随手关 */
export const HINT_AUTO_DISMISS_MS = 9000

/* ───────── 动画卡提示（ADR 0177） ───────── */

/** 动画卡提示：由 `components/hints/HintCard` 画，不进通知轨；仍占 `useHintStore.current` 这一个槽位 */
export const CARD_HINTS = [
  'fit_canvas',
  'edge_crop',
  'ruler_guides',
  'shift_axis',
  'no_snap',
  'arrow_nudge',
  'shift_rotate',
] as const satisfies readonly HintKind[]
export type CardHintKind = (typeof CARD_HINTS)[number]
export const isCardHint = (k: HintKind | null): k is CardHintKind =>
  k !== null && (CARD_HINTS as readonly HintKind[]).includes(k)

/** 分层：1 = 看得见摸得着的基本手势；2 = 修饰键；3 = 效率。层数越高越晚解锁 */
export const CARD_TIER: Record<CardHintKind, 1 | 2 | 3> = {
  fit_canvas: 1,
  edge_crop: 1,
  ruler_guides: 1,
  shift_axis: 2,
  no_snap: 2,
  arrow_nudge: 2,
  shift_rotate: 2,
}
/** 每一层要求累计用过几天（本地日期计）才解锁 */
export const TIER_MIN_DAYS: Record<1 | 2 | 3, number> = { 1: 0, 2: 2, 3: 5 }
/** 每条最多出几次；× 关掉或自己学会就不再出 */
export const CARD_MAX_SHOWS = 2
/** 同一条两次之间至少隔这么久（20 小时 ≈「至少隔一天」，又不卡在整点上） */
export const CARD_MIN_GAP_MS = 20 * 60 * 60 * 1000
/** 每次启动最多出几张卡 */
export const CARD_SESSION_BUDGET = 2
/** 卡片自己收起的时长（动画一遍 8 秒上下，够看两遍再多一点）；指针停在卡上不走表 */
export const CARD_AUTO_DISMISS_MS = 30000
/** 画布跑偏后停手多久才提示 */
export const DRIFT_SETTLE_MS = 2000
/** 「反复挪对象」：这么长时间内挪了这么多次 */
export const NUDGE_WINDOW_MS = 30000
export const NUDGE_COUNT = 3
/** 「一点一点挪」：这么长时间内有这么多次不到 2 mm 的拖动 */
export const TINY_WINDOW_MS = 20000
export const TINY_COUNT = 3
/** 「跟吸附较劲」：吸上之后这么快又来一次很小的拖动 */
export const SNAP_FIGHT_MS = 4000

let sessionCards = 0
/** 只给测试：把「本次启动已出几张卡」清零 */
export function resetHintSessionForTest(): void {
  sessionCards = 0
}

type Box = { x: number; y: number; w: number; h: number }

/**
 * 画布是不是「跑偏了」：取景框（适应画布要框住的那块）露在窗口里的部分，既不到它自己的三成、也不到窗口的三成。
 * 两个条件都要：放大看细节时露出来的只是页面一角（第一条成立），但窗口里满满都是页面（第二条不成立）——那不是跑偏。
 */
export function driftedAway(frame: Box, visible: Box): boolean {
  const w = Math.min(frame.x + frame.w, visible.x + visible.w) - Math.max(frame.x, visible.x)
  const h = Math.min(frame.y + frame.h, visible.y + visible.h) - Math.max(frame.y, visible.y)
  const inter = w > 0 && h > 0 ? w * h : 0
  const frameArea = frame.w * frame.h
  const viewArea = visible.w * visible.h
  if (!(frameArea > 0) || !(viewArea > 0)) return false
  return inter / frameArea < 0.3 && inter / viewArea < 0.3
}

/** 此刻画布跑偏了吗：不在适应模式、没有补间 / 手势在走，且 `driftedAway` */
export function canvasDriftedNow(): boolean {
  const vp = useViewportStore.getState()
  if (vp.fitted || vp.tweening) return false
  if (useInteractionStore.getState().kind !== 'none') return false
  const visible = currentVisibleRectMm()
  if (!visible) return false
  const doc = useDocumentStore.getState().doc
  const id = fastEditPanelOf(useWorkspaceStore.getState())
  return driftedAway(stageFitFrame(doc.objects, doc.page, id, fastEditCanvasBox(doc, id)), visible)
}

/** 这张卡此刻说得对吗（场景还在不在）；出之前判一次，出了之后场景变了就收起 */
export function cardApplicable(kind: CardHintKind): boolean {
  if (kind === 'fit_canvas') return canvasDriftedNow()
  // 吸附关着的时候谈不上「暂时不吸附」
  if (kind === 'no_snap') return useUiStore.getState().snapEnabled
  // 拖边裁剪与标尺只在多图排版里成立：拖边裁剪是排版手势，标尺在单图编辑里不画
  if (kind === 'edge_crop' || kind === 'ruler_guides') {
    if (useWorkspaceStore.getState().mode !== 'layout') return false
    if (kind === 'ruler_guides') {
      return useUiStore.getState().showRulers && useDocumentStore.getState().doc.guides.length === 0
    }
  }
  return true
}

/** 出一张动画卡；返回是否真的出了。不出的原因一律不记账（下次场景再来还能出） */
export function showCardHint(kind: CardHintKind, now: number = Date.now()): boolean {
  const ob = useOnboardingStore.getState()
  if (ob.hintsOff || ob.status === 'active') return false
  if (useHintStore.getState().current) return false
  if (useDialogLayerStore.getState().open > 0) return false
  // 反馈卡占着右下角：这时出了也画不出来，白白记一次、白走 30 秒（Codex #1830）
  if (feedbackCornerBusy()) return false
  if (sessionCards >= CARD_SESSION_BUDGET) return false
  if (ob.activeDays < TIER_MIN_DAYS[CARD_TIER[kind]]) return false
  const stat = ob.hintStats[kind]
  if (stat?.done || (stat?.shows ?? 0) >= CARD_MAX_SHOWS) return false
  if (stat && stat.shows > 0 && now - stat.lastAt < CARD_MIN_GAP_MS) return false
  if (!cardApplicable(kind)) return false
  ob.recordHintShown(kind)
  sessionCards += 1
  useHintStore.setState((s) => ({ current: kind, token: s.token + 1 }))
  hintDismissTimer.start(CARD_AUTO_DISMISS_MS, () => useHintStore.getState().dismiss())
  return true
}

/** 用户自己用过这个功能了：记「学会」，正开着的那张一并收起 */
export function learnCardHint(kind: CardHintKind): void {
  useOnboardingStore.getState().markHintDone(kind)
  if (useHintStore.getState().current === kind) useHintStore.getState().dismiss()
}

/** 用户点了卡上的 ×：这条别再提 */
export function dismissCardForGood(kind: CardHintKind): void {
  useOnboardingStore.getState().markHintDone(kind)
  useHintStore.getState().dismiss()
}

const localDay = (d = new Date()) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`

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
 * 锚点此刻在不在：右栏是开着的（窄屏下它是抽屉，收起时整个卸载，页签条也就不在），
 * 且停的不是「画布」页——用户已经在这条提示指向的那一页上，它既自指（「可在『画布』页调整」+「打开画布页」）
 * 又会盖住页签下面的身份头与「页面尺寸」标题（2026-10-31 UI 审计 B7）；切到画布页时已出的也随之收起。
 * 读 store 不读 DOM；不在就不出现、也**不记看过**（下一次主动回排版再来）。
 */
export function anchorAvailable(kind: HintKind): boolean {
  if (kind === 'layout_page_bounds') {
    const ui = useUiStore.getState()
    return ui.rightOpen && ui.rightTab !== 'canvas'
  }
  return true
}

/**
 * 「选中一张图」类提示（双击进图内编辑 / 只能排版）此刻还说得对吗：没进图内编辑、仍恰好选中一张面板、在多图排版里。
 * 提示可能在通知轨里排队好几秒才露面（别的提示条占着位置），用户这期间已经按 Enter 进了图内编辑时，
 * 「双击进入图内编辑」就是错的；显示前、露面前都再判一次。
 */
export function selectionHintApplicable(): boolean {
  if (useUiStore.getState().elementPanelId) return false
  if (useWorkspaceStore.getState().mode !== 'layout') return false
  const sel = useSelectionStore.getState()
  if (sel.ids.length !== 1) return false
  const id = sel.primary()
  return useDocumentStore.getState().doc.objects.some((x) => x.id === id && x.type === 'panel')
}

const isSelectionHint = (k: HintKind | null) => k === 'panel_editable' || k === 'panel_layout_only'

/** 显示一条；已经看过 / 教程进行中 / 已经有一条在显示 / 锚点不在 → 不显示 */
export function showHint(kind: HintKind): boolean {
  if (hintSeen(kind)) return false
  if (useOnboardingStore.getState().hintsOff) return false
  if (useOnboardingStore.getState().status === 'active') return false
  if (useHintStore.getState().current) return false
  if (!anchorAvailable(kind)) return false
  // 不适用就不显示，也不记「看过」：下次第一次选中图时仍可提示
  if (isSelectionHint(kind) && !selectionHintApplicable()) return false
  useOnboardingStore.getState().markHintSeen(kind)
  useHintStore.setState((s) => ({ current: kind, token: s.token + 1 }))
  hintDismissTimer.start(HINT_AUTO_DISMISS_MS, () => useHintStore.getState().dismiss())
  return true
}

/** 最近几次「挪对象」的时刻（滑动窗口） */
let recentMoves: number[] = []
/** 最近几次「很小的拖动」的时刻 */
let recentTiny: number[] = []
/** 上一次拖动吸上了的时刻（没吸上 = 0） */
let lastSnapAt = 0

/** 一次拖动松手：先记学会，再按「最像哪种笨办法」出一张（一个槽位，先到先得） */
function onDragged(d: Extract<ActivityDetail, { kind: 'object.dragged' }>): void {
  const now = Date.now()
  if (d.mods === 'shift' || d.mods === 'both') learnCardHint('shift_axis')
  if (d.mods === 'meta' || d.mods === 'both') learnCardHint('no_snap')
  const foughtSnap = d.size === 'tiny' && d.mods === 'none' && lastSnapAt > 0 && now - lastSnapAt <= SNAP_FIGHT_MS
  lastSnapAt = d.snapped && d.mods === 'none' ? now : 0
  if (d.size === 'tiny') recentTiny = [...recentTiny.filter((t) => now - t <= TINY_WINDOW_MS), now]
  // 先判更具体的笨办法，都不是才算一次普通的「挪」
  if (foughtSnap && showCardHint('no_snap', now)) return
  if (recentTiny.length >= TINY_COUNT && showCardHint('arrow_nudge', now)) {
    recentTiny = []
    return
  }
  // 很小的拖动先攒着看是不是「一点一点挪」，不急着算进「反复挪」——否则对齐线那张卡总是抢在微移前面
  if (d.size === 'tiny') return
  if (d.track === 'straight' && d.mods === 'none' && showCardHint('shift_axis', now)) return
  noteMove(now)
}

/** 又挪了一次对象（鼠标拖或方向键）：更具体的卡都没出，才轮到「从标尺拖出对齐线」 */
function noteMove(now: number): void {
  recentMoves = [...recentMoves.filter((t) => now - t <= NUDGE_WINDOW_MS), now]
  if (recentMoves.length >= NUDGE_COUNT && showCardHint('ruler_guides', now)) recentMoves = []
}

function onSignal(d: ActivityDetail): void {
  switch (d.kind) {
    case 'view.fitted':
      learnCardHint('fit_canvas')
      return
    case 'panel.edge_dragged':
      // 裁剪过了 = 已经会了；改了图幅 = 正是说「按住 ⌥ 就是裁剪」的时候
      if (d.outcome === 'cropped') learnCardHint('edge_crop')
      else showCardHint('edge_crop')
      return
    case 'object.dragged':
      onDragged(d)
      return
    case 'object.nudged':
      learnCardHint('arrow_nudge')
      noteMove(Date.now())
      return
    case 'object.rotated':
      if (d.snapped) learnCardHint('shift_rotate')
      else if (d.near) showCardHint('shift_rotate')
      return
    case 'history.pushed':
      if (d.label === 'history.addGuide') learnCardHint('ruler_guides')
      return
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
  useOnboardingStore.getState().touchActiveDay(localDay())
  const unsubActivity = onActivity(onSignal)
  // 画布跑偏：视口每动一下重新计时，停手 `DRIFT_SETTLE_MS` 后还偏着才说；已出的那张在画布回来后收起
  let driftTimer: ReturnType<typeof setTimeout> | null = null
  const unsubViewport = useViewportStore.subscribe((s, prev) => {
    if (s.zoom === prev.zoom && s.panX === prev.panX && s.panY === prev.panY) return
    if (driftTimer) clearTimeout(driftTimer)
    driftTimer = setTimeout(() => {
      driftTimer = null
      if (useHintStore.getState().current === 'fit_canvas') {
        if (!canvasDriftedNow()) useHintStore.getState().dismiss()
      } else if (canvasDriftedNow()) {
        showCardHint('fit_canvas')
      }
    }, DRIFT_SETTLE_MS)
  })
  // 排版手势的两张卡：离开多图排版就不对了
  const unsubMode = useWorkspaceStore.subscribe((s, prev) => {
    if (s.mode === prev.mode) return
    const cur = useHintStore.getState().current
    if ((cur === 'edge_crop' || cur === 'ruler_guides') && s.mode !== 'layout') useHintStore.getState().dismiss()
  })
  // 「第一次发现问题」不是一个动作，是检查结果——盯 validation store
  // 提示的内容是「问题可定位到对象和字段」，所以只认**能定位到对象**的问题：空画布上只有文档级问题
  // （无对象可定位）时不弹，等第一个落在对象上的问题出现才说（2026-10-31 UI 审计 B6）
  const locatable = (issues: { objectRef: { objectId: string | null } }[]) =>
    issues.some((i) => i.objectRef.objectId !== null)
  const unsubValidation = useValidationStore.subscribe((s, prev) => {
    if (s.ready && locatable(s.issues) && !locatable(prev.issues)) showHint('problem_found')
  })
  // 锚点跟着走：右栏收起 / 被抽屉互斥收掉时，锚在它上面的提示一并收起（不留悬浮孤岛）
  const unsubUi = useUiStore.subscribe(() => {
    const cur = useHintStore.getState().current
    if (cur && ANCHORED_HINTS.has(cur) && !anchorAvailable(cur)) useHintStore.getState().dismiss()
  })
  // 已出（含排队未露面）的「选中图」提示，在图内编辑 / 选区变了之后就不对了：收起；
  // 用户自己进过图内编辑 = 已经会了，记为看过
  const recheckSelectionHint = () => {
    if (isSelectionHint(useHintStore.getState().current) && !selectionHintApplicable()) useHintStore.getState().dismiss()
  }
  const unsubSelection = useSelectionStore.subscribe(recheckSelectionHint)
  const unsubEdit = useUiStore.subscribe((s, prev) => {
    if (s.elementPanelId && !prev.elementPanelId) {
      useOnboardingStore.getState().markHintSeen('panel_editable')
      recheckSelectionHint()
    }
  })
  stop = () => {
    if (driftTimer) clearTimeout(driftTimer)
    recentMoves = []
    recentTiny = []
    lastSnapAt = 0
    unsubViewport()
    unsubMode()
    unsubSelection()
    unsubEdit()
    unsubActivity()
    unsubValidation()
    unsubUi()
    hintDismissTimer.cancel()
    useHintStore.setState({ current: null })
    stop = null
  }
  return stop
}
