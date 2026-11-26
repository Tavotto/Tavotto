/**
 * 修改方案卡与弹窗（changePlan/）：两张助手卡的各个状态、弹窗的对比三模式与清单、失败卡的下一步、主按钮唯一（ADR 0206 §三例外）。
 * 图片在 jsdom 里不加载，只看结构与 `data-change-plan-*` 锚点。状态机与应用 / 撤销的细节在 `store/changePlanStore.test.ts`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ChangePlan, ReviewComment } from '@/lib/api'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/hooks/useRuntimePreview', () => ({ useRuntimePreview: () => ({ url: null, exact: false }) }))
const previewSnapshot = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPngSnapshot: (...a: unknown[]) => previewSnapshot(...a),
}))
const connectAi = vi.fn()
vi.mock('@/store/connectAiStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/connectAiStore')>()),
  requestConnectAi: (...a: unknown[]) => connectAi(...a),
}))

import { TooltipProvider } from '../ui/Tooltip'
import { ChangePlanCard } from './ChangePlanCard'
import { ChangePlanDialog } from './ChangePlanDialog'
import { ChangePlanTranscript } from './ChangePlanTranscript'
import { ReviewNoteCard } from './ReviewNoteCard'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const comment = (i: number, anchor: ReviewComment['anchor']): ReviewComment => ({
  id: `c${i}`,
  v: 1,
  anchor,
  author: i === 1 ? '林悦' : '王工',
  text: i === 1 ? '标题太小' : '图例挡住了曲线',
  resolved: false,
  created_at: '2026-11-23T10:00:00+08:00',
  replies: [],
})
const COMMENTS = [comment(1, { kind: 'region', box: [0.1, 0.1, 0.2, 0.2] }), comment(2, { kind: 'overall' }), comment(3, { kind: 'overall' })]
const PLAN: ChangePlan = {
  id: 'p1',
  version: 1,
  source: 'review',
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: [{ gid: 'ax.title', prop: 'fontsize', value: 14 }],
  groups: [
    {
      key: 'g1',
      label: '标题太小',
      comment_id: 'c1',
      atomic: false,
      changes: [{ target_gid: 'ax.title', prop: 'fontsize', before: 9, after: 14, patch: { gid: 'ax.title', prop: 'fontsize', value: 14 } }],
      boxes: { before: [{ gid: 'ax.title', box: [0.1, 0.1, 0.2, 0.2] }], after: [{ gid: 'ax.title', box: [0.1, 0.1, 0.3, 0.2] }] },
    },
  ],
  unhandled: [
    { comment_id: 'c2', reason: 'needs_script' },
    { comment_id: 'c3', reason: 'unclear' },
  ],
  preview: { before: '/b.png', after: '/a.png' },
}

const entry = (key: string, over: Partial<PlanEntry> = {}): PlanEntry => {
  const s = useDocumentStore.getState()
  return {
    key,
    pj: null,
    panelId: 'p1',
    fileId: 'f_p1',
    source: { fileId: 'f_p1', script: 'fig.py' },
    documentId: s.documentId,
    loadSeq: s.loadSeq,
    canvasId: s.activeCanvasId,
    createdAt: 1,
    status: 'ready',
    plan: PLAN,
    beforeSrc: '/b.png',
    afterSrc: '/a.png',
    unchecked: {},
    drafts: {},
    request: { comments: COMMENTS },
    comments: COMMENTS,
    commentCount: 3,
    ...over,
  }
}
const put = (...es: PlanEntry[]) =>
  act(() => useChangePlanStore.setState({ entries: Object.fromEntries(es.map((e) => [e.key, e])), order: es.map((e) => e.key), dialogKey: null }))

let host: HTMLDivElement
let root: Root
const render = (el: React.ReactNode) => act(() => root.render(<TooltipProvider>{el}</TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  connectAi.mockReset()
  previewSnapshot.mockReset()
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_change_plan_ui')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

it('审阅意见卡：条数、审阅者；整体意见不画圆点', () => {
  put(entry('a'))
  render(<ReviewNoteCard entry={useChangePlanStore.getState().entries.a} />)
  const card = q('[data-change-plan-note-card]')!
  expect(card.textContent).toContain('3')
  expect(card.textContent).toContain('林悦')
  expect(card.querySelectorAll('[data-review-mark]')).toHaveLength(1)
})

it('方案卡：生成中没有按钮；待确认「查看」实心 +「直接应用」次级，一句话说清改了几处与几条没改', () => {
  put(entry('a', { status: 'generating', plan: undefined, afterSrc: undefined, beforeSrc: undefined }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  const card = () => q('[data-change-plan-card]')!
  expect(card().dataset.changePlanCard).toBe('generating')
  // 生成中：图下面三步清单，图的位置是坐标纸上的作图小画；不再放思考小球（用户 2026-11-26）
  expect(qa('[data-task-step]').map((x) => x.dataset.stepState)).toEqual(['active', 'pending', 'pending'])
  expect(q('[data-plot-sketch]')!.dataset.plotSketch).toBe('read')
  expect(q('[data-thinking-orb]')).toBeNull()
  expect(card().querySelector('button')).toBeNull()
  expect(q('[data-change-plan-body]')!.className).toContain('min-h-21.5')

  put(entry('a'))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(card().dataset.changePlanCard).toBe('ready')
  expect([...card().querySelectorAll('button')].map((b) => b.dataset.variant)).toEqual(['primary', 'secondary'])
  // 出好：清单与作图小画收掉；正文区与生成中同一最小高度（卡片不缩短）
  expect(q('[data-task-step]')).toBeNull()
  expect(q('[data-plot-sketch]')).toBeNull()
  expect(q('[data-change-plan-body]')!.className).toContain('min-h-21.5')
  // 有一条要改脚本、一条没法直接改 → 「没法直接改」那一句
  expect(card().textContent).toContain('按 1 条意见改了 1 处，2 条没法直接改')
})

it('方案卡：已应用（还是我们写的那份才给「撤销」）· 已放弃 · 图已改过（基线对不上）', () => {
  put(entry('a', { status: 'applied', appliedPatches: PLAN.candidate_patches }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  // 图此刻还是 B0（⌘Z 撤回过）：卡片以文档为准，又是待确认
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('ready')
  act(() =>
    useDocumentStore.getState().commit(literal('应用'), (d) => {
      ;(d.objects.find((x) => x.id === 'p1') as PanelObject).overrides = PLAN.candidate_patches
    }),
  )
  // 重做回来 = 已应用；但栈顶不是记下的那次 commit 时不给「撤销」
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('applied')
  expect(q('[data-change-plan-undo]')).toBeNull()
  put(entry('a', { status: 'applied', appliedPatches: PLAN.candidate_patches, appliedEntry: useDocumentStore.getState().past.at(-1) }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-change-plan-undo]')).not.toBeNull()

  put(entry('a', { status: 'discarded' }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('discarded')
  expect(q('[data-change-plan-card]')!.querySelector('button')).toBeNull()

  // 待确认，但这张图被改过：自己转「图已改过」，主按钮是「重新生成」
  put(entry('a'))
  act(() =>
    useDocumentStore.getState().commit(literal('手改'), (d) => {
      ;(d.objects.find((x) => x.id === 'p1') as PanelObject).overrides = [{ gid: 'ax.title', prop: 'fontsize', value: 40 }]
    }),
  )
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('stale')
  expect(q('[data-change-plan-regenerate]')).not.toBeNull()
  expect(q('[data-change-plan-view]')).toBeNull()
})

it('失败卡：一句灰字 + 一个下一步（没连 AI → 连接 AI；额度 → 重试；没法改 → 收起）', () => {
  const fail = (code: string) => entry('a', { status: 'failed', plan: undefined, failure: { code } })
  put(fail('change_plan_no_connection'))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  const act1 = () => q('[data-change-plan-failed-action]')!
  expect(act1().dataset.changePlanFailedAction).toBe('connect')
  expect(q('[data-change-plan-card]')!.textContent).toContain('还没有连接 AI')
  act(() => act1().click())
  expect(connectAi).toHaveBeenCalledTimes(1)

  put(fail('change_plan_model_quota'))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(act1().dataset.changePlanFailedAction).toBe('retry')

  put(fail('change_plan_native_unsupported'))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(act1().dataset.changePlanFailedAction).toBe('dismiss')
  act(() => act1().click())
  expect(useChangePlanStore.getState().entries.a).toBeUndefined()
})

it('「没有一条意见能落成修改」：一句话 + 并列列出没处理的意见与原因', () => {
  put(entry('a', { status: 'failed', failure: { code: 'change_plan_empty' }, plan: { ...PLAN, groups: [] } }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  const card = q('[data-change-plan-card]')!
  expect(card.textContent).toContain('这些意见没法直接改图')
  expect(card.querySelectorAll('[data-change-plan-unhandled]')).toHaveLength(2)
  expect(card.textContent).toContain('图例挡住了曲线')
})

it('主按钮唯一：两张待确认卡，只有最新一张的「查看」实心，旧的降次级', () => {
  put(entry('a', { createdAt: 1 }), entry('b', { createdAt: 2 }))
  render(<ChangePlanTranscript panelId="p1" />)
  const views = qa('[data-change-plan-view]')
  expect(views.map((v) => v.dataset.variant)).toEqual(['secondary', 'primary'])
  expect(qa('[data-variant="primary"]')).toHaveLength(1)
})

it('弹窗：顶栏只有标题；清单逐组勾选、未处理灰字；应用 / 放弃在页脚；取消全部勾选禁用「应用」', () => {
  put(entry('a'))
  act(() => useChangePlanStore.getState().openDialog('a'))
  render(<ChangePlanDialog />)
  expect(q('[data-change-plan-dialog]')).not.toBeNull()
  // 没有批注 / 版本切换：顶栏没有分段控件，对比子切换只有「滑动 / 并排 / 切换」三个
  expect(qa('[role="radiogroup"]').map((g) => g.querySelectorAll('[role="radio"]').length)).toEqual([3])
  expect(qa('[data-change-group]')).toHaveLength(1)
  expect(qa('[data-change-plan-unhandled]')).toHaveLength(2)
  const apply = () => q('[data-change-plan-confirm]') as HTMLButtonElement
  expect(apply().dataset.variant).toBe('primary')
  expect(apply().disabled).toBe(false)
  act(() => (q('[data-change-group] input') as HTMLInputElement).click())
  expect(useChangePlanStore.getState().entries.a.unchecked.g1).toBe(true)
  expect(apply().disabled).toBe(true)
  act(() => (q('[data-change-plan-discard]') as HTMLButtonElement).click())
  expect(useChangePlanStore.getState().entries.a.status).toBe('discarded')
})

it('弹窗：滑动 / 并排 / 切换；按住空格看修改前只在「切换」；清单悬停才淡入对应细框', () => {
  put(entry('a'))
  act(() => useChangePlanStore.getState().openDialog('a'))
  render(<ChangePlanDialog />)
  const stage = () => q('[data-change-plan-stage]')!
  expect(stage().dataset.changePlanStage).toBe('slide')
  expect(q('[data-change-plan-divider]')).not.toBeNull()
  const modes = qa('[role="radio"]')
  const space = (type: 'keydown' | 'keyup') => act(() => void window.dispatchEvent(new KeyboardEvent(type, { code: 'Space', bubbles: true })))

  // 滑动里按空格：不换图、没有提示
  space('keydown')
  expect(stage().textContent).not.toContain('按住')
  space('keyup')

  act(() => modes[1].click())
  expect(stage().dataset.changePlanStage).toBe('side')
  expect(qa('[data-change-plan-frame]')).toHaveLength(2)

  act(() => modes[2].click())
  expect(stage().dataset.changePlanStage).toBe('toggle')
  expect(stage().textContent).toContain('按住')
  const shown = () => stage().querySelector('[data-change-plan-frame] img')!.getAttribute('src')
  expect(shown()).toBe('/a.png')
  space('keydown')
  expect(shown()).toBe('/b.png')
  space('keyup')
  expect(shown()).toBe('/a.png')

  // 细框默认不显（opacity-0），清单悬停那一组才亮
  const box = () => stage().querySelector<HTMLElement>('[data-change-plan-box="g1"]')!
  expect(box().className).toContain('opacity-0')
  act(() => void q('[data-change-group]')!.dispatchEvent(new PointerEvent('pointerover', { bubbles: true })))
  act(() => void q('[data-change-group]')!.dispatchEvent(new Event('focusin', { bubbles: true })))
  expect(box().dataset.active).toBe('true')
  expect(box().className).toContain('opacity-100')
})

const TWO: ChangePlan = {
  ...PLAN,
  groups: [
    PLAN.groups[0],
    { ...PLAN.groups[0], key: 'g2', comment_id: 'c2', changes: [{ target_gid: 'ax.legend', prop: 'loc', before: 'a', after: 'b', patch: { gid: 'ax.legend', prop: 'loc', value: 'upper left' } }] },
  ],
}
const stageAfter = () => q('[data-change-plan-stage] [data-change-plan-frame]')!.querySelectorAll('img')[1].getAttribute('src')

it('弹窗：取消勾选部分组后「修改后」按勾选组合出图（applyGroups 的结果），加载中保留旧图 + 轻遮罩；全选回到全量图', async () => {
  vi.useFakeTimers()
  try {
    let resolve!: (v: unknown) => void
    previewSnapshot.mockReturnValue(new Promise((r) => (resolve = r)))
    put(entry('a', { plan: TWO }))
    act(() => useChangePlanStore.getState().openDialog('a'))
    render(<ChangePlanDialog />)
    expect(stageAfter()).toBe('/a.png')
    act(() => (qa('[data-change-group] input')[1] as HTMLInputElement).click())
    // 防抖之前不发请求；旧图保留
    expect(previewSnapshot).not.toHaveBeenCalled()
    await act(async () => void (await vi.advanceTimersByTimeAsync(400)))
    expect(previewSnapshot).toHaveBeenCalledTimes(1)
    const [id, patches] = previewSnapshot.mock.calls[0]
    expect(id).toBe('f_p1')
    expect(patches).toEqual([{ gid: 'ax.title', prop: 'fontsize', value: 14 }])
    expect(stageAfter()).toBe('/a.png')
    expect(q('[data-change-plan-stage] [data-change-plan-frame] img:nth-of-type(2)')!.className).toContain('opacity-60')
    await act(async () => resolve({ url: 'data:image/png;base64,AAA', manifest: {} }))
    expect(stageAfter()).toBe('data:image/png;base64,AAA')
    // 应用时把这张图交给卡片
    act(() => (q('[data-change-plan-confirm]') as HTMLButtonElement).click())
    const e = useChangePlanStore.getState().entries.a
    expect(e.appliedPartial).toBe(true)
    expect(e.appliedSrc).toBe('data:image/png;base64,AAA')
  } finally {
    vi.useRealTimers()
  }
})

it('弹窗：按勾选出图失败 = 回退全量图 + 一句灰字；应用后的卡不挂全量图', async () => {
  vi.useFakeTimers()
  try {
    previewSnapshot.mockRejectedValue(new Error('x'))
    put(entry('a', { plan: TWO }))
    act(() => useChangePlanStore.getState().openDialog('a'))
    render(<ChangePlanDialog />)
    act(() => (qa('[data-change-group] input')[1] as HTMLInputElement).click())
    await act(async () => void (await vi.advanceTimersByTimeAsync(400)))
    expect(stageAfter()).toBe('/a.png')
    expect(q('[data-change-plan-preview-failed]')!.textContent).toContain('预览没更新')
    act(() => (q('[data-change-plan-confirm]') as HTMLButtonElement).click())
    const e = useChangePlanStore.getState().entries.a
    expect(e.appliedPartial).toBe(true)
    expect(e.appliedSrc).toBeUndefined()
    render(<ChangePlanCard entry={e} />)
    expect(q('[data-change-plan-card] img')!.getAttribute('src')).not.toBe('/a.png')
  } finally {
    vi.useRealTimers()
  }
})

it('替换素材后（面板 id 不变、overrides 为空）：待确认的方案转「图已改过」，且不应用', () => {
  put(entry('a', { plan: { ...PLAN, base_patches: [] }, source: { fileId: 'f_p1', script: 'fig.py' } }))
  act(() =>
    useDocumentStore.getState().commit(literal('替换素材'), (d) => {
      const o = d.objects.find((x) => x.id === 'p1') as PanelObject
      o.fileId = 'f_other'
      o.script = 'other.py'
      o.overrides = []
    }),
  )
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('stale')
  expect(useChangePlanStore.getState().apply('a')).toBe('stale')
  expect((useDocumentStore.getState().doc.objects.find((x) => x.id === 'p1') as PanelObject).overrides).toEqual([])
})

it('滑动对比：每个框按所在一侧取框（左 before、右 after），跨过分隔线的元素两侧各画各自那一段；把手在上三分之一', () => {
  const span: ChangePlan = {
    ...PLAN,
    groups: [{ ...PLAN.groups[0], boxes: { before: [{ gid: 'ax.title', box: [0.3, 0, 0.4, 0.2] }], after: [{ gid: 'ax.title', box: [0.3, 0, 0.5, 0.2] }] } }],
  }
  put(entry('a', { plan: span }))
  act(() => useChangePlanStore.getState().openDialog('a'))
  render(<ChangePlanDialog />)
  const boxes = qa('[data-change-plan-stage] [data-change-plan-box]')
  expect(boxes).toHaveLength(2)
  // 分隔线默认在 50%：左段 = before 框（宽 0.4）里线左的 50%，右段 = after 框（宽 0.5）里线右的 60%
  expect(boxes[0].style.width).toBe('40%')
  expect(boxes[0].style.clipPath).toBe('inset(0 50% 0 0)')
  expect(boxes[1].style.width).toBe('50%')
  expect(boxes[1].style.clipPath).toBe('inset(0 0 0 40%)')
  expect(q('[data-change-plan-divider] [role="slider"]')!.className).toContain('top-1/3')
})

it('并排：左图画改前的框、右图画改后的框', () => {
  put(entry('a'))
  act(() => useChangePlanStore.getState().openDialog('a'))
  render(<ChangePlanDialog />)
  act(() => qa('[role="radio"]')[1].click())
  const [l, r] = qa('[data-change-plan-frame]')
  expect(l.querySelector<HTMLElement>('[data-change-plan-box]')!.style.width).toBe('20%')
  expect(r.querySelector<HTMLElement>('[data-change-plan-box]')!.style.width).toBe('30%')
})

it('方案卡三步跟着后端阶段走：model → 第二步；render_after → 第三步、笔在描；改脚本那条第二步是「改脚本」', () => {
  const steps = () => qa('[data-task-step]').map((x) => `${x.dataset.taskStep}:${x.dataset.stepState}`)
  put(entry('a', { status: 'generating', plan: undefined, afterSrc: undefined, phase: 'model' }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(steps()).toEqual(['before:done', 'model:active', 'after:pending'])
  expect(q('[data-plot-sketch]')!.dataset.plotSketch).toBe('think')

  put(entry('a', { status: 'generating', plan: undefined, afterSrc: undefined, phase: 'render_after' }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(steps()).toEqual(['before:done', 'model:done', 'after:active'])
  expect(q('[data-plot-sketch]')!.dataset.plotSketch).toBe('draw')

  put(entry('a', { status: 'generating', plan: undefined, afterSrc: undefined, phase: 'agent' }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(steps()).toEqual(['before:done', 'agent:active', 'after:pending'])
})

it('按批注重改：卡片回到生成中，三步从头来；一键排版的整版卡没有阶段，照旧一句话', () => {
  put(entry('a', { revising: '', phase: undefined }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-change-plan-card]')!.dataset.changePlanCard).toBe('generating')
  expect(qa('[data-task-step]').map((x) => x.dataset.stepState)).toEqual(['active', 'pending', 'pending'])

  put(entry('a', { kind: 'layout', status: 'generating', plan: undefined, afterSrc: undefined }))
  render(<ChangePlanCard entry={useChangePlanStore.getState().entries.a} />)
  expect(q('[data-task-step]')).toBeNull()
  expect(q('[data-thinking-orb]')).not.toBeNull()
  expect(q('[data-change-plan-pending-actions]')).not.toBeNull()
})
