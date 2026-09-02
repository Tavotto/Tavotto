/**
 * 教程引擎 + 步骤表（ADR 0040）：每一步只被**真实动作**完成，重放 / 乱序 /
 * 不在教程里的动作都完成不了；离开教程项目自动暂停、回来自动继续。
 *
 * 装置：一份两张图的教程文档（fileId 与 tutorial_meta 对上）、第二张图（带
 * spec_issue 的那张）已经精确渲染过（seedExactRender）、项目 store 认领了
 * 教程项目。动作全部走生产 action（enterElementEdit / setOverride / focusObject /
 * alignSelectedTo …），不手写 store 状态去「模拟完成」。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { emitActivity } from '@/lib/activity'
import type { Manifest, TutorialMetadata } from '@/lib/api'
import { focusObject } from '@/lib/issueFocus'
import { seedExactRender } from '@/test/renderFixtures'
import { alignSelectedTo, enterElementEdit, setOverride } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { addFigureToLayout, openFastEdit, returnToLayout, useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type PanelObject } from '@/types/document'
import {
  completeStep,
  currentContext,
  evaluate,
  inTutorial,
  resetSignalsForTest,
  signalSnapshot,
  startOnboardingEngine,
} from './flow'
import { buildContext, EMPTY_SIGNALS, problemsResolved, STEP_TABLE_MATCHES_IDS, STEPS, TYPOGRAPHY_PROPS_FIGURE } from './steps'
import { useTutorialStore } from './tutorial'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const META: TutorialMetadata = {
  schema: 1,
  tutorial_version: 1,
  project_name: 'Tutorial',
  document_name: 'Tutorial',
  document_id: 'tavotto-tutorial',
  expected_stems: ['Fig1_kinetics', 'Fig2_correlation'],
  editable_role_preferences: ['title', 'legend_text', 'axis_label'],
  panels: [
    {
      key: 'first',
      file: 'Fig1_kinetics.pdf',
      stem: 'Fig1_kinetics',
      script: 'fig1_kinetics.py',
      editable_roles: ['title', 'legend_text', 'axis_label', 'line'],
      spec_issue: null,
    },
    {
      key: 'second',
      file: 'Fig2_correlation.pdf',
      stem: 'Fig2_correlation',
      script: 'fig2_correlation.py',
      editable_roles: ['title', 'legend_text', 'axis_label', 'text'],
      spec_issue: { code: 'font-below-absolute-floor', role: 'text', text_prefix: 'n = 60' },
    },
  ],
}

const panel = (id: string, fileId: string, script: string, x: number): PanelObject => ({
  id,
  type: 'panel',
  fileId,
  fileKind: 'pdf',
  nativeW: 75,
  nativeH: 58,
  x,
  y: 10,
  w: 75,
  h: 58,
  script,
  overrides: [],
})

const manifest: Manifest = {
  stem: 'Fig2_correlation',
  size_mm: [73, 58.68],
  elements: [
    { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], draggable: false, editable: [] },
    {
      gid: 'axes_0.title',
      role: 'title',
      label: '标题',
      bbox: [0.3, 0.05, 0.4, 0.08],
      draggable: true,
      editable: [
        { prop: 'text', type: 'text', value: 'Correlation' },
        { prop: 'fontsize', type: 'number', value: 10 },
        { prop: 'color', type: 'color', value: '#000000' },
        { prop: 'weight', type: 'enum', value: 'normal' },
      ],
    },
    {
      gid: 'text_0',
      role: 'text',
      label: '文字',
      bbox: [0.6, 0.8, 0.3, 0.06],
      draggable: true,
      editable: [{ prop: 'fontsize', type: 'number', value: 7 }],
    },
    {
      gid: 'axes_0.lines_0',
      role: 'line',
      label: '曲线',
      bbox: [0.1, 0.2, 0.8, 0.6],
      draggable: false,
      editable: [{ prop: 'linewidth', type: 'number', value: 1 }],
    },
  ],
}

const ob = () => useOnboardingStore.getState()
const tick = () => new Promise<void>((r) => setTimeout(r, 0))
let stopEngine: (() => void) | null = null

async function setupTutorial() {
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  resetSignalsForTest()
  useUiStore.setState({
    elementPanelId: null,
    selectedGids: [],
    exportOpen: false,
    leftOpen: true,
    leftTab: 'assets',
    layout: 'wide',
    status: null,
  })
  useSelectionStore.getState().clear()
  useWorkspaceStore.getState().clear()
  useRenderStore.getState().clear()
  useValidationStore.setState({ issues: [], ready: false, results: [] })
  useAssetStore.setState({
    byId: {
      'Fig1_kinetics.pdf': {
        id: 'Fig1_kinetics.pdf',
        name: 'Fig1_kinetics',
        folder: '.',
        kind: 'pdf',
        native_w_mm: 75,
        native_h_mm: 58,
        mtime: 1,
        script: 'fig1_kinetics.py',
      },
      'Fig2_correlation.pdf': {
        id: 'Fig2_correlation.pdf',
        name: 'Fig2_correlation',
        folder: '.',
        kind: 'pdf',
        native_w_mm: 73,
        native_h_mm: 58,
        mtime: 1,
        script: 'fig2_correlation.py',
      },
    },
    panels: [],
    loaded: true,
  })
  useProjectStore.setState({ phase: 'open', project: { open: true, id: 'p_tut', tutorial: true } })
  useTutorialStore.setState({ meta: META })
  const pd = emptyProject()
  pd.canvases[0].objects = [
    panel('p1', 'Fig1_kinetics.pdf', 'fig1_kinetics.py', 10),
    panel('p2', 'Fig2_correlation.pdf', 'fig2_correlation.py', 95),
  ]
  await useDocumentStore.getState().switchDocument(pd, META.document_id)
  const p2 = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p2') as PanelObject
  seedExactRender(p2, manifest)
  ob().start({ projectId: 'p_tut', documentId: META.document_id })
  stopEngine = startOnboardingEngine()
  await tick()
}

beforeEach(async () => {
  await setupTutorial()
})

afterEach(() => {
  stopEngine?.()
  stopEngine = null
})

describe('步骤表', () => {
  it('与 STEP_IDS 一一对应', () => {
    expect(STEP_TABLE_MATCHES_IDS).toBe(true)
    expect(STEPS.map((s) => s.id)).toContain('locate_problem')
  })

  it('要编辑的那张图是带 spec_issue 的第二张；两张都在文档里', () => {
    const ctx = currentContext()
    expect(ctx.edit?.meta.key).toBe('second')
    expect(ctx.edit?.panel.id).toBe('p2')
    expect(ctx.other?.panel.id).toBe('p1')
    expect([...ctx.tutorialPanelIds].sort()).toEqual(['p1', 'p2'])
    expect(ctx.elements?.length).toBe(4)
  })

  it('图内排版属性集合来自 lib/typography 的 figureText 路径', () => {
    expect([...TYPOGRAPHY_PROPS_FIGURE].sort()).toEqual(['color', 'fontfamily', 'fontsize', 'style', 'weight'])
  })
})

describe('完整流程：每一步都由真实动作完成', () => {
  it('welcome 是手动步骤，引擎不会自己跳过', async () => {
    expect(ob().currentStep).toBe('welcome')
    enterElementEdit('p2')
    await tick()
    expect(ob().currentStep).toBe('welcome')
    completeStep('welcome')
    await tick()
    // 图内编辑态已经在了 → open_fast_edit 立刻被识别为完成（提前完成的动作自动识别）
    expect(ob().completedSteps).toContain('open_fast_edit')
    expect(ob().currentStep).toBe('select_text')
  })

  it('open_fast_edit：只认那张图；进别的图不算', async () => {
    completeStep('welcome')
    enterElementEdit('p1')
    await tick()
    expect(ob().currentStep).toBe('open_fast_edit')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    expect(ob().currentStep).toBe('select_text')
  })

  it('select_text：主选必须是文字类 role；选曲线不算、选 figure 不算', async () => {
    completeStep('welcome')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    useUiStore.getState().setSelectedGid('axes_0.lines_0')
    await tick()
    expect(ob().currentStep).toBe('select_text')
    useUiStore.getState().setSelectedGid('figure')
    await tick()
    expect(ob().currentStep).toBe('select_text')
    useUiStore.getState().setSelectedGid('axes_0.title')
    await tick()
    expect(ob().currentStep).toBe('change_typography')
  })

  it('change_typography：要一条真实的排版 override + 一条历史；非排版属性不算；重放不算', async () => {
    completeStep('welcome')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    useUiStore.getState().setSelectedGid('axes_0.title')
    await tick()
    expect(ob().currentStep).toBe('change_typography')
    // 非排版属性：有历史但没有排版信号
    setOverride('p2', 'axes_0.lines_0', 'linewidth', 2, true)
    await tick()
    expect(ob().currentStep).toBe('change_typography')
    // 只有信号没有历史（重放一条假信号）也不算
    emitActivity({ kind: 'element.property_changed', prop: 'fontsize' })
    resetSignalsForTest()
    await tick()
    expect(ob().currentStep).toBe('change_typography')
    const before = useDocumentStore.getState().past.length
    setOverride('p2', 'axes_0.title', 'fontsize', 12, true)
    await tick()
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
    expect(ob().currentStep).toBe('locate_problem')
    // 消费过的信号已清零：重放一次 history.pushed 不会顺手完成下一步
    expect(signalSnapshot().typographyChanged).toBe(0)
    expect(signalSnapshot().historyPushed).toBe(0)
  })

  it('locate_problem：真实 focusObject 成功且落在教程面板上才算；失败不算', async () => {
    completeStep('welcome')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    useUiStore.getState().setSelectedGid('axes_0.title')
    await tick()
    setOverride('p2', 'axes_0.title', 'fontsize', 12, true)
    await tick()
    expect(ob().currentStep).toBe('locate_problem')
    const docId = useDocumentStore.getState().documentId
    const canvasId = useDocumentStore.getState().activeCanvasId
    // 对象已删 → 失败 → 不算
    const bad = focusObject({ documentId: docId, canvasId, objectId: 'nope', gid: null })
    expect(bad.ok).toBe(false)
    await tick()
    expect(ob().currentStep).toBe('locate_problem')
    const ok = focusObject({ documentId: docId, canvasId, objectId: 'p2', gid: 'text_0' }, 'fontsize')
    expect(ok.ok).toBe(true)
    await tick()
    expect(ob().currentStep).toBe('export_original')
  })

  it('locate_problem 的「已解决」出口：那张图渲染过、检查跑过、教程面板上没问题', () => {
    const ctx = buildContext(META, EMPTY_SIGNALS)
    expect(problemsResolved(ctx)).toBe(false) // 检查还没跑
    useValidationStore.setState({ ready: true, issues: [] })
    expect(problemsResolved(buildContext(META, EMPTY_SIGNALS))).toBe(true)
    // 别的项目的问题不影响；教程面板上的问题让它回 false
    useValidationStore.setState({
      ready: true,
      issues: [
        {
          issueId: 'x',
          ruleCode: 'font-below-absolute-floor',
          severity: 'error',
          context: 'document',
          objectRef: { documentId: META.document_id, canvasId: 'c', objectId: 'p2', gid: 'text_0' },
          subject: { kind: 'element' },
          propertyPath: 'fontsize',
          message: literal('7 pt'),
          technicalDetails: {},
          fixKind: 'safe_auto',
        },
      ],
    })
    expect(problemsResolved(buildContext(META, EMPTY_SIGNALS))).toBe(false)
  })

  it('export_original / add_to_layout / multi_select_align / export_canvas → done', async () => {
    completeStep('welcome')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    useUiStore.getState().setSelectedGid('axes_0.title')
    await tick()
    setOverride('p2', 'axes_0.title', 'fontsize', 12, true)
    await tick()
    const docId = useDocumentStore.getState().documentId
    const canvasId = useDocumentStore.getState().activeCanvasId
    focusObject({ documentId: docId, canvasId, objectId: 'p2', gid: 'text_0' }, 'fontsize')
    await tick()
    expect(ob().currentStep).toBe('export_original')

    // 面板开着、范围是画布 → 不算；切成原图 → 记下；面板没关 → 还不算；关掉 → 完成
    useUiStore.getState().setExportOpen(true)
    emitActivity({ kind: 'export.scope_changed', scope: 'canvas' })
    await tick()
    expect(ob().currentStep).toBe('export_original')
    emitActivity({ kind: 'export.scope_changed', scope: 'original' })
    await tick()
    expect(ob().currentStep).toBe('export_original')
    useUiStore.getState().setExportOpen(false)
    await tick()
    expect(ob().currentStep).toBe('add_to_layout')

    // 两张图都在画布上，但此刻在快速编辑里：要回到版面才算
    expect(useWorkspaceStore.getState().mode).toBe('fast_edit')
    addFigureToLayout('Fig2_correlation.pdf')
    await tick()
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(ob().currentStep).toBe('multi_select_align')

    // 只选一张对齐不算；两张教程图 + 对齐 → 完成
    useSelectionStore.getState().set(['p1'])
    alignSelectedTo('top', 'page')
    await tick()
    expect(ob().currentStep).toBe('multi_select_align')
    useSelectionStore.getState().set(['p1', 'p2'])
    alignSelectedTo('top', 'selection')
    await tick()
    expect(ob().currentStep).toBe('export_canvas')

    // 原图范围留下的信号已被消费：直接开关面板不算
    useUiStore.getState().setExportOpen(true)
    emitActivity({ kind: 'export.scope_changed', scope: 'original' })
    useUiStore.getState().setExportOpen(false)
    await tick()
    expect(ob().currentStep).toBe('export_canvas')
    useUiStore.getState().setExportOpen(true)
    emitActivity({ kind: 'export.scope_changed', scope: 'canvas' })
    useUiStore.getState().setExportOpen(false)
    await tick()
    expect(ob().currentStep).toBe('done')
    expect(ob().status).toBe('active')
    completeStep('done')
    expect(ob().status).toBe('completed')
  })

  it('在画布模式到达 add_to_layout 时它直接完成（两张图本来就在画布上）', async () => {
    ob().goTo('add_to_layout')
    returnToLayout()
    await tick()
    expect(ob().currentStep).toBe('multi_select_align')
  })
})

describe('暂停与恢复', () => {
  it('切到别的项目 → 系统暂停；切回来 → 自动继续；用户暂停不会被自动继续', async () => {
    completeStep('welcome')
    useProjectStore.setState({ project: { open: true, id: 'p_other' } })
    await tick()
    expect(ob().status).toBe('paused')
    expect(ob().pausedBy).toBe('system')
    expect(inTutorial()).toBe(false)
    useProjectStore.setState({ project: { open: true, id: 'p_tut', tutorial: true } })
    await tick()
    expect(ob().status).toBe('active')
    ob().pause('user')
    evaluate()
    expect(ob().status).toBe('paused')
  })

  it('换成别的文档也算离开；不在教程里发生的动作不累计信号', async () => {
    completeStep('welcome')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    useUiStore.getState().setSelectedGid('axes_0.title')
    await tick()
    expect(ob().currentStep).toBe('change_typography')
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    await tick()
    expect(ob().status).toBe('paused')
    emitActivity({ kind: 'element.property_changed', prop: 'fontsize' })
    emitActivity({ kind: 'history.pushed', label: 'history.setProp' })
    await tick()
    expect(signalSnapshot()).toEqual(EMPTY_SIGNALS)
  })
})
