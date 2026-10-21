/**
 * 一次性情境提示：每类只出一次、教程进行中不出、可关、状态存本机、
 * 不依赖遥测同意。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emitActivity, onActivity } from '@/lib/activity'
import { useDocumentStore } from '@/store/documentStore'
import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { returnToLayout, useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject } from '@/types/document'
import { HINT_AUTO_DISMISS_MS, showHint, startHintEngine, useHintStore } from './hints'
import { hintSeen } from '@/store/onboardingStore'

const panel = (id: string, script: string | null): PanelObject => ({
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
  script,
  overrides: [],
})

let stop: (() => void) | null = null

beforeEach(async () => {
  vi.useFakeTimers()
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  useHintStore.setState({ current: null, token: 0 })
  useUiStore.setState({ elementPanelId: null, rightOpen: true })
  useWorkspaceStore.getState().clear()
  useSelectionStore.getState().clear()
  useValidationStore.setState({ issues: [], ready: false })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_hints')
  useDocumentStore.getState().commit(literal('seed'), (d) => {
    d.objects.push(panel('a', 'a.py'), panel('b', null))
  })
  stop = startHintEngine()
})

afterEach(() => {
  stop?.()
  stop = null
  vi.useRealTimers()
})

const current = () => useHintStore.getState().current

describe('showHint', () => {
  it('每类只出一次；看过的记在 onboardingStore', () => {
    expect(showHint('multi_select')).toBe(true)
    expect(current()).toBe('multi_select')
    expect(useOnboardingStore.getState().hintSeen.multi_select).toBeTypeOf('number')
    useHintStore.getState().dismiss()
    expect(showHint('multi_select')).toBe(false)
    expect(current()).toBeNull()
  })

  it('教程进行中不出提示；一条在显示时另一条不叠', () => {
    useOnboardingStore.getState().start()
    expect(showHint('multi_select')).toBe(false)
    useOnboardingStore.getState().skip()
    expect(showHint('multi_select')).toBe(true)
    expect(showHint('problem_found')).toBe(false)
    expect(useOnboardingStore.getState().hintSeen.problem_found).toBeUndefined()
  })

  it('到时自己收起', () => {
    showHint('fast_edit_entered')
    vi.advanceTimersByTime(HINT_AUTO_DISMISS_MS + 1)
    expect(current()).toBeNull()
  })

  it('重置提示后可以再出一次', () => {
    showHint('multi_select')
    useHintStore.getState().dismiss()
    useOnboardingStore.getState().resetHints()
    expect(showHint('multi_select')).toBe(true)
  })
})

describe('触发', () => {
  it('第一次单选可编辑面板 → panel_editable；仅排版面板 → panel_layout_only', () => {
    useSelectionStore.getState().set(['a'])
    expect(current()).toBe('panel_editable')
    useHintStore.getState().dismiss()
    useSelectionStore.getState().set(['b'])
    expect(current()).toBe('panel_layout_only')
  })

  it('图内编辑态里的画布选区不算；快速编辑里的单选不算', () => {
    useUiStore.setState({ elementPanelId: 'a' })
    useSelectionStore.getState().set(['a'])
    expect(current()).toBeNull()
    useUiStore.setState({ elementPanelId: null })
    useWorkspaceStore.getState().enterFastEdit('a')
    useHintStore.getState().dismiss() // enterFastEdit 触发的 fast_edit_entered 先收掉
    useSelectionStore.getState().set(['b'])
    expect(current()).toBeNull()
  })

  it('第一次多选 → multi_select；第一次进快速编辑 → fast_edit_entered', () => {
    useSelectionStore.getState().set(['a', 'b'])
    expect(current()).toBe('multi_select')
    useHintStore.getState().dismiss()
    emitActivity({ kind: 'workspace.mode_changed', mode: 'fast_edit' })
    expect(current()).toBe('fast_edit_entered')
  })

  it('第一次出现问题 → problem_found（盯的是检查结果，不是动作）', () => {
    useValidationStore.setState({ ready: true, issues: [] })
    expect(current()).toBeNull()
    useValidationStore.setState({
      ready: true,
      issues: [
        {
          issueId: 'i',
          ruleCode: 'r',
          severity: 'warn',
          context: 'document',
          objectRef: { documentId: 'd_hints', canvasId: 'c', objectId: 'a', gid: null },
          subject: { kind: 'object' },
          propertyPath: null,
          message: literal('x'),
          technicalDetails: {},
          fixKind: 'none',
        },
      ],
    })
    expect(current()).toBe('problem_found')
  })
})

describe('layout_page_bounds：第一次主动回到排版', () => {
  // 回排版会重新选中那张图——把无关的两条面板提示标成看过，免得它们抢槽位混淆判据
  beforeEach(() => {
    useOnboardingStore.getState().markHintSeen('panel_editable')
    useOnboardingStore.getState().markHintSeen('panel_layout_only')
  })
  /** 进快速编辑（顺手收掉 fast_edit_entered），再由用户动作回排版 */
  const enterThenReturn = () => {
    useWorkspaceStore.getState().enterFastEdit('a')
    useHintStore.getState().dismiss()
    returnToLayout()
  }
  const seen = () => hintSeen('layout_page_bounds')

  it('第一次 returnToLayout → 出现；记看过；活动信号里不带任何用户内容', () => {
    const got: object[] = []
    const off = onActivity((d) => d.kind === 'workspace.returned_to_layout' && got.push(d))
    enterThenReturn()
    off()
    expect(current()).toBe('layout_page_bounds')
    expect(seen()).toBe(true)
    expect(got).toEqual([{ kind: 'workspace.returned_to_layout' }]) // 只有 kind，没有 id / 路径 / 文字
  })

  it('第二次不再出现（持久化在 onboardingStore.hintSeen）', () => {
    enterThenReturn()
    useHintStore.getState().dismiss()
    enterThenReturn()
    expect(current()).toBeNull()
  })

  it('关闭后不立即重现：下一次回排版也不再来', () => {
    enterThenReturn()
    useHintStore.getState().dismiss()
    vi.advanceTimersByTime(HINT_AUTO_DISMISS_MS * 3)
    useWorkspaceStore.getState().enterFastEdit('b')
    returnToLayout()
    expect(current()).toBeNull()
  })

  it('重启语义：看过的记在持久化里，重新创建引擎后仍不出现', () => {
    enterThenReturn()
    useHintStore.getState().dismiss()
    stop?.()
    stop = startHintEngine()
    enterThenReturn()
    expect(current()).toBeNull()
  })

  it('本来就在排版上的「回排版」不是一次进入：不触发、不记看过', () => {
    returnToLayout()
    expect(current()).toBeNull()
    expect(seen()).toBe(false)
  })

  it('被动退出（定位问题 / 修剪 / 启动恢复）走 exitToLayout，不触发', () => {
    useWorkspaceStore.getState().enterFastEdit('a')
    useHintStore.getState().dismiss()
    useWorkspaceStore.getState().exitToLayout()
    expect(current()).toBeNull()
    expect(seen()).toBe(false)
  })

  it('教程进行中不触发，也不记看过', () => {
    useOnboardingStore.getState().start()
    enterThenReturn()
    expect(current()).toBeNull()
    expect(seen()).toBe(false)
  })

  it('右栏收起（窄屏抽屉遮挡 / 锚点不在）→ 不出现、不记看过；下一次主动回排版再来', () => {
    useUiStore.setState({ rightOpen: false })
    enterThenReturn()
    expect(current()).toBeNull()
    expect(seen()).toBe(false)
    useUiStore.setState({ rightOpen: true })
    enterThenReturn()
    expect(current()).toBe('layout_page_bounds')
  })

  it('已出现后右栏被收起 → 锚在它上面的提示一并收起', () => {
    enterThenReturn()
    expect(current()).toBe('layout_page_bounds')
    useUiStore.setState({ rightOpen: false })
    expect(current()).toBeNull()
  })

  it('同一时刻只一条：轨上的提示占着槽位时它让位；两个事件相邻只显示一条', () => {
    useWorkspaceStore.getState().enterFastEdit('a') // fast_edit_entered 占着槽位
    expect(current()).toBe('fast_edit_entered')
    returnToLayout()
    expect(current()).toBe('fast_edit_entered')
    expect(seen()).toBe(false)
  })

  it('重入：同一刻连发两枪 returned_to_layout，只显示一次、只记一次', () => {
    useWorkspaceStore.getState().enterFastEdit('a')
    useHintStore.getState().dismiss()
    const token0 = useHintStore.getState().token
    emitActivity({ kind: 'workspace.returned_to_layout' })
    emitActivity({ kind: 'workspace.returned_to_layout' })
    expect(current()).toBe('layout_page_bounds')
    expect(useHintStore.getState().token).toBe(token0 + 1)
  })

  it('自动收起走同一只计时器', () => {
    enterThenReturn()
    vi.advanceTimersByTime(HINT_AUTO_DISMISS_MS + 1)
    expect(current()).toBeNull()
  })
})

describe('layout_page_bounds 与 panel_editable 抢槽位', () => {
  it('刚从图内回来：先显示纸面说明；面板提示不叠、不记看过，关掉后下一次单选再来', () => {
    useWorkspaceStore.getState().enterFastEdit('a')
    useHintStore.getState().dismiss()
    returnToLayout() // 回排版会重新选中 a（可编辑面板）
    expect(current()).toBe('layout_page_bounds')
    expect(hintSeen('panel_editable')).toBe(false)
    useHintStore.getState().dismiss()
    useSelectionStore.getState().clear()
    useSelectionStore.getState().set(['a'])
    expect(current()).toBe('panel_editable')
  })
})
