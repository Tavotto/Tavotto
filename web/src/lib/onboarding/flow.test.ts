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
import { migratePersisted } from '@/store/onboardingStore'
import {
  completeStep,
  currentContext,
  evaluate,
  inTutorial,
  resetSignalsForTest,
  signalSnapshot,
  skipStep,
  startOnboardingEngine,
} from './flow'
import { REAL_STEP_IDS, type StepId } from './stepIds'
import {
  buildContext,
  EMPTY_SIGNALS,
  missingTutorialPanels,
  problemsResolved,
  STEP_TABLE_MATCHES_IDS,
  STEPS,
  stepById,
  TYPOGRAPHY_PROPS_FIGURE,
} from './steps'
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

/** 一步的前置结论（不满足时带原因） */
const pre = (id: StepId) => stepById(id).precondition?.(currentContext()) ?? { ok: true }
const reasonOf = (id: StepId) => {
  const p = pre(id)
  return p.ok ? null : p.reason
}
const runAction = (id: StepId) => {
  const p = pre(id)
  if (p.ok || !p.action) throw new Error(`step ${id} has no precondition action`)
  p.action.run()
}

describe('前置状态先验：缺了就说清并给真实行动，不「等待」', () => {
  it('select_text / change_typography / locate_problem：还在画布模式时说「要在图内编辑里」，按钮打开那张图', async () => {
    completeStep('welcome')
    // 跳过第 1 步：人还在画布模式
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    for (const id of ['select_text', 'change_typography'] as const) {
      const p = pre(id)
      expect(p.ok).toBe(false)
      if (p.ok) continue
      expect(p.reason).toBe('notInElementEdit')
      expect(p.values).toEqual({ name: 'Fig2_correlation' })
      expect(p.action?.key).toBe('openFigure')
      // 装置里素材抽屉开着：指那张卡（选择器经 CSS.escape，点号带转义）
      expect(p.anchor).toEqual({ kind: 'selector', selector: `[data-card="${CSS.escape('Fig2_correlation.pdf')}"]` })
    }
    // 行动按钮 = 稳定动作 openFastEdit：进了 Fig2 的图内编辑，前置随之满足
    runAction('select_text')
    await tick()
    expect(useUiStore.getState().elementPanelId).toBe('p2')
    expect(pre('select_text').ok).toBe(true)
    // 第 3 步还要选中一段文字：没选时说清，按钮替用户选首选的文字元素（标题）
    expect(reasonOf('change_typography')).toBe('noTextSelected')
    expect(pre('change_typography')).toMatchObject({ action: { key: 'selectText' } })
    runAction('change_typography')
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.title'])
    expect(pre('change_typography').ok).toBe(true)
    // 选了曲线也不算文字
    useUiStore.getState().setSelectedGid('axes_0.lines_0')
    expect(reasonOf('change_typography')).toBe('noTextSelected')
  })

  it('locate_problem：那张图没渲染过 → 问题面板里不会有它的问题，先打开它一次', async () => {
    completeStep('welcome')
    useRenderStore.getState().clear()
    expect(currentContext().elements).toBeNull()
    expect(reasonOf('locate_problem')).toBe('editPanelNotRendered')
    expect(pre('locate_problem')).toMatchObject({ action: { key: 'openFigure', values: { name: 'Fig2_correlation' } } })
    const p2 = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p2') as PanelObject
    seedExactRender(p2, manifest)
    expect(pre('locate_problem').ok).toBe(true)
  })

  it('要编辑的那张图不在文档里：说「不在这份画布里」，按钮把它打开（经素材表加进来）', async () => {
    completeStep('welcome')
    const s = useDocumentStore.getState()
    useDocumentStore.setState({ doc: { ...s.doc, objects: s.doc.objects.filter((o) => o.id !== 'p2') } })
    expect(currentContext().edit).toBeNull()
    expect(reasonOf('select_text')).toBe('editPanelMissing')
    expect(reasonOf('locate_problem')).toBe('editPanelMissing')
    runAction('select_text')
    await tick()
    const ctx = currentContext()
    expect(ctx.edit?.meta.key).toBe('second')
    expect(ctx.elementPanelId).toBe(ctx.edit?.panel.id)
  })

  it('multi_select_align：在快速编辑里说「要回到画布」，按钮回排版；只剩一张时说「先加 Fig1_kinetics」', async () => {
    completeStep('welcome')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    expect(reasonOf('multi_select_align')).toBe('notInLayout')
    expect(pre('multi_select_align')).toMatchObject({
      action: { key: 'returnToLayout' },
      anchor: { kind: 'selector', selector: '[data-onboarding-anchor="to-layout"]' },
    })
    runAction('multi_select_align')
    expect(useWorkspaceStore.getState().mode).toBe('layout')
    expect(pre('multi_select_align').ok).toBe(true)
    // 删掉 Fig1：缺一张
    const s = useDocumentStore.getState()
    useDocumentStore.setState({ doc: { ...s.doc, objects: s.doc.objects.filter((o) => o.id !== 'p1') } })
    expect(missingTutorialPanels(currentContext()).map((m) => m.stem)).toEqual(['Fig1_kinetics'])
    expect(pre('multi_select_align')).toMatchObject({
      reason: 'otherPanelMissing',
      values: { name: 'Fig1_kinetics' },
      action: { key: 'addToLayout', values: { name: 'Fig1_kinetics' } },
    })
    runAction('multi_select_align')
    expect(currentContext().tutorialPanelIds.size).toBe(2)
    expect(pre('multi_select_align').ok).toBe(true)
  })

  it('没有元数据时不下结论（那是「等待」的合法窗口）', () => {
    completeStep('welcome')
    useTutorialStore.setState({ meta: null })
    for (const id of ['select_text', 'change_typography', 'locate_problem', 'multi_select_align'] as const) {
      expect(pre(id).ok).toBe(true)
    }
  })
})

