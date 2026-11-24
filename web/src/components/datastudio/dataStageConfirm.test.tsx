/**
 * 一张主图 + 一排切换（`CandidateStage`）与右栏「需要你确认」（`DataPanel`）：
 * 切换只是预览（不调 onCreate / draw），点「创建可编辑图」才创建；依据句；1 张不显示切换排；
 * 确认两行的出现条件、选了之后写进请求的 answers 并从小标题下移走。
 */
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { aiDataFigurePropose, type AiDataProposeResponse } from '@/lib/api'
import { candidateIdentity, candidatesFromResponse } from '@/lib/dataFigureAdapter'
import { setCurrentProjectId } from '@/lib/session'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { CandidateStage } from './CandidateStage'
import { DataPanel } from './DataPanel'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiDataFigurePropose: vi.fn(),
  aiDataFigureDiscard: vi.fn(async () => ({ ok: true })),
}))
vi.mock('@/store/liveSync', () => ({ refreshProjectNow: vi.fn(async () => {}) }))
const propose = vi.mocked(aiDataFigurePropose)

let root: Root
let host: HTMLDivElement
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)
const qa = <T extends Element = HTMLElement>(sel: string) => [...document.querySelectorAll<T>(sel)]
const click = async (el: Element | null) => {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })
const mount = async (ui: React.ReactNode) => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{ui}</TooltipProvider>))
}
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  useDataFigureStore.getState().clear()
})

const item = (id: string, code: string, extra: Record<string, unknown> = {}) => ({
  id, title_code: code, why_code: code, recommended: false, unplotted: { count: 0, code: null }, thumbnail_url: `/t/${id}`, thumbnail_status: 'pending' as const, ...extra,
})

