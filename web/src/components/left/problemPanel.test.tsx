/**
 * 问题面板的界面看护（ADR 0030）。
 *
 * 三条硬规矩逐条量：普通界面**不出现内部标识**、「查不了」与「没问题」
 * **是两个答案**、修复**可撤销**。外加筛选、空态、键盘与轨道角标。
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createRoot, type Root } from 'react-dom/client'
import { literal, setLocale } from '@/i18n'
import { ProblemPanel } from './ProblemPanel'
import { LeftRail } from './LeftRail'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { runValidation, useValidationStore } from '@/store/validationStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  script: 'fig1.py',
}

const manifest = {
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [
    {
      gid: 'axes_0.xticks',
      role: 'ticks',
      label: 'X 刻度文字',
      bbox: [0.1, 0.9, 0.8, 0.05],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 6 }],
    },
    {
      gid: 'axes_0.xlabel',
      role: 'axis_label',
      label: 'X 轴标题',
      bbox: [0.1, 0.95, 0.8, 0.05],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 7 }],
    },
  ],
}

let container: HTMLDivElement
let root: Root

async function mount(node: React.ReactNode) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}

async function seed() {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_panel')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 60 }
    d.objects = [{ ...panel }]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  seedExactRender(panel, manifest as never)
  runValidation()
}

const text = () => container.textContent ?? ''
const buttons = () => [...container.querySelectorAll('button')]
const byText = (s: string) => buttons().find((b) => b.textContent?.includes(s))
const click = async (el: Element) =>
  act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })

beforeEach(() => {
  useUiStore.setState({ problemFilter: null, leftTab: 'problems', leftOpen: true })
  useValidationStore.setState({
    results: [],
    issues: [],
    ready: false,
    failed: false,
    running: false,
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  setLocale('zh-CN')
})

describe('普通界面不出现内部标识', () => {
  it('列的是人话主语（「X 刻度文字」），不是 gid', async () => {
    await seed()
    await mount(<ProblemPanel />)
    expect(text()).toContain('X 刻度文字')
    // gid / 对象 id 只允许出现在收起的技术详情里，不许出现在行本身
    const rows = [...container.querySelectorAll('[data-issue-row]')]
    expect(rows.length).toBeGreaterThan(0)
    for (const row of rows) {
      expect(row.textContent).not.toContain('axes_0')
      expect(row.textContent).not.toContain('p1')
      expect(row.getAttribute('aria-label') ?? '').not.toContain('axes_0')
    }
  })

  it('技术详情里有 gid，而且默认是收起的', async () => {
    await seed()
    await mount(<ProblemPanel />)
    const details = container.querySelector('details')!
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('axes_0.xticks')
  })

  it('每行给出短标题 + 当前值 → 要求', async () => {
    await seed()
    await mount(<ProblemPanel />)
    expect(text()).toContain('字号低于绝对下限')
    // 当前值 → 要求：两个数字都摆出来，用户不必点开才知道差多少
    expect(text()).toMatch(/6\.00pt\s*→\s*大于 8pt/)
  })
})

describe('空态、筛选与「查不了」', () => {
  it('没有问题时说「未发现问题」，不堆说明', async () => {
    useValidationStore.setState({ ready: true, failed: false, issues: [], results: [] })
    await mount(<ProblemPanel />)
    expect(text()).toContain('未发现问题')
  })

  it('「这一次没查成」与「没问题」是两句不同的话', async () => {
    useValidationStore.setState({ ready: false, failed: true, issues: [], results: [] })
    await mount(<ProblemPanel />)
    expect(text()).toContain('这一次没查成')
    expect(text()).not.toContain('未发现问题')
  })

  it('筛掉之后给的是「当前筛选下没有问题」+ 一键取消筛选', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await act(async () => useUiStore.getState().setProblemFilter(['suggestion']))
    expect(text()).toContain('当前筛选下没有问题')
    await click(byText('显示全部')!)
    expect(useUiStore.getState().problemFilter).toBeNull()
  })

  it('等级筛选是可切换的开关，带 aria-pressed', async () => {
    await seed()
    await mount(<ProblemPanel />)
    const chip = buttons().find((b) => b.getAttribute('aria-pressed') != null)!
    expect(chip.getAttribute('aria-pressed')).toBe('false')
    await click(chip)
    expect(chip.getAttribute('aria-pressed')).toBe('true')
    expect(useUiStore.getState().problemFilter).not.toBeNull()
  })
})

describe('无障碍与键盘', () => {
  it('每行的无障碍名带等级、主语与要求', async () => {
    await seed()
    await mount(<ProblemPanel />)
    const row = container.querySelector('[data-issue-row]')!
    const label = row.getAttribute('aria-label') ?? ''
    expect(label).toContain('阻断')
    expect(label).toContain('X 刻度文字')
  })

  it('清单可用方向键漫游', async () => {
    await seed()
    await mount(<ProblemPanel />)
    const rows = [...container.querySelectorAll<HTMLElement>('[data-issue-row]')]
    expect(rows.length).toBeGreaterThan(1)
    rows[0].focus()
    await act(async () => {
      rows[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    })
    expect(document.activeElement).toBe(rows[1])
  })

  it('「修复」是行的兄弟节点，不是它的子节点（nested interactive）', async () => {
    await seed()
    await mount(<ProblemPanel />)
    for (const row of container.querySelectorAll('[data-issue-row]')) {
      expect(row.querySelector('button')).toBeNull()
    }
  })
})

describe('安全修复', () => {
  it('点一下就修好，且能撤销', async () => {
    await seed()
    await mount(<ProblemPanel />)
    const fix = byText('修复')!
    const past = useDocumentStore.getState().past.length
    await click(fix)
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(p.overrides.length).toBeGreaterThan(0)
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    useDocumentStore.getState().undo()
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).overrides).toEqual([])
  })

  it('不能安全自动修的那些没有「修复」按钮', async () => {
    await seed()
    await mount(<ProblemPanel />)
    const fixable = useValidationStore
      .getState()
      .issues.filter((i) => i.fixKind !== 'none').length
    const fixButtons = buttons().filter((b) => b.textContent === '修复').length
    expect(fixButtons).toBeLessThanOrEqual(fixable)
    expect(fixButtons).toBeGreaterThan(0)
  })
})

describe('左轨入口', () => {
  it('折叠时角标给出问题数，而且不挡画布（就在轨道格子里）', async () => {
    await seed()
    useUiStore.setState({ leftOpen: false })
    await mount(<LeftRail />)
    const entry = container.querySelector('[data-rail="problems"]')!
    expect(entry).toBeTruthy()
    const n = useValidationStore.getState().issues.length
    expect(entry.textContent).toContain(String(n))
    expect(entry.getAttribute('aria-label')).toContain(String(n))
  })

  it('一个问题都没有时入口仍然在，只是不带角标', async () => {
    // 常驻入口：**没有问题也要在**——「一个问题都没有」本身就是用户要的答案
    useValidationStore.setState({ ready: true, failed: false, issues: [], results: [] })
    await mount(<LeftRail />)
    const entry = container.querySelector('[data-rail="problems"]')!
    expect(entry).toBeTruthy()
    expect(entry.textContent?.trim()).toBe('')
  })
})

describe('英文界面', () => {
  it('切到 en-US 之后措辞跟着换（存的是 key，不是翻好的字符串）', async () => {
    await seed()
    setLocale('en-US')
    await mount(<ProblemPanel />)
    expect(text()).toContain('Font below hard floor')
    expect(text()).not.toContain('字号低于绝对下限')
  })
})