describe('add_to_layout 按文档里实际有几张图说话', () => {
  it('只剩一张：变体说「还缺 Fig1_kinetics」、锚点指素材、按钮把它加进来；加完回到原文案并完成', async () => {
    completeStep('welcome')
    const s = useDocumentStore.getState()
    useDocumentStore.setState({ doc: { ...s.doc, objects: s.doc.objects.filter((o) => o.id !== 'p1') } })
    ob().goTo('add_to_layout')
    await tick()
    const def = stepById('add_to_layout')
    let ctx = currentContext()
    expect(def.done(ctx)).toBe(false)
    expect(def.variant?.(ctx)).toBe('add_to_layout.missing')
    expect(def.values?.(ctx)).toEqual({ name: 'Fig1_kinetics' })
    expect(def.anchor(ctx)).toEqual({ kind: 'selector', selector: `[data-card="${CSS.escape('Fig1_kinetics.pdf')}"]` })
    const action = def.action?.(ctx)
    expect(action?.key).toBe('addToLayout')
    action!.run()
    await tick()
    ctx = currentContext()
    expect(ctx.tutorialPanelIds.size).toBe(2)
    expect(def.variant?.(ctx)).toBe('add_to_layout')
    expect(def.action?.(ctx)).toBeNull()
    // 两张都在、人在版面 → 引擎把这一步判完
    expect(ob().currentStep).toBe('multi_select_align')
  })

  it('两张都在但在快速编辑里：锚点是快速编辑条的「添加到画布」，没有多余的按钮', async () => {
    completeStep('welcome')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    const def = stepById('add_to_layout')
    const ctx = currentContext()
    expect(def.variant?.(ctx)).toBe('add_to_layout')
    expect(def.anchor(ctx)).toEqual({ kind: 'selector', selector: '[data-onboarding-anchor="add-to-layout"]' })
    expect(def.action?.(ctx)).toBeNull()
  })
})