describe('CandidateStage：一张主图 + 一排切换', () => {
  const list = (extra: Record<string, unknown> = {}) =>
    candidatesFromResponse([item('a', 'line_band', { facts: { rows: 40, drawn: 36, groups: 3, n_min: 12, n_max: 12, x_values: 6 }, ...extra }), item('b', 'scatter_fit'), item('c', 'points_mean')], 'pa')
  const allReady = (cs: { id: string }[]) => Object.fromEntries(cs.map((c) => [c.id, 'ready' as const]))
  const onCreate = vi.fn()
  const onPreview = vi.fn()
  const Harness = ({ items, thumbs, drawingId = null }: { items: ReturnType<typeof list>; thumbs: Record<string, 'ready' | 'pending'>; drawingId?: string | null }) => {
    const [key, setKey] = useState<string | null>(null)
    return (
      <CandidateStage
        items={items}
        thumbs={thumbs}
        drawingId={drawingId}
        previewKey={key}
        onPreview={(id) => {
          onPreview(id)
          setKey(candidateIdentity(items.find((c) => c.id === id)!))
        }}
        onCreate={onCreate}
        onThumb={() => {}}
      />
    )
  }
  beforeEach(() => {
    onCreate.mockReset()
    onPreview.mockReset()
  })

  it('说明里的误差类型跟着这张图实际画的那一种走：没点过 = 标准差，点了标准误 / 置信区间就改口（Codex 评审 #2176）', async () => {
    for (const [error, word] of [[undefined, '标准差'], ['sd', '标准差'], ['sem', '标准误'], ['ci95', '95% 置信区间']] as const) {
      const items = candidatesFromResponse([item('a', 'line_band', error ? { error } : {})], 'pa')
      await mount(<Harness items={items} thumbs={allReady(items)} />)
      expect(q('[data-candidate-why]')!.textContent).toBe(`每个横轴位置取均值，带是${word}，看总体趋势和波动。`)
      act(() => root.unmount())
      root = createRoot(host)
    }
    const bar = candidatesFromResponse([item('b', 'bar_error', { error: 'sem' })], 'pa')
    await mount(<Harness items={bar} thumbs={allReady(bar)} />)
    expect(q('[data-candidate-why]')!.textContent).toBe('各组均值一目了然，误差线是标准误；个别点会藏在柱里。')
  })

  it('切换排永远一行：候选多了（合画最多 8 张）在这一排里横向滚动，不换行、不把区域撑高', async () => {
    const codes = ['points_mean', 'box_points', 'violin_points', 'ecdf_groups', 'bar_error', 'line_points', 'line_band', 'scatter']
    const items = candidatesFromResponse(codes.map((c, i) => item(`m${i}`, c)), 'pa')
    await mount(<Harness items={items} thumbs={allReady(items)} />)
    const wrap = q('[data-candidate-switch]') as HTMLElement
    expect(wrap.className).toContain('overflow-x-auto')
    const grid = wrap.querySelector('[data-option-grid]') as HTMLElement
    expect(grid.querySelectorAll('[role="radio"]')).toHaveLength(8)
    // 8 列 = 一行；内层按 8 × 150 定宽，超出外层就滚动
    expect(grid.style.gridTemplateColumns).toBe('repeat(8, minmax(0, 1fr))')
    expect((grid.parentElement as HTMLElement).style.width).toBe('1200px')
  })

  it('默认主图是第一张（推荐）：标题 +「推荐」+ 说明 + 依据；创建按钮在标题行右端', async () => {
    const items = list()
    await mount(<Harness items={items} thumbs={allReady(items)} />)
    expect(q('[data-candidate-title]')!.textContent).toBe('均值线 + 误差带')
    expect(q('[data-candidate-stage]')!.textContent).toContain('推荐')
    expect(q('[data-candidate-why]')!.textContent).not.toBe('')
    expect(q('[data-candidate-basis]')!.textContent).toBe('依据：按 3 组各 12 个数值绘制，横轴有 6 个不同取值。')
    const head = q('[data-candidate-title]')!.parentElement!
    expect(head.lastElementChild).toBe(q('[data-candidate-create]'))
    expect(q('[data-candidate-create]')!.textContent).toBe('创建可编辑图')
    expect(qa('[data-candidate-switch] [data-value]')).toHaveLength(3)
  })

  it('点切换项只换主图，不创建；点「创建可编辑图」才创建选中的那一张', async () => {
    const items = list()
    await mount(<Harness items={items} thumbs={allReady(items)} />)
    await click(qa('[data-candidate-switch] [data-value]')[1]!)
    expect(onPreview).toHaveBeenCalledWith('b')
    expect(onCreate).not.toHaveBeenCalled()
    expect(q('[data-candidate-title]')!.textContent).toBe('散点 + 拟合线')
    expect(q('[data-candidate-stage]')!.textContent).not.toContain('推荐') // 推荐小标只在第一张
    expect(qa('[data-candidate-switch] [data-value]')[1]!.getAttribute('aria-checked')).toBe('true')
    expect(q('[data-candidate-basis]')).toBeNull() // 没有 facts 就没有这一行
    await click(q('[data-candidate-create]'))
    expect(onCreate).toHaveBeenCalledTimes(1)
    expect(onCreate).toHaveBeenCalledWith('b')
  })

  it('图没到的项不可选；主图的创建按钮在选中的图到之前也不可点', async () => {
    const items = list()
    await mount(<Harness items={items} thumbs={{ a: 'ready', b: 'pending', c: 'pending' }} />)
    expect(qa<HTMLButtonElement>('[data-candidate-switch] [data-value]').map((b) => b.disabled)).toEqual([false, true, true])
    await click(qa('[data-candidate-switch] [data-value]')[1]!)
    expect(onPreview).not.toHaveBeenCalled()
    act(() => root.unmount())
    host.remove()
    await mount(<Harness items={items} thumbs={{ a: 'pending', b: 'ready', c: 'ready' }} />)
    expect(q<HTMLButtonElement>('[data-candidate-create]')!.disabled).toBe(true)
  })

  const BasisStage = ({ items }: { items: ReturnType<typeof candidatesFromResponse> }) => (
    <CandidateStage items={items} thumbs={Object.fromEntries(items.map((c) => [c.id, 'ready' as const]))} drawingId={null} previewKey={null} onPreview={() => {}} onCreate={() => {}} onThumb={() => {}} />
  )
  it('依据句整句：「依据：」+ 主句 +「；」+ 没画的行 +「。」，中文各部分直接相连', async () => {
    const items = candidatesFromResponse(
      [item('a', 'points_mean', { facts: { rows: 26, drawn: 24, groups: 3, n_min: 8, n_max: 8, x_values: null, skipped_missing: 2, skipped_other: 0 }, unplotted: { count: 2, code: null } })],
      'pa',
    )
    await mount(<BasisStage items={items} />)
    expect(q('[data-candidate-basis]')!.textContent).toBe('依据：按 3 组各 8 个数值绘制；另有 2 行因缺值未计入。')
  })

  it('依据句：两种原因都有、旧响应只有总数', async () => {
    const mk = (facts: Record<string, unknown>, count: number) =>
      candidatesFromResponse([item('a', 'hist', { facts: { rows: 30, drawn: 24, groups: null, n_min: null, n_max: null, x_values: null, ...facts }, unplotted: { count, code: null } })], 'pa')
    let items = mk({ skipped_missing: 2, skipped_other: 3 }, 5)
    await mount(<BasisStage items={items} />)
    expect(q('[data-candidate-basis]')!.textContent).toBe('依据：按 24 个数值绘制；另有 2 行因缺值、3 行因不是数值未计入。')
    act(() => root.unmount())
    host.remove()
    items = mk({}, 4)
    await mount(<BasisStage items={items} />)
    expect(q('[data-candidate-basis]')!.textContent).toBe('依据：按 24 个数值绘制；另有 4 行未计入。')
  })

  it('1 张候选：没有切换排', async () => {
    const items = list().slice(0, 1)
    await mount(<Harness items={items} thumbs={allReady(items)} />)
    expect(q('[data-candidate-switch]')).toBeNull()
    expect(qa('[data-candidate-switch] [data-value]')).toHaveLength(0)
    expect(q('[data-candidate-create]')).not.toBeNull()
  })

  it('出图中：主图带扫光、切换排与按钮置灰', async () => {
    const items = list()
    await mount(<Harness items={items} thumbs={allReady(items)} drawingId="a" />)
    expect(q('[data-candidate-stage] .animate-sweep')).not.toBeNull()
    expect(qa<HTMLButtonElement>('[data-candidate-switch] [data-value]').every((b) => b.disabled)).toBe(true)
    expect(q<HTMLButtonElement>('[data-candidate-create]')!.disabled).toBe(true)
  })

  it('候选没回来：骨架（主图 1 + 切换 3），没有按钮', async () => {
    await mount(<CandidateStage items={[]} thumbs={{}} pending drawingId={null} previewKey={null} onPreview={() => {}} onCreate={() => {}} onThumb={() => {}} />)
    expect(qa('[data-candidate-skeleton]')).toHaveLength(4)
    expect(q('[data-candidate-create]')).toBeNull()
  })

  it('成对的合画候选：切换项各有 variant 一行，主图标题下也有，列名只当文本', async () => {
    const col = '组别<b>x</b>'
    const items = candidatesFromResponse(
      [item('a', 'points_mean', { variant: { kind: 'split', column: col } }), item('b', 'points_mean', { variant: { kind: 'by_file' } })],
      'pa',
    )
    await mount(<Harness items={items} thumbs={allReady(items)} />)
    expect(qa('[data-candidate-switch] [data-value] [data-candidate-variant]').map((e) => e.textContent)).toEqual([`按文件 +「${col}」分组`, '只按文件分组'])
    expect(q('[data-candidate-stage] > div [data-candidate-variant]')!.textContent).toBe(`按文件 +「${col}」分组`)
    expect(q('[data-candidate-stage] b')).toBeNull()
  })

  it('主图的盒子是图本身：object-contain、受最大高度约束，不拉伸不裁切', async () => {
    const items = list()
    await mount(<Harness items={items} thumbs={allReady(items)} />)
    const img = q<HTMLImageElement>('[data-candidate-image] img')!
    act(() => void img.dispatchEvent(new Event('load')))
    expect(img.className).toContain('object-contain')
    expect(img.style.maxHeight).toContain('100cqh')
    expect(img.className).not.toContain('object-cover')
  })
})

