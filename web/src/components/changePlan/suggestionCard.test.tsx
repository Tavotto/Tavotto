/**
 * 建议待办卡的画法（ADR 0214 §三、§四）：回答是助手正文（不是气泡）+「建议」卡；一行一个勾选（默认全勾、整行可点）、「为什么」一行小灰字、
 * 脚本待办行尾小标签；主按钮写数量、全取消禁用成「一项都没勾」；「对比原图」只在有图内修改时出现；落定后收成一行 + 撤销 + 「没改：…」。
 * 图片在 jsdom 里不加载，只看结构与 `data-*` 锚点。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { ChangePlan } from '@/lib/api'
import { useChangePlanStore, type PlanEntry } from '@/store/changePlanStore'
import { useDocumentStore } from '@/store/documentStore'
import { useJournalPickerRequest } from '@/store/journalPickerRequest'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/hooks/useVariantPng', () => ({ useVariantPng: () => ({ url: null, loading: false, approximate: false }), variantNeedsRender: () => false }))
vi.mock('@/hooks/useRuntimePreview', () => ({ useRuntimePreview: () => ({ url: null, exact: false }) }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPngSnapshot: vi.fn(async () => new Promise(() => undefined)),
}))

import { TooltipProvider } from '../ui/Tooltip'
import { ChangePlanTranscript } from './ChangePlanTranscript'

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
const g = (key: string, cid: string, gid: string) => ({
  key,
  label: key,
  comment_id: cid,
  atomic: false,
  changes: [{ target_gid: gid, prop: 'linewidth', before: 1, after: 3, patch: { gid, prop: 'linewidth', value: 3 } }],
  boxes: { after: [{ gid, box: [0.1, 0.1, 0.2, 0.2] as [number, number, number, number] }] },
})
const PLAN: ChangePlan = {
  id: 'p1',
  version: 1,
  source: 'assistant',
  reply: '线偏细，还可以加误差线。',
  todos: [
    { key: 't1', label: '线条加粗', why: '远看不清', kind: 'figure', group_key: 'g1' },
    { key: 't2', label: '图例挪到左上角', kind: 'figure', group_key: 'g2' },
    { key: 't3', label: '加误差线（SD）', why: '要看离散度', kind: 'script', comment_id: 'a3' },
  ],
  base: { target: 'f_p1', fingerprint: 'fp' },
  base_patches: BASE,
  candidate_patches: BASE,
  groups: [g('g1', 'a1', 'ax.line'), g('g2', 'a2', 'ax.legend')],
  unhandled: [{ comment_id: 'a3', reason: 'needs_script' }],
  preview: { before: '/b.png', after: '/a.png' },
}
const entry = (key: string, over: Partial<PlanEntry> = {}): PlanEntry => {
  const s = useDocumentStore.getState()
  return {
    key,
    from: 'assistant',
    instruction: '这张图有什么可以改进的',
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
    request: { instruction: '这张图有什么可以改进的' },
    comments: [],
    commentCount: 0,
    ...over,
  }
}
const apply = vi.fn()
const put = (...es: PlanEntry[]) =>
  act(() => useChangePlanStore.setState({ entries: Object.fromEntries(es.map((e) => [e.key, e])), order: es.map((e) => e.key), dialogKey: null, applyTodos: apply as never }))

let host: HTMLDivElement
let root: Root
const render = () => act(() => root.render(<TooltipProvider><ChangePlanTranscript panelId="p1" /></TooltipProvider>))
const q = (sel: string) => document.body.querySelector<HTMLElement>(sel)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]

beforeEach(async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  apply.mockReset().mockResolvedValue('applied')
  useChangePlanStore.setState({ entries: {}, order: [], dialogKey: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_suggestion_card')
  useDocumentStore.getState().commit(literal('放入'), (d) => {
    d.objects.push({ id: 'p1', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f_p1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides: BASE } as PanelObject)
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

it('回答是助手正文，卡叫「建议」；一行一个勾选，默认全勾；「为什么」一行小灰字；脚本待办带「要改脚本」标签（悬停有说明）', async () => {
  put(entry('a'))
  await render()
  expect(q('[data-change-plan-reply]')?.textContent).toContain('线偏细')
  const card = q('[data-change-plan-suggestion]')!
  expect(card.textContent).toContain('建议')
  const rows = qa('[data-suggestion-todo]')
  expect(rows.map((r) => r.textContent)).toEqual(['线条加粗·远看不清', '图例挪到左上角', '加误差线（SD）·要看离散度'])
  expect(q('[data-suggestion-count]')?.textContent).toBe('3 项')
  expect(rows[2].querySelector('[title]')?.getAttribute('title')).toBe('加误差线（SD） · 要看离散度')
  expect(qa('[data-suggestion-todo] input[type=checkbox]').every((c) => (c as HTMLInputElement).checked)).toBe(true)
  const section = q('[data-suggestion-script-section]')!
  expect(section.textContent).toBe('要改脚本')
  expect(section.getAttribute('title')).toBe('这几项要改图的 Python 脚本，改前会备份，可以撤销')
  expect(qa('[data-suggestion-script-section]')).toHaveLength(1)
  // 分组小标题在图内几行之后、脚本几行之前；对比原图在标题行右侧
  expect(section.previousElementSibling?.querySelector('[data-suggestion-todo="t2"]')).toBeTruthy()
  expect(section.nextElementSibling?.querySelector('[data-suggestion-todo="t3"]')).toBeTruthy()
  expect(q('[data-change-plan-body] [data-change-plan-view]')).toBeNull()
  expect(q('[data-suggestion-apply]')?.textContent).toBe('改这 3 项')
  expect(q('[data-change-plan-view]')?.textContent).toBe('对比原图')
})

it('点整行就是勾 / 取消：图内待办进逐组勾选，按钮上的数量跟着；全取消禁用成「一项都没勾」', async () => {
  put(entry('a'))
  await render()
  await act(async () => q('[data-suggestion-todo="t1"]')!.click())
  expect(useChangePlanStore.getState().entries.a.unchecked).toEqual({ g1: true })
  expect(q('[data-suggestion-apply]')?.textContent).toBe('改这 2 项')
  await act(async () => q('[data-suggestion-todo="t3"]')!.click())
  expect(useChangePlanStore.getState().entries.a.todoOff).toEqual({ t3: true })
  await act(async () => q('[data-suggestion-todo="t2"]')!.click())
  const btn = q('[data-suggestion-apply]') as HTMLButtonElement
  expect(btn.textContent).toBe('一项都没勾')
  expect(btn.disabled).toBe(true)
})

it('点「改这 N 项」走一键链路', async () => {
  put(entry('a'))
  await render()
  await act(async () => q('[data-suggestion-apply]')!.click())
  expect(apply).toHaveBeenCalledWith('a')
})

it('没有图内修改（只有脚本待办）：不出「对比原图」', async () => {
  put(entry('a', { plan: { ...PLAN, groups: [], todos: [PLAN.todos![2]] } }))
  await render()
  expect(q('[data-change-plan-view]')).toBeNull()
  expect(q('[data-suggestion-apply]')?.textContent).toBe('改这 1 项')
})

it('脚本方案在跑：按钮区只剩一行「正在改…」，勾选冻住', async () => {
  const script = entry('s', { plan: { ...PLAN, kind: 'script' } as ChangePlan, status: 'generating', todoOf: 'a', from: undefined })
  put(entry('a', { todoRun: { figure: [], script: ['t3'], scripts: [{ todo: 't3', key: 's' }] } }), script)
  await render()
  expect(q('[data-suggestion-running]')?.textContent).toContain('正在改')
  expect(q('[data-suggestion-apply]')).toBeNull()
  // 执行中：勾着的行在复选框的位置显示进度（脚本待办转圈），不整体变灰
  expect(q('[data-suggestion-todo="t3"] [data-todo-progress]')?.getAttribute('data-todo-progress')).toBe('active')
  expect(q('[data-suggestion-todo="t3"] input')).toBeNull()
  expect(q('[data-suggestion-running]')?.textContent).toContain('0/3')
  // 自动续出的脚本方案卡在生成 / 已应用时不单独画
  expect(qa('[data-change-plan-card]')).toHaveLength(1)
})

it('落定：收成一行「已按建议改了 N 项」，没改的写在下面一行灰字', async () => {
  const h = useDocumentStore.getState()
  const panel = h.doc.objects[0] as PanelObject
  put(entry('a', { status: 'applied', appliedPatches: panel.overrides, plan: { ...PLAN, base_patches: panel.overrides }, unchecked: { g2: true }, todoOff: { t3: true }, todoRun: { figure: ['t1'], script: [], scripts: [] } }))
  await render()
  expect(q('[data-suggestion-done]')?.textContent).toBe('已按建议改了 1 项')
  expect(q('[data-suggestion-skipped]')?.textContent).toBe('没改：图例挪到左上角和加误差线（SD）')
  expect(q('[data-suggestion-list]')).toBeNull()
})

it('只有回答（answered）：只有助手正文，没有卡', async () => {
  put(entry('a', { status: 'answered', plan: { ...PLAN, todos: undefined, groups: [] } }))
  await render()
  expect(q('[data-change-plan-reply]')?.textContent).toContain('线偏细')
  expect(q('[data-change-plan-card]')).toBeNull()
})

it('脚本方案要问改数据：问句与两个按钮在建议卡脚底（不单画脚本方案卡），回答记在脚本方案上', async () => {
  const sp = { ...PLAN, kind: 'script', groups: [{ ...g('script', 'a1', 'x'), kind: 'script', confirm: { kind: 'script_data_changed', parts: [] } }] } as unknown as ChangePlan
  const answer = vi.fn()
  const script = entry('s', { plan: sp, status: 'ready', todoOf: 'a', from: undefined })
  put(entry('a', { todoRun: { figure: [], script: ['t3'], scripts: [{ todo: 't3', key: 's' }] } }), script)
  act(() => useChangePlanStore.setState({ answerConfirm: answer as never }))
  await render()
  expect(qa('[data-change-plan-card]')).toHaveLength(1)
  expect(q('[data-change-plan-confirm-data-ask]')?.textContent).toBe('这会改动图里画的数据，要应用吗？')
  expect(q('[data-change-plan-confirm-data="yes"]')?.textContent).toBe('就这样改')
  expect(q('[data-change-plan-confirm-data="no"]')?.textContent).toBe('先不改')
  expect(q('[data-suggestion-script-section]')).toBeTruthy()
  await act(async () => q('[data-change-plan-confirm-data="yes"]')!.click())
  expect(answer).toHaveBeenCalledWith('s', 'script', 'yes')
})

it('答了「先不改」/ 没做成的脚本方案卡都不单画：原因一条一句并进建议卡；只有越界那张（带恢复）还单独出现', async () => {
  const declined = entry('s', { plan: { ...PLAN, kind: 'script' } as ChangePlan, status: 'failed', failure: { code: 'change_plan_script_data_declined' }, todoOf: 'a', from: undefined })
  put(entry('a', { todoRun: { figure: [], script: ['t3'], scripts: [{ todo: 't3', key: 's' }] } }), declined)
  await render()
  expect(qa('[data-change-plan-card]')).toHaveLength(1)
  expect(q('[data-suggestion-fail]')).toBeNull()
  // Agent 没改、回了话：建议卡里一句「…没做成：<它的话>」，不另出卡（维护者 2026-11-27「并进去吧」）
  put(
    entry('a', { todoRun: { figure: [], script: ['t3'], scripts: [{ todo: 't3', key: 's' }] } }),
    { ...declined, failure: { code: 'change_plan_script_no_change', params: { agent_reply: '脚本里没有这些数值，没法补。\n\n第二段不显示' } } },
  )
  await render()
  expect(qa('[data-change-plan-card]')).toHaveLength(1)
  const line = q('[data-suggestion-fail="t3"]')
  expect(line?.textContent).toContain('没做成：脚本里没有这些数值，没法补。')
  expect(line?.textContent).not.toContain('第二段')
  // 越界：那张带「恢复到开始前的版本」的卡照旧单独出现
  put(entry('a', { todoRun: { figure: [], script: ['t3'], scripts: [{ todo: 't3', key: 's' }] } }), { ...declined, failure: { code: 'change_plan_script_overreach', params: { files: ['x.py'], total: 1 } } })
  await render()
  expect(qa('[data-change-plan-card]')).toHaveLength(2)
})

it('图内已改、脚本在等回答：图内行是勾（ok 色）、脚本行是点状圈', async () => {
  const h = useDocumentStore.getState()
  const panel = h.doc.objects[0] as PanelObject
  const sp = { ...PLAN, kind: 'script', groups: [{ ...g('script', 'a1', 'x'), kind: 'script', confirm: { kind: 'script_data_changed', parts: [] } }] } as unknown as ChangePlan
  put(
    entry('a', { status: 'applied', appliedPatches: panel.overrides, plan: { ...PLAN, base_patches: panel.overrides }, todoRun: { figure: ['t1', 't2'], script: ['t3'], scripts: [{ todo: 't3', key: 's' }] } }),
    entry('s', { plan: sp, status: 'ready', todoOf: 'a', from: undefined }),
  )
  await render()
  expect(q('[data-suggestion-todo="t1"]')?.getAttribute('data-todo-state')).toBe('done')
  expect(q('[data-suggestion-todo="t3"]')?.getAttribute('data-todo-state')).toBe('pending')
})

it('落定那一行点一下展开只读的已改清单（没有复选框），再点收起；「没改」一行缩进对齐', async () => {
  const h = useDocumentStore.getState()
  const panel = h.doc.objects[0] as PanelObject
  put(entry('a', { status: 'applied', appliedPatches: panel.overrides, plan: { ...PLAN, base_patches: panel.overrides }, unchecked: { g2: true }, todoOff: { t3: true }, todoRun: { figure: ['t1'], script: [], scripts: [] } }))
  await render()
  expect(q('[data-suggestion-landed]')).toBeNull()
  await act(async () => q('[data-suggestion-done-toggle]')!.click())
  expect(qa('[data-suggestion-landed-row]').map((r) => r.textContent)).toEqual(['线条加粗·远看不清'])
  expect(q('[data-suggestion-landed] input')).toBeNull()
  await act(async () => q('[data-suggestion-done-toggle]')!.click())
  expect(q('[data-suggestion-landed]')).toBeNull()
})

// --------------------------------------------------------------------------- 回答下的按钮 / 再加一条 / 执行中跳过 / Agent 的话
const setField = (el: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}
const key = (el: HTMLElement, k: string) => el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }))

it('回答（answered）：助手正文 + 一个小按钮「把这些建议写成待办」，点了发请求；点过之后按钮不再出现', async () => {
  const request = vi.fn().mockResolvedValue('k2')
  put(entry('a', { status: 'answered', plan: { ...PLAN, todos: undefined, groups: [] } }))
  act(() => useChangePlanStore.setState({ requestTodos: request as never }))
  await render()
  const btn = q('[data-change-plan-to-todos]')!
  expect(btn.textContent).toBe('把这些建议写成待办')
  await act(async () => btn.click())
  expect(request).toHaveBeenCalledWith('a')
  put(entry('a', { status: 'answered', todosKey: 'k2', plan: { ...PLAN, todos: undefined, groups: [] } }))
  expect(q('[data-change-plan-to-todos]')).toBeNull()
})

it('回答里提到期刊 / 投稿、且还没选期刊：多一个「选期刊」，点了打开期刊选择器；没提到 / 已选期刊 / 已点过待办之外的条件都不出现', async () => {
  const opened = vi.spyOn(await import('@/store/journalPickerRequest'), 'openJournalPicker')
  const answered = { status: 'answered' as const, plan: { ...PLAN, todos: undefined, groups: [] } }
  put(entry('a', { ...answered, instruction: '这张图符合投稿期刊的要求吗' }))
  await render()
  const btn = q('[data-change-plan-pick-journal]')!
  expect(btn.textContent).toBe('选期刊')
  await act(async () => btn.click())
  expect(opened).toHaveBeenCalledOnce()
  expect(useUiStore.getState().leftTab).toBe('problems')
  expect(useJournalPickerRequest.getState().pending).toBe(true)
  // 没提到期刊
  put(entry('a', { ...answered, instruction: '这张图有什么可以改进的' }))
  await render()
  expect(q('[data-change-plan-pick-journal]')).toBeNull()
  // 已选期刊
  put(entry('a', { ...answered, instruction: 'journal requirements?' }))
  await render()
  expect(q('[data-change-plan-pick-journal]')).not.toBeNull()
  act(() =>
    useDocumentStore.getState().commit(literal('选期刊'), (d) => {
      d.profile = { id: 'x', journalRef: { id: 'j', catalogRev: 1, retrieved: '', label: 'J' } }
    }),
  )
  expect(q('[data-change-plan-pick-journal]')).toBeNull()
})

it('「＋ 再加一条…」：点开是一行输入框，回车提交，Esc 取消；提交后这一行是想一想 + 那句话', async () => {
  const add = vi.fn().mockResolvedValue(true)
  put(entry('a'))
  act(() => useChangePlanStore.setState({ addTodo: add as never }))
  await render()
  expect(q('[data-suggestion-add]')?.getAttribute('title')).toContain('再加一条')
  await act(async () => q('[data-suggestion-add]')!.click())
  const input = q('[data-suggestion-add-input]') as HTMLInputElement
  expect(input.placeholder).toBe('比如：图例去掉边框')
  expect(document.activeElement).toBe(input)
  await act(async () => key(input, 'Escape'))
  expect(q('[data-suggestion-add-input]')).toBeNull()
  await act(async () => q('[data-suggestion-add]')!.click())
  const again = q('[data-suggestion-add-input]') as HTMLInputElement
  await act(async () => setField(again, ' 图例去掉边框 '))
  await act(async () => key(again, 'Enter'))
  expect(add).toHaveBeenCalledWith('a', '图例去掉边框')
  put(entry('a', { adding: 'p3', addingText: '图例去掉边框' }))
  expect(q('[data-suggestion-adding]')?.textContent).toBe('图例去掉边框')
  expect(q('[data-suggestion-add]')).toBeNull()
})

it('新加的几行带「新加」标签；点过「改这 N 项」之后 / 执行中 / 满 12 条不再出现「再加一条」', async () => {
  put(entry('a', { plan: { ...PLAN, todos: [...PLAN.todos!, { key: 't4', label: '图例去掉边框', kind: 'figure', group_key: 'g2', added: true }] } }))
  await render()
  expect(qa('[data-suggestion-new-tag]')).toHaveLength(1)
  expect(q('[data-suggestion-new-tag]')?.getAttribute('title')).toBe('新加')
  put(entry('a', { todoRun: { figure: [], script: ['t3'], scripts: [] } }))
  expect(q('[data-suggestion-add]')).toBeNull()
  const many = Array.from({ length: 12 }, (_, i) => ({ key: `t${i + 1}`, label: `第${i + 1}条`, kind: 'script' as const, comment_id: `a${i + 1}` }))
  put(entry('a', { plan: { ...PLAN, todos: many, groups: [] } }))
  expect(q('[data-suggestion-add]')).toBeNull()
})

it('执行中还没轮到的脚本待办仍是能勾的复选框；做过 / 在做的是进度图标', async () => {
  const two = { ...PLAN, groups: [], todos: [PLAN.todos![2], { key: 't4', label: '换成对数坐标', kind: 'script' as const, comment_id: 'a4' }] }
  const running = entry('s', { plan: { ...PLAN, kind: 'script' } as ChangePlan, status: 'generating', todoOf: 'a', from: undefined })
  put(entry('a', { plan: two, todoRun: { figure: [], script: ['t3', 't4'], scripts: [{ todo: 't3', key: 's' }] } }), running)
  await render()
  expect(q('[data-suggestion-todo="t3"]')?.getAttribute('data-todo-state')).toBe('active')
  expect(q('[data-suggestion-todo="t4"]')?.getAttribute('data-todo-state')).toBe('queued')
  const box = q('[data-suggestion-todo="t4"] input') as HTMLInputElement
  expect(box.disabled).toBe(false)
  await act(async () => box.click())
  expect(useChangePlanStore.getState().entries.a.todoOff).toEqual({ t4: true })
  expect(q('[data-suggestion-running]')?.textContent).toContain('0/1') // 跳过的不再算进总数
})

it('脚本方案里 Agent 没改脚本但回了话：失败卡显示它的话（不只说「Agent 没有改动脚本」）', async () => {
  put(entry('f', { from: undefined, plan: { ...PLAN, kind: 'script' } as ChangePlan, status: 'failed', failure: { code: 'change_plan_script_no_change', params: { agent_reply: '1. 线偏细\n2. 标题偏小' } } }))
  await render()
  const said = q('[data-change-plan-agent-reply]')
  expect(said?.textContent).toContain('线偏细')
  expect(q('[data-change-plan-card="failed"]')?.textContent).not.toContain('Agent 没有改动脚本')
  put(entry('f', { from: undefined, plan: { ...PLAN, kind: 'script' } as ChangePlan, status: 'failed', failure: { code: 'change_plan_script_no_change' } }))
  expect(q('[data-change-plan-agent-reply]')).toBeNull()
  expect(q('[data-change-plan-card="failed"]')?.textContent).toContain('Agent 没有改动脚本')
})