describe('四条结束路径：完成与跳过分别记账，结束页按账说话', () => {
  const doneDef = stepById('done')
  const outcomesNow = () => currentContext().outcomes

  it('全做：结束页是「完成」变体，跳过 0', async () => {
    completeStep('welcome')
    for (const id of REAL_STEP_IDS) completeStep(id)
    expect(ob().currentStep).toBe('done')
    expect(outcomesNow()).toEqual({ done: REAL_STEP_IDS.length, skipped: 0, total: REAL_STEP_IDS.length })
    expect(doneDef.variant?.(currentContext())).toBe('done')
    completeStep('done')
    expect(ob().status).toBe('completed')
    expect(ob().skippedSteps).toEqual([])
  })

  it('全跳：每一步都能跳、结束页说「跳过了全部」，不用完成式', async () => {
    completeStep('welcome')
    for (let i = 0; i < REAL_STEP_IDS.length; i++) {
      expect(stepById(ob().currentStep!).manual).toBeFalsy()
      skipStep()
    }
    expect(ob().currentStep).toBe('done')
    expect(outcomesNow()).toEqual({ done: 0, skipped: REAL_STEP_IDS.length, total: REAL_STEP_IDS.length })
    expect(doneDef.variant?.(currentContext())).toBe('done.allSkipped')
    expect(doneDef.values?.(currentContext())).toEqual(outcomesNow())
    completeStep('done')
    expect(ob().status).toBe('completed')
    // 结束之后那本账还在：入口 / 设置页说得出这轮是跳完的
    expect(ob().skippedSteps).toEqual([...REAL_STEP_IDS])
  })

  it('部分完成后退出再进入：进度与两本账都在，继续到结束页说「完成 n 步，跳过 m 步」', async () => {
    completeStep('welcome')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    expect(ob().completedSteps).toContain('open_fast_edit')
    // 用户关掉 coachmark = 暂停；本机那格的往返（迁移）保住两本账
    ob().pause('user')
    const persisted = migratePersisted(JSON.parse(JSON.stringify(ob())))
    expect(persisted.status).toBe('paused')
    expect(persisted.currentStep).toBe('select_text')
    expect(persisted.completedSteps).toEqual(['welcome', 'open_fast_edit'])
    expect(persisted.skippedSteps).toEqual([])
    ob().resume()
    expect(ob().currentStep).toBe('select_text')
    // 剩下的全跳
    while (ob().currentStep !== 'done') skipStep()
    expect(outcomesNow()).toEqual({ done: 1, skipped: REAL_STEP_IDS.length - 1, total: REAL_STEP_IDS.length })
    expect(doneDef.variant?.(currentContext())).toBe('done.partial')
    completeStep('done')
    expect(ob().status).toBe('completed')
  })

  it('中途切走项目再回来：系统暂停 → 自动继续 → 能走到结束', async () => {
    completeStep('welcome')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    useProjectStore.setState({ project: { open: true, id: 'p_other' } })
    await tick()
    expect(ob().status).toBe('paused')
    useProjectStore.setState({ project: { open: true, id: 'p_tut', tutorial: true } })
    await tick()
    expect(ob().status).toBe('active')
    expect(ob().currentStep).toBe('select_text')
    while (ob().currentStep !== 'done') skipStep()
    completeStep('done')
    expect(ob().status).toBe('completed')
    expect(outcomesNow().done).toBe(1)
  })

  it('返回再真的做完一步：从跳过那本账里移出', async () => {
    completeStep('welcome')
    skipStep() // open_fast_edit
    expect(ob().skippedSteps).toEqual(['open_fast_edit'])
    ob().back()
    expect(ob().currentStep).toBe('open_fast_edit')
    openFastEdit('Fig2_correlation.pdf')
    await tick()
    expect(ob().currentStep).toBe('select_text')
    expect(ob().skippedSteps).toEqual([])
    expect(outcomesNow().done).toBe(1)
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
    // 这两条没有第二道守卫（它们不看正在编辑哪张图）：教程外开导出面板确认原图，
    // 回来之后不能被当成刚做的
    useUiStore.getState().setExportOpen(true)
    emitActivity({ kind: 'export.scope_changed', scope: 'original' })
    emitActivity({ kind: 'export.scope_changed', scope: 'canvas' })
    useUiStore.getState().setExportOpen(false)
    await tick()
    expect(signalSnapshot()).toEqual(EMPTY_SIGNALS)
  })
})
