/**
 * 一次性情境提示：每类只出一次、教程进行中不出、可关、状态存本机、
 * 不依赖遥测同意。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emitActivity } from '@/lib/activity'
import { useDocumentStore } from '@/store/documentStore'
import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject } from '@/types/document'
import { HINT_AUTO_DISMISS_MS, showHint, startHintEngine, useHintStore } from './hints'

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
  useUiStore.setState({ elementPanelId: null })
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
    useOnboardingStore.getState().start({ projectId: 'p', documentId: 'd' })
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
