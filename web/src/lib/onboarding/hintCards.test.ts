/**
 * 动画卡提示（ADR 0177）的判据：看到笨办法才出、自己用过就算学会、每条最多两次且隔一天、
 * 每次启动最多两张、设置里能整个关掉。主语是提示引擎（`hints.ts`）与它的账（`onboardingStore.hintStats`），
 * 卡片长什么样在 `components/hints/hintCard.test.tsx`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emitActivity } from '@/lib/activity'
import { useDialogLayerStore } from '@/store/dialogLayerStore'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useUnexpectedErrorStore } from '@/store/unexpectedErrorStore'
import { useDocumentStore } from '@/store/documentStore'
import { configureOnboardingPersistence, migratePersisted, useOnboardingStore } from '@/store/onboardingStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject } from '@/types/document'
import {
  CARD_AUTO_DISMISS_MS,
  CARD_MIN_GAP_MS,
  DRIFT_SETTLE_MS,
  dismissCardForGood,
  driftedAway,
  resetHintSessionForTest,
  showCardHint,
  showHint,
  startHintEngine,
  useHintStore,
} from './hints'

const panel = (id: string): PanelObject => ({
  id,
  type: 'panel',
  fileId: `${id}.pdf`,
  fileKind: 'pdf',
  nativeW: 10,
  nativeH: 10,
  x: 0,
  y: 0,
  w: 10,
  h: 10,
  script: `${id}.py`,
  overrides: [],
})

let stop: (() => void) | null = null
const current = () => useHintStore.getState().current
const stats = () => useOnboardingStore.getState().hintStats
const edgeResized = () => emitActivity({ kind: 'panel.edge_dragged', outcome: 'resized' })
const moved = () => emitActivity({ kind: 'history.pushed', label: 'history.moveObject' })

beforeEach(async () => {
  vi.useFakeTimers()
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  useOnboardingStore.setState({ hintsOff: false, hintStats: {}, activeDays: 0, lastActiveDay: null })
  resetHintSessionForTest()
  useHintStore.setState({ current: null, token: 0 })
  useDialogLayerStore.setState({ open: 0 })
  useUnexpectedErrorStore.setState({ card: null })
  useUiStore.setState({ elementPanelId: null, showRulers: true })
  useWorkspaceStore.getState().clear()
  useViewportStore.setState({ viewW: 800, viewH: 600, zoom: 1, panX: 0, panY: 0, fitted: true, tweening: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_hint_cards')
  useDocumentStore.getState().commit(literal('seed'), (d) => {
    d.objects.push(panel('a'), panel('b'))
  })
  stop = startHintEngine()
})

afterEach(() => {
  stop?.()
  stop = null
  vi.useRealTimers()
})

describe('driftedAway（纯函数）', () => {
  const page = { x: 0, y: 0, w: 180, h: 120 }
  it('页面整个在窗口里：没跑偏', () => {
    expect(driftedAway(page, { x: -20, y: -20, w: 240, h: 180 })).toBe(false)
  })
  it('只剩一角露在窗口边上：跑偏了', () => {
    expect(driftedAway(page, { x: 150, y: 100, w: 240, h: 180 })).toBe(true)
  })
  it('页面完全不在窗口里：跑偏了', () => {
    expect(driftedAway(page, { x: 400, y: 400, w: 240, h: 180 })).toBe(true)
  })
  it('放大看细节（窗口里满是页面的一小块）：不算跑偏', () => {
    expect(driftedAway(page, { x: 60, y: 40, w: 30, h: 20 })).toBe(false)
  })
  it('尺寸未知 / 退化：判不出就不判', () => {
    expect(driftedAway({ x: 0, y: 0, w: 0, h: 0 }, { x: 0, y: 0, w: 100, h: 100 })).toBe(false)
  })
})

describe('拖边裁剪（edge_crop）', () => {
  it('拖边改了图幅 → 出卡、记一次；到时自己收起不算「别再提」', () => {
    edgeResized()
    expect(current()).toBe('edge_crop')
    expect(stats().edge_crop).toMatchObject({ shows: 1, done: false })
    vi.advanceTimersByTime(CARD_AUTO_DISMISS_MS)
    expect(current()).toBeNull()
    expect(stats().edge_crop?.done).toBe(false)
  })

  it('先裁剪过一次 = 已经会了：之后拖边改图幅不再出', () => {
    emitActivity({ kind: 'panel.edge_dragged', outcome: 'cropped' })
    expect(stats().edge_crop?.done).toBe(true)
    edgeResized()
    expect(current()).toBeNull()
  })

  it('卡开着时用户按住 ⌥ 裁剪了：记学会并当场收起', () => {
    edgeResized()
    emitActivity({ kind: 'panel.edge_dragged', outcome: 'cropped' })
    expect(current()).toBeNull()
    expect(stats().edge_crop?.done).toBe(true)
  })

  it('点 × = 这条别再提', () => {
    edgeResized()
    dismissCardForGood('edge_crop')
    expect(current()).toBeNull()
    vi.advanceTimersByTime(CARD_MIN_GAP_MS + 1)
    resetHintSessionForTest()
    edgeResized()
    expect(current()).toBeNull()
  })

  it('最多出两次，两次之间至少隔一天', () => {
    edgeResized()
    useHintStore.getState().dismiss()
    edgeResized()
    expect(current()).toBeNull() // 刚出过
    vi.advanceTimersByTime(CARD_MIN_GAP_MS + 1)
    edgeResized()
    expect(current()).toBe('edge_crop')
    expect(stats().edge_crop?.shows).toBe(2)
    useHintStore.getState().dismiss()
    vi.advanceTimersByTime(CARD_MIN_GAP_MS + 1)
    resetHintSessionForTest()
    edgeResized()
    expect(current()).toBeNull() // 两次用完
  })

  it('离开多图排版：已出的收起（不算别再提）；单图编辑里不出', () => {
    edgeResized()
    useWorkspaceStore.setState({ mode: 'fast_edit' })
    expect(current()).toBeNull()
    expect(stats().edge_crop?.done).toBe(false)
    expect(showCardHint('edge_crop', Date.now() + CARD_MIN_GAP_MS + 1)).toBe(false)
  })
})

describe('对齐线（ruler_guides）', () => {
  it('30 秒内挪了 3 次对象 → 出卡；前两次不出', () => {
    moved()
    moved()
    expect(current()).toBeNull()
    moved()
    expect(current()).toBe('ruler_guides')
  })

  it('三次隔得太久不算「反复挪」', () => {
    moved()
    vi.advanceTimersByTime(20000)
    moved()
    vi.advanceTimersByTime(20000)
    moved()
    expect(current()).toBeNull()
  })

  it('拉出过参考线 = 学会；已有参考线 / 标尺关着都不出', () => {
    emitActivity({ kind: 'history.pushed', label: 'history.addGuide' })
    expect(stats().ruler_guides?.done).toBe(true)
    useOnboardingStore.setState({ hintStats: {} })
    useUiStore.setState({ showRulers: false })
    moved()
    moved()
    moved()
    expect(current()).toBeNull()
    expect(stats().ruler_guides).toBeUndefined() // 没出就不记账
    useUiStore.setState({ showRulers: true })
    useDocumentStore.getState().commit(literal('g'), (d) => {
      d.guides.push({ axis: 'y', pos: 10 })
    })
    moved()
    moved()
    moved()
    expect(current()).toBeNull()
  })
})

describe('画布回正（fit_canvas）', () => {
  const panAway = () => useViewportStore.setState({ panX: 5000, panY: 5000, fitted: false })

  it('画布移出窗口、停手 2 秒 → 出卡；没停够不出', () => {
    panAway()
    vi.advanceTimersByTime(DRIFT_SETTLE_MS - 1)
    expect(current()).toBeNull()
    vi.advanceTimersByTime(1)
    expect(current()).toBe('fit_canvas')
  })

  it('用户让画布适应了一次 = 学会，卡当场收起', () => {
    panAway()
    vi.advanceTimersByTime(DRIFT_SETTLE_MS)
    emitActivity({ kind: 'view.fitted' })
    expect(current()).toBeNull()
    expect(stats().fit_canvas?.done).toBe(true)
  })

  it('自己把画布拖回来了：卡收起，但不算学会', () => {
    panAway()
    vi.advanceTimersByTime(DRIFT_SETTLE_MS)
    useViewportStore.setState({ panX: 0, panY: 0 })
    vi.advanceTimersByTime(DRIFT_SETTLE_MS)
    expect(current()).toBeNull()
    expect(stats().fit_canvas?.done).toBe(false)
  })

  it('适应模式里（程序自己摆的视口）不提示', () => {
    useViewportStore.setState({ panX: 5000, panY: 5000, fitted: true })
    vi.advanceTimersByTime(DRIFT_SETTLE_MS)
    expect(current()).toBeNull()
  })
})

describe('节奏与开关', () => {
  it('每次启动最多两张卡', () => {
    edgeResized()
    useHintStore.getState().dismiss()
    moved()
    moved()
    moved()
    expect(current()).toBe('ruler_guides')
    useHintStore.getState().dismiss()
    useViewportStore.setState({ panX: 5000, panY: 5000, fitted: false })
    vi.advanceTimersByTime(DRIFT_SETTLE_MS)
    expect(current()).toBeNull()
    expect(stats().fit_canvas).toBeUndefined()
  })

  it('教程进行中、有对话框开着、已有一条提示在显示：都不出，也不记账', () => {
    useOnboardingStore.getState().start()
    edgeResized()
    useOnboardingStore.getState().skip()
    useDialogLayerStore.setState({ open: 1 })
    edgeResized()
    useDialogLayerStore.setState({ open: 0 })
    expect(showHint('multi_select')).toBe(true)
    edgeResized()
    expect(current()).toBe('multi_select')
    expect(stats().edge_crop).toBeUndefined()
  })

  it('反馈卡占着右下角时不出，也不记账（出了也画不出来）', () => {
    useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
    useUnexpectedErrorStore.setState({ card: { token: 1 } })
    edgeResized()
    expect(current()).toBeNull()
    expect(stats().edge_crop).toBeUndefined()
    useUnexpectedErrorStore.setState({ card: null })
    edgeResized()
    expect(current()).toBe('edge_crop')
  })

  it('设置里关掉操作提示：动画卡与一句话提示都不出', () => {
    useOnboardingStore.getState().setHintsOff(true)
    edgeResized()
    expect(current()).toBeNull()
    expect(showHint('multi_select')).toBe(false)
    expect(useOnboardingStore.getState().hintSeen.multi_select).toBeUndefined()
    useOnboardingStore.getState().setHintsOff(false)
    edgeResized()
    expect(current()).toBe('edge_crop')
  })

  it('启动提示引擎记一天；同一天不重复记', () => {
    expect(useOnboardingStore.getState().activeDays).toBe(1)
    stop?.()
    stop = startHintEngine()
    expect(useOnboardingStore.getState().activeDays).toBe(1)
  })
})

describe('账的持久化', () => {
  it('「重新显示操作提示」把动画卡的账也清掉；开关与天数不动', () => {
    edgeResized()
    dismissCardForGood('edge_crop')
    useOnboardingStore.getState().setHintsOff(true)
    useOnboardingStore.getState().resetHints()
    const s = useOnboardingStore.getState()
    expect(s.hintStats).toEqual({})
    expect(s.hintsOff).toBe(true)
    expect(s.activeDays).toBe(1)
  })

  it('迁移逐条校验：坏的那条丢掉，认不出的种类不留', () => {
    const base = { ...useOnboardingStore.getState() }
    const out = migratePersisted({
      schemaVersion: base.schemaVersion,
      flowVersion: base.flowVersion,
      status: 'completed',
      hintStats: {
        edge_crop: { shows: 2, lastAt: 123, done: true },
        fit_canvas: { shows: 'x', lastAt: 1 },
        no_such_hint: { shows: 1, lastAt: 1, done: false },
      },
      hintsOff: true,
      activeDays: 4,
      lastActiveDay: '2026-11-10',
    })
    expect(out.hintStats).toEqual({ edge_crop: { shows: 2, lastAt: 123, done: true } })
    expect(out.hintsOff).toBe(true)
    expect(out.activeDays).toBe(4)
    expect(out.lastActiveDay).toBe('2026-11-10')
  })

  it('旧版 blob 没有这几项：全新的账、提示默认开', () => {
    const base = { ...useOnboardingStore.getState() }
    const out = migratePersisted({ schemaVersion: base.schemaVersion, flowVersion: base.flowVersion, status: 'completed' })
    expect(out.hintStats).toEqual({})
    expect(out.hintsOff).toBe(false)
    expect(out.activeDays).toBe(0)
  })
})