describe('DataPanel：需要你确认', () => {
  const roles = { x: 'x', y: 'y', yAll: ['y'], group: null, stack: false, yExtra: 0, options: { x: ['x'], y: ['y'], group: [] } }
  const resp = (extra: Record<string, unknown> = {}): AiDataProposeResponse =>
    ({
      kind: 'candidates', session: 'S2', dropped: [], render: 'lazy', header_row: 1, defaults: [], mixed_cells: [],
      items: [item('n0', 'line_band', { error: 'sd' }), item('n1', 'scatter')],
      roles: { x: 'x', y: ['y'], group: null, facet: null, unit: null, options: { x: ['x'], y: ['y'], group: [] } },
      ...extra,
    }) as AiDataProposeResponse
  const seed = (over: Record<string, unknown> = {}) => {
    const cs = candidatesFromResponse([item('c0', 'line_band', { error: 'sd' }), item('c1', 'scatter')], 'pa')
    useDataFigureStore.setState({
      selected: { path: 'a.csv', sheet: null }, draftOpen: true, draftActive: true, phase: 'ready', session: 'S1', roles: roles as never,
      candidates: cs, thumbs: { c0: 'ready', c1: 'ready' }, errorConfirmed: false, designAsked: false, answers: {}, bindingCaptured: true, ...over,
    })
  }
  const mountPanel = () => mount(<DataPanel />)
  /** Radix Select：指针按下开、点选项 */
  const choose = async (role: string, value: string) => {
    const trigger = q(`[data-data-role="${role}"] button`)!
    await act(async () => {
      trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }))
    })
    const opt = qa('[role=option]').find((o) => o.textContent === value)
    expect(opt, `没有选项 ${value}`).toBeTruthy()
    await act(async () => {
      opt!.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0, pointerType: 'mouse' }))
      opt!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await flush()
  }
  beforeEach(() => {
    propose.mockReset()
    setCurrentProjectId('pa')
    useDataFigureStore.getState().clear()
  })

  it('选中的候选带 error 且没确认：小标题「需要你确认」+ 一行「误差线」，值显示当前（标准差）；没有待确认项时没有小标题', async () => {
    seed()
    await mountPanel()
    expect(q('[data-data-confirm-title]')!.textContent).toBe('需要你确认')
    const row = q('[data-data-confirm] [data-data-role="error"]')!
    expect(row.textContent).toContain('误差线')
    expect(row.textContent).toContain('标准差 (SD)')
    expect(q('[data-data-confirm] [data-data-role="design"]')).toBeNull()
    // 预览切到没有误差线的那一张：不再需要确认
    act(() => useDataFigureStore.setState({ previewKey: candidateIdentity(useDataFigureStore.getState().candidates[1]!) }))
    expect(q('[data-data-confirm-title]')).toBeNull()
    expect(q('[data-data-role="error"]')).toBeNull()
  })

  it('选一项（包括仍选标准差）：写 answers.error 重新出候选；确认后那一行搬到列角色下面、小标题消失、可以再改', async () => {
    seed()
    propose.mockResolvedValue(resp({ error_confirmed: true }))
    await mountPanel()
    await choose('error', '标准差 (SD)')
    expect(propose).toHaveBeenCalledTimes(1)
    expect(propose.mock.calls[0]![0].answers).toEqual({ error: 'sd' })
    expect(q('[data-data-confirm-title]')).toBeNull()
    const roles = q('[data-data-roles]')!
    expect(roles.querySelector('[data-data-role="error"]')).not.toBeNull() // 在列角色那一组里
    expect(roles.querySelector('[data-data-role="error"]')!.textContent).toContain('标准差 (SD)')
    propose.mockResolvedValue(resp({ error_confirmed: true }))
    await choose('error', '标准误 (SEM)')
    expect(propose.mock.calls[1]![0].answers).toEqual({ error: 'sem' })
    expect(useDataFigureStore.getState().autoPicks).toEqual({})
  })

  it('重复测量：defaults 里有 pick_design 才出现，当前值「不确定」；选「同一对象测了多次」写 answers.design、确认后留在列角色下面', async () => {
    seed({ designAsked: true, candidates: candidatesFromResponse([item('c1', 'scatter')], 'pa'), thumbs: { c1: 'ready' } })
    propose.mockResolvedValue(resp({ items: [item('n1', 'scatter')], defaults: [] }))
    await mountPanel()
    const row = q('[data-data-confirm] [data-data-role="design"]')!
    expect(row.textContent).toContain('重复测量')
    expect(row.textContent).toContain('还不确定')
    // 标签列统一 56px、放得下「重复测量」；选项放不下时截断并带 title，不撑高行
    expect(row.querySelector<HTMLElement>('[data-select-trigger]')!.title).toBe('还不确定')
    expect(q('[data-data-role="design"] span[style]')!.getAttribute('style')).toContain('width: 56px')
    expect(row.querySelector('[title="重复测量"]')!.className).toContain('truncate')
    expect(q('[data-data-role="error"]')).toBeNull()
    await choose('design', '是，同一个样本测了多次')
    expect(propose.mock.calls[0]![0].answers).toEqual({ design: 'repeated' })
    expect(q('[data-data-confirm-title]')).toBeNull()
    expect(q('[data-data-roles] [data-data-role="design"]')!.textContent).toContain('是，同一个样本测了多次')
  })

  it('读表 / 出图中禁用', async () => {
    seed({ drawingId: 'c0' })
    await mountPanel()
    expect(q<HTMLButtonElement>('[data-data-role="error"] button')!.disabled).toBe(true)
  })

  it('没有 error 的候选 / 没有 pick_design：整块不出现', async () => {
    seed({ candidates: candidatesFromResponse([item('c1', 'scatter')], 'pa'), thumbs: { c1: 'ready' } })
    await mountPanel()
    expect(q('[data-data-confirm]')).toBeNull()
  })
})
