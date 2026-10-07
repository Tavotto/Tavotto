/**
 * 问题面板的界面看护（ADR 0030）。
 *
 * 三条硬规矩逐条量：普通界面**不出现内部标识**、「查不了」与「没问题」
 * **是两个答案**、修复**可撤销**。外加筛选、空态、键盘与轨道角标。
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot, type Root } from 'react-dom/client'
import { literal, setLocale } from '@/i18n'
import { RENDER_RETRY_DELAYS_MS } from '@/lib/imgRetry'
import { problemContextNow } from '@/lib/problemContext'
import { drillKey } from '@/lib/problemList'
import { useScopedProblems } from './useProblemScope'
import { ProblemPanel } from './ProblemPanel'
import { PREVIEW_ROWS } from './problemTree'
import { LeftPanel } from './LeftPanel'
import { LeftRail } from './LeftRail'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { runValidation, useValidationStore } from '@/store/validationStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'

/**
 * 面板内部的修复走后端事务（`/api/engine/specfix`）。这里替它回一份「通过」：
 * 原列表 + 每条点名的问题一条 patch——面板这一侧只关心「通过就一次 commit」，
 * 后端怎么算、怎么裁决在 `tests/test_specfix_real.py` 里对真实渲染验。
 */
const engineSpecfix = vi.fn(
  async (_id: string, patches: unknown[], _scale: number, _p: unknown, only?: { gid: string }[]) => ({
    ok: true,
    exit: 'done',
    patches: [
      ...(patches as object[]),
      ...(only ?? []).map((o) => ({ gid: o.gid, prop: 'fontsize', value: 8.5 })),
    ],
    changes: [],
    skipped: [],
    unresolved: [],
    blocking: [],
    adjustments: [],
  }),
)

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineSpecfix: (...args: Parameters<typeof engineSpecfix>) => engineSpecfix(...args),
  engineRender: () => new Promise(() => {}),
}))

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
/** 图标钮只有可达名，没有可见文字（逐项处理条的上 / 下，左栏审计 L26） */
const byLabel = (s: string) => buttons().find((b) => b.getAttribute('aria-label') === s)
const click = async (el: Element) =>
  act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })

/**
 * 问题面板先是卡片层（2026-09-28）：逐条清单在点进一张卡片之后。点第一张卡片，
 * 或装着指定对象的那张。
 */
async function openCard(objectId?: string) {
  // 按图看、只有一张拆不出子图的图时没有卡片层，清单直接就在（ProblemPanel 的 `single`）
  if (!objectId && container.querySelector('[data-issue-row]') && !container.querySelector('[data-problem-back]')) return
  const cards = [...container.querySelectorAll<HTMLElement>('li[data-problem-card]')]
  const card = objectId
    ? cards.find((c) => (c.dataset.problemCardObjects ?? '').split(' ').includes(objectId))
    : cards[0]
  expect(card, '卡片层上没有这张卡片').toBeTruthy()
  await click(card!.querySelector('button')!)
}
/** 此刻真正生效的那张卡片：store 里记着的、且盖的是此刻的现场章（与面板读的是同一个派生） */
const liveDrill = () => {
  const s = useUiStore.getState()
  return s.problemContext === problemContextNow() ? s.problemDrill : null
}
/** 树上各分桶的项数之和（不含图头：图头的项数就是它下面子图的和；整份排版范围里它应当等于全部问题数） */
const cardTotal = () =>
  [...container.querySelectorAll<HTMLElement>('li[data-problem-card]')]
    .filter((c) => !c.querySelector('li[data-problem-card]'))
    .reduce((n, c) => n + Number(c.dataset.problemCardCount), 0)
/** Radix 的 DropdownMenu 开在 pointerdown 上，jsdom 里 .click() 打不开它 */
async function openMenu(trigger: Element) {
  await act(async () => {
    trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
    trigger.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, button: 0 }))
    await new Promise((r) => setTimeout(r, 0))
  })
}
/** 标题行的范围胶囊（`data-problem-scope-trigger` = 此刻生效的范围） */
const scopePill = () => container.querySelector<HTMLElement>('[data-problem-scope-trigger]')!
const scopeNow = () => scopePill().dataset.problemScopeTrigger
const scopeItem = (s: 'figure' | 'document') => document.querySelector<HTMLElement>(`[data-problem-scope="${s}"]`)!
/** 换范围：开胶囊的菜单、点那一档 */
async function chooseScope(s: 'figure' | 'document') {
  await openMenu(scopePill())
  await act(async () => scopeItem(s).click())
}
/** 换分组方式：开标题行的「⋯」、点那一档 */
async function chooseView(v: 'figure' | 'category') {
  await openMenu(container.querySelector('[data-problem-menu]')!)
  await act(async () => document.querySelector<HTMLElement>(`[data-problem-view="${v}"]`)!.click())
}

beforeEach(() => {
  useUiStore.setState({
    problemFilter: null,
    problemScope: null,
    problemCursor: null,
    problemView: 'figure',
    problemDrill: null,
    problemContext: null,
    elementPanelId: null,
    leftTab: 'problems',
    leftOpen: true,
    layout: 'wide',
  })
  useWorkspaceStore.getState().clear()
  useSelectionStore.getState().clear()
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

describe('「还没查」不许掉进绿色空态', () => {
  it('`ready=false, running=false` 是防抖窗口里的常态，那一刻不许说「没有问题」', async () => {
    // 换文档之后 `resetValidation()` 与那一轮真正开跑之间有 250ms 防抖窗口
    useValidationStore.setState({
      results: [],
      issues: [],
      ready: false,
      failed: false,
      running: false,
    })
    await mount(<ProblemPanel />)
    expect(text(), '这一刻根本还没查过，却报了一屏静悄悄的绿').not.toContain('未发现问题')
    expect(text()).toContain('正在检查')
  })

  it('查完了确实没问题时，才说没问题', async () => {
    useValidationStore.setState({
      results: [],
      issues: [],
      ready: true,
      failed: false,
      running: false,
    })
    await mount(<ProblemPanel />)
    expect(text()).toContain('未发现问题')
  })
})

describe('普通界面不出现内部标识', () => {
  it('列的是人话主语（「X 轴刻度」，引擎串「X 刻度文字」经 engineLabel 翻过），不是 gid', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    expect(text()).toContain('X 轴刻度')
    // gid / 对象 id 只允许出现在收起的技术详情里，不许出现在行本身
    const rows = [...container.querySelectorAll('[data-issue-row]')]
    expect(rows.length).toBeGreaterThan(0)
    for (const row of rows) {
      expect(row.textContent).not.toContain('axes_0')
      expect(row.textContent).not.toContain('p1')
      expect(row.getAttribute('aria-label') ?? '').not.toContain('axes_0')
    }
  })

  it('技术详情（ⓘ）里有 gid：默认不在页面上，点开尾随格里的 ⓘ 才出现', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    expect(document.querySelector('[data-issue-tech]')).toBeNull()
    const toggle = container.querySelector<HTMLElement>('[data-issue-tech-toggle]')!
    expect(toggle.closest('[data-problem-trail]'), 'ⓘ 在行尾的尾随格里').toBeTruthy()
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    await click(toggle)
    const details = document.querySelector<HTMLElement>('[data-issue-tech]')!
    expect(details.textContent).toContain('axes_0.xticks')
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
  })

  it('悬停不改行高：尾随格只换透明度——静止是值，指到 / 聚焦时同一格换成「修复」与 ⓘ', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    const rowEls = [...container.querySelectorAll<HTMLElement>('[data-issue-row]')]
    const toggles = [...container.querySelectorAll<HTMLElement>('[data-issue-tech-toggle]')]
    expect(toggles.length).toBe(rowEls.length)
    for (const t of toggles) {
      // 与「定位」按钮同一个 li（兄弟），不是行下面另起的一行
      const li = t.closest('[data-problem-trail]')!.parentElement!
      expect(li.querySelector(':scope > [data-issue-row]')).toBeTruthy()
    }
    // 任何会随悬停 / 聚焦改 display 的类都不许出现（那正是每指一行清单跳 20px 的来源）
    const classes = rowEls
      .flatMap((r) => [...r.closest('li')!.querySelectorAll<HTMLElement>('*')])
      .map((el) => el.getAttribute('class') ?? '')
      .join(' ')
    expect(classes).not.toMatch(/(hover|focus-within|focus-visible)[\w/-]*:(block|hidden|flex|inline)\b/)
    // 同一格两层：静止时值可见、动作透明；行拿到焦点后反过来
    const trail = rowEls[0].closest('li')!.querySelector<HTMLElement>('[data-problem-trail]')!
    const rest = trail.querySelector<HTMLElement>('[data-problem-trail-rest]')!
    const action = trail.querySelector<HTMLElement>('[data-problem-trail-action]')!
    expect(rest.textContent).toMatch(/6\.00 pt/)
    expect(action.className).toContain('opacity-0')
    expect(action.querySelector('[data-issue-fix]'), '修复钮一直在 DOM 里（Tab 得到）').toBeTruthy()
    await act(async () => rowEls[0].focus())
    expect(action.className).toContain('opacity-100')
    expect(rest.className).toContain('opacity-0')
  })

  it('每行给出短标题 + 当前值 → 要求', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    expect(text()).toContain('字号低于绝对下限')
    // 当前值 → 要求：两个数字都摆出来，用户不必点开才知道差多少
    expect(text()).toMatch(/6\.00 pt\s*→\s*大于 8 pt/)
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
    expect(text()).toContain('检查未完成')
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
    await openCard()
    const row = container.querySelector('[data-issue-row]')!
    const label = row.getAttribute('aria-label') ?? ''
    expect(label).toContain('阻断')
    expect(label).toContain('X 轴刻度')
  })

  it('清单可用方向键漫游', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
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
    await openCard()
    const rows = container.querySelectorAll('[data-issue-row]')
    expect(rows.length, '一行都没有时这条判据恒真').toBeGreaterThan(0)
    for (const row of rows) {
      expect(row.querySelector('button')).toBeNull()
    }
  })
})

describe('安全修复', () => {
  it('点一下就修好，且能撤销', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    const fix = byText('修复')!
    const past = useDocumentStore.getState().past.length
    await click(fix)
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(p.overrides.length).toBeGreaterThan(0)
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    useDocumentStore.getState().undo()
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).overrides).toEqual([])
  })

  it('修复在跑的那几秒：按钮置灰、说「正在修复…」，回来之后恢复', async () => {
    await seed()
    await mount(<ProblemPanel />)
    let release: () => void = () => {}
    const gate = new Promise<void>((r) => (release = r))
    engineSpecfix.mockImplementationOnce(async (_id, patches) => {
      await gate
      return {
        ok: true,
        exit: 'done',
        patches: patches as never[],
        changes: [],
        skipped: [],
        unresolved: [],
        blocking: [],
        adjustments: [],
      }
    })
    const all = container.querySelector<HTMLButtonElement>('button[data-problem-autofix]')!
    expect(all.textContent).toBe('全部修复 2')
    // 一颗填色主动作（32px lg）
    expect(all.dataset.variant).toBe('primary')
    await click(all)
    expect(all.disabled).toBe(true)
    expect(all.textContent).toBe('正在修复…')
    const rowFixes = [...container.querySelectorAll<HTMLButtonElement>('[data-issue-fix]')]
    expect(rowFixes.length).toBeGreaterThan(0)
    for (const b of rowFixes) expect(b.disabled).toBe(true)
    await act(async () => {
      release()
      await gate
    })
    expect(all.disabled).toBe(false)
  })

  it('native 图（tavotto run）：问题照常列出，修复按钮不可用，悬停说一句为什么（ADR 0080）', async () => {
    await seed()
    useRuntimeAssetStore.setState({
      byId: { 'Fig1.pdf': { status: 'fresh', cached: true, registered: true, profile: 'native', checked: true } },
    } as never)
    try {
      await mount(<ProblemPanel />)
      await openCard()
      expect(useValidationStore.getState().issues.length).toBeGreaterThan(0)
      const wraps = [...container.querySelectorAll('[data-fix-native-unsupported]')]
      expect(wraps.length).toBeGreaterThan(0)
      for (const w of wraps) expect(w.querySelector('button')?.hasAttribute('disabled')).toBe(true)
      // 说明不常驻：不悬停时界面上没有这句
      expect(text()).not.toContain('暂不支持自动修复')
      const calls = engineSpecfix.mock.calls.length
      await click(wraps[0].querySelector('button')!)
      expect(engineSpecfix.mock.calls.length).toBe(calls)
    } finally {
      useRuntimeAssetStore.setState({ byId: {} })
    }
  })

  it('不能安全自动修的那些没有「修复」按钮', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    const fixable = useValidationStore
      .getState()
      .issues.filter((i) => i.fixKind !== 'none').length
    const fixButtons = container.querySelectorAll('[data-issue-fix="safe"]').length
    expect(fixButtons).toBeLessThanOrEqual(fixable)
    expect(fixButtons).toBeGreaterThan(0)
  })
})

describe('左轨入口', () => {
  it('有阻断项时图标上是一颗红点（只为阻断亮），不挂红底数字；「N 项阻断（共 M）」在可达名里', async () => {
    await seed() // 两条都是阻断（字号低于绝对下限）
    useUiStore.setState({ leftOpen: false })
    await mount(<LeftRail />)
    const entry = container.querySelector('[data-rail="problems"]')!
    expect(entry).toBeTruthy()
    const n = useValidationStore.getState().issues.length
    const dot = entry.querySelector('[data-rail-blocking]')
    expect(dot, '有阻断项时要有提示').toBeTruthy()
    expect(dot!.className).toContain('bg-danger')
    // 图标是检查清单，不与「警告」同形（2026-10-07 审计 §9.4）
    expect(entry.querySelector('svg.icon-list-checks')).toBeTruthy()
    // 轨钮下面写的是短名（2026-09-30 重设计），不是数字
    expect(entry.textContent?.trim(), '轨道上不再写数字').not.toMatch(/\d/)
    expect(entry.getAttribute('aria-label')).toBe(`问题 · ${n} 项阻断（共 ${n}）`)
  })

  it('选中态是白底 + 1px 轮廓，不靠投影（卡片投影只属于 ui/Card）', async () => {
    await seed()
    useUiStore.setState({ leftOpen: true, leftTab: 'problems' })
    await mount(<LeftRail />)
    const entry = container.querySelector('[data-rail="problems"]')!
    expect(entry.getAttribute('aria-expanded')).toBe('true')
    expect(entry.className).toContain('bg-surface')
    expect(entry.className).toContain('outline-border')
    expect(entry.className).not.toContain('shadow')
  })

  it('只有警告 / 建议时不打扰：没有小点', async () => {
    await seed()
    const soft = useValidationStore.getState().issues.map((i) => ({ ...i, severity: 'warn' as const }))
    useValidationStore.setState({ issues: soft })
    await mount(<LeftRail />)
    const entry = container.querySelector('[data-rail="problems"]')!
    expect(entry.querySelector('[data-rail-blocking]')).toBeNull()
    expect(entry.getAttribute('aria-label')).toContain(String(soft.length))
  })

  it('一个问题都没有时入口仍然在，只是不带标记', async () => {
    // 常驻入口：**没有问题也要在**——「一个问题都没有」本身就是用户要的答案
    useValidationStore.setState({ ready: true, failed: false, issues: [], results: [] })
    await mount(<LeftRail />)
    const entry = container.querySelector('[data-rail="problems"]')!
    expect(entry).toBeTruthy()
    expect(entry.querySelector('[data-rail-blocking]')).toBeNull()
    expect(entry.textContent?.trim()).not.toMatch(/\d/)
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

describe('这一轮查砸了、上一轮的结果还留着', () => {
  const list = () => container.querySelector('ul[aria-label]')

  it('失败提示与**那份留下来的清单**同时在场，不是二选一', async () => {
    await seed() // ready=true，issues 非空
    const kept = useValidationStore.getState().issues.length
    expect(kept).toBeGreaterThan(0)
    // 下一轮查砸了，`validationStore` 刻意把上一轮的结果留着
    useValidationStore.setState({ failed: true })
    await mount(<ProblemPanel />)
    await openCard()

    // 失败要说出来——那句话本身就承诺了「下面列的是上一次的结果」
    expect(text()).toContain('下面是上次的结果')
    // ……那就真的得列出来。它们仍算在计数条与导出摘要里，
    // 藏起来就成了「看得见数字、找不到东西」
    expect(list(), '整屏被换成错误空态，留下来的问题在唯一一份清单里翻不到').toBeTruthy()
    expect(list()!.querySelectorAll('[data-issue-row]').length).toBe(kept)
    expect(text()).toContain('X 轴刻度')
  })

  it('上一轮什么都没有时仍然只出错误空态，不摆一条没有清单的横幅', async () => {
    useValidationStore.setState({ ready: true, failed: true, issues: [], results: [] })
    await mount(<ProblemPanel />)
    expect(text()).toContain('检查未完成')
    expect(text()).not.toContain('未发现问题')
    expect(list()).toBeNull()
  })
})

/* ------------------------------ 审计 T09 --------------------------------- */

const panel2: PanelObject = {
  ...panel,
  id: 'p2',
  fileId: 'Fig2.pdf',
  y: 70,
  script: 'fig2.py',
}

const manifest2 = {
  stem: 'Fig2',
  size_mm: [80, 60],
  elements: [
    {
      gid: 'axes_0.yticks',
      role: 'ticks',
      label: 'Y 刻度文字',
      bbox: [0.05, 0.1, 0.05, 0.8],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 6 }],
    },
  ],
}

/** 第三张：没有任何问题 */
const panel3: PanelObject = { ...panel, id: 'p3', fileId: 'Fig3.pdf', y: 140, script: 'fig3.py' }
const manifest3 = {
  stem: 'Fig3',
  size_mm: [80, 60],
  elements: [
    {
      gid: 'axes_0.title',
      role: 'title',
      label: '标题',
      bbox: [0.1, 0.0, 0.8, 0.05],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 9 }],
    },
  ],
}

/** 三张图：p1 两条问题、p2 一条、p3 没有 */
async function seedThree() {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_three')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 200 }
    d.objects = [{ ...panel }, { ...panel2 }, { ...panel3 }]
  })
  useAssetStore.setState({
    byId: {
      'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 },
      'Fig2.pdf': { id: 'Fig2.pdf', mtime: 1 },
      'Fig3.pdf': { id: 'Fig3.pdf', mtime: 1 },
    },
  } as never)
  seedExactRender(panel, manifest as never)
  seedExactRender(panel2, manifest2 as never)
  seedExactRender(panel3, manifest3 as never)
  runValidation()
}

const rows = () => [...container.querySelectorAll<HTMLElement>('[data-issue-row]')]
const cursorBar = () => container.querySelector('[data-problem-cursor]')
/** 全文档的问题数（三张图的 + 页面级那条：80×200 的页面比例不合规范） */
const total = () => useValidationStore.getState().issues.length
const severityChip = () => buttons().find((b) => (b.getAttribute('aria-label') ?? '').includes('只看'))!

describe('按规则聚合（审计 T09）', () => {
  it('同一条规则合成一组：标题只在组头说一遍，组头给受影响对象数，行里各说各的数字', async () => {
    await seed() // 两条 font-below-absolute-floor：xticks 6 pt、xlabel 7 pt
    await mount(<ProblemPanel />)
    await openCard()
    const groups = container.querySelectorAll('[data-issue-group]')
    expect(groups.length).toBe(1)
    expect(groups[0].getAttribute('data-issue-group')).toBe('font-below-absolute-floor')
    expect(text().split('字号低于绝对下限').length - 1, '标题逐行重复').toBe(1)
    expect(text()).toContain('2 个对象')
    expect(rows().length).toBe(2)
    expect(text()).toMatch(/6\.00 pt\s*→\s*大于 8 pt/)
    expect(text()).toMatch(/7\.00 pt\s*→\s*大于 8 pt/)
    // 等级不只靠颜色：组头写着等级文字
    expect(groups[0].textContent).toContain('阻断')
  })

  it('组头可折叠：折起来行就不在，展开又回来', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    const head = container.querySelector('[data-issue-group] button[aria-expanded]')!
    expect(head.getAttribute('aria-expanded')).toBe('true')
    await click(head)
    expect(rows().length).toBe(0)
    expect(head.getAttribute('aria-expanded')).toBe('false')
    await click(head)
    expect(rows().length).toBe(2)
  })

  it('组头的「修复 N 项」一次修完这一组能安全修的，一条历史可撤销', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    const past = useDocumentStore.getState().past.length
    // 组的「修复 N」在组头的尾随格里（一个动词：修复 / 修复… / 修复 N）
    const fix = container.querySelector<HTMLButtonElement>('[data-issue-group-head] [data-problem-fix-count]')!
    expect(fix.textContent).toBe('修复 2')
    await click(fix)
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(p.overrides.length).toBe(2)
    useDocumentStore.getState().undo()
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).overrides).toEqual([])
  })
})

describe('范围：当前图 / 整份排版（审计 T09）', () => {
  it('在图内编辑里打开面板，默认只看这张图；切到整份排版才列别的图', async () => {
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    expect(scopeNow()).toBe('figure')
    expect(scopePill().textContent).toContain('当前图')
    await openCard()
    expect(text()).toContain('X 轴刻度')
    expect(text()).not.toContain('Y 轴刻度')
    await chooseScope('document')
    expect(useUiStore.getState().problemScope).toBe('document')
    // 换范围退回卡片总览：别的图各有一张卡片，项数加起来就是全部
    expect(liveDrill()).toBeNull()
    expect(container.querySelector('li[data-problem-card][data-problem-card-objects~="p2"]')).toBeTruthy()
    expect(cardTotal()).toBe(total())
    // 页面级那条（主语是整张画布）也只在「整份排版」里出现
    const page = container.querySelector('li[data-problem-card][data-problem-card-rules~="page-aspect"]')
    expect(page?.textContent).toContain('整张画布')
  })

  it('快速编辑中的那张图也算「当前图」（没有进图内编辑也一样）', async () => {
    await seedThree()
    useWorkspaceStore.getState().enterFastEdit('p2')
    await mount(<ProblemPanel />)
    expect(scopeNow()).toBe('figure')
    await openCard()
    expect(text()).toContain('Y 轴刻度')
    expect(text()).not.toContain('X 轴刻度')
    // 图名写在范围胶囊的 title 里，不在面板上另挂一行
    expect(text()).not.toContain('Fig2.pdf')
    expect(scopePill().getAttribute('title')).toContain('Fig2.pdf')
  })

  it('没有正在编辑或选中的图：「当前图」灰掉并说明原因，实际看整份排版', async () => {
    await seedThree()
    useUiStore.setState({ problemScope: 'figure' })
    await mount(<ProblemPanel />)
    expect(scopeNow()).toBe('document')
    await openMenu(scopePill())
    const fig = scopeItem('figure')
    expect(fig.hasAttribute('data-disabled')).toBe(true)
    // 灰掉的那一档原地说明为什么——消失的选项解释不了自己
    expect(fig.textContent).toContain('没有正在编辑或选中的图')
    expect(cardTotal()).toBe(total())
  })

  it('范围裁到一张没有问题的图：说「这张图上没有问题」并给回整份排版的出口，不冒充「未发现问题」', async () => {
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p3' })
    await mount(<ProblemPanel />)
    expect(text()).toContain('这张图上没有问题')
    expect(text()).toContain(`整份排版里还有 ${total()} 项问题`)
    expect(text()).not.toContain('未发现问题')
    await click(byText('整份排版')!)
    expect(cardTotal()).toBe(total())
  })

  it('范围并进标题行：标题旁是范围胶囊（带这一档的数），没有第二层范围条；等级开关按范围算', async () => {
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<LeftPanel />)
    const heading = container.querySelector('h2')!
    expect(heading.textContent).toBe('问题')
    // 胶囊 portal 进了标题行的 meta 槽（与 h2 同一行）
    const meta = container.querySelector('[data-drawer-meta]')!
    expect(meta.parentElement).toBe(heading.parentElement)
    expect(meta.querySelector('[data-problem-scope-trigger]')?.textContent).toContain('2')
    // 「⋯」进了 actions 槽
    expect(container.querySelector('[data-drawer-actions] [data-problem-menu]')).toBeTruthy()
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(0)
    expect(severityChip().getAttribute('aria-label')).toContain('2')
  })
})

describe('吸顶组头与抽屉同色', () => {
  it('停靠抽屉（灰桌面）里组头读抽屉底色，不写死白底', async () => {
    await seed()
    await mount(<LeftPanel />)
    await openCard()
    const aside = container.querySelector<HTMLElement>('[data-left-drawer]')!
    expect(aside.className).toContain('[--drawer-bg:var(--color-bg)]')
    const head = container.querySelector<HTMLElement>('[data-issue-group-head]')!
    expect(head.className).toContain('bg-[var(--drawer-bg')
    expect(head.className).not.toMatch(/(^|\s)bg-surface(\s|$)/)
  })

  it('覆盖式抽屉（白底浮层）的底色变量跟着换', async () => {
    await seed()
    await mount(<LeftPanel overlay />)
    const aside = container.querySelector<HTMLElement>('[data-left-drawer]')!
    expect(aside.className).toContain('[--drawer-bg:var(--color-surface)]')
  })
})

describe('定位后清单留在原地（审计 T09）', () => {
  it('点一行：左栏仍是「问题」页，那行带「当前」标记，底部给第几条与「下一项」', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    await click(rows()[0])
    expect(useUiStore.getState().leftTab, '元素树把问题清单顶掉了').toBe('problems')
    expect(useUiStore.getState().elementPanelId).toBe('p1')
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xticks'])
    expect(rows()[0].getAttribute('aria-current')).toBe('true')
    // 「当前」不只靠颜色：选中底 + 600（listRowClass 的选中态），尾随格常亮出「修复」
    expect(rows()[0].className).toContain('font-semibold')
    expect(rows()[0].closest('li')!.querySelector('[data-problem-trail-action]')!.className).toContain('opacity-100')
    expect(rows()[1].getAttribute('aria-current')).toBeNull()
    expect(cursorBar()?.textContent).toContain('第 1 / 2 项')
    expect(cursorBar()?.textContent).toContain('X 轴刻度')
    // 「上一项 / 下一项」是两颗同形的图标钮（左栏审计 L26）：条上只剩现状那一句，
    // 方向名字在可达名里——此前上是图标钮、下是文字钮，一对动作看着像两件事
    expect(cursorBar()?.textContent).not.toContain('下一项')
    expect(byLabel('上一项')!.querySelector('svg')).not.toBeNull()
    expect(byLabel('下一项')!.querySelector('svg')).not.toBeNull()

    await click(byLabel('下一项')!)
    expect(rows()[1].getAttribute('aria-current')).toBe('true')
    expect(rows()[0].getAttribute('aria-current')).toBeNull()
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xlabel'])
    expect(cursorBar()?.textContent).toContain('第 2 / 2 项')
    // 到底了：清单里没有下一支，「下一项」原地不动
    await click(byLabel('下一项')!)
    expect(rows()[1].getAttribute('aria-current')).toBe('true')
  })

  it('F8 / ⇧F8 与「上一项 / 下一项」是同一个动作：没有游标时 F8 落到第一条', async () => {
    await seed()
    await mount(<ProblemPanel />)
    const press = (shiftKey = false) =>
      act(async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'F8', shiftKey, bubbles: true }))
      })
    await press()
    expect(rows()[0].getAttribute('aria-current')).toBe('true')
    await press()
    expect(rows()[1].getAttribute('aria-current')).toBe('true')
    await press(true)
    expect(rows()[0].getAttribute('aria-current')).toBe('true')
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xticks'])
  })

  it('指着一行：画布上那个对象描一道悬停轮廓（issueHover），指针离开就撤', async () => {
    await seed()
    await mount(<ProblemPanel />)
    const row = rows()[0]
    await act(async () => {
      row.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }))
    })
    expect(useUiStore.getState().issueHover).toEqual({ objectId: 'p1', gid: 'axes_0.xticks' })
    await act(async () => {
      row.dispatchEvent(new PointerEvent('pointerout', { bubbles: true, relatedTarget: document.body }))
    })
    expect(useUiStore.getState().issueHover).toBeNull()
  })

  it('当前那条修好消失之后，「下一项」指向顶上来的那条，不必重开清单', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    await click(rows()[0])
    const [, second] = useValidationStore.getState().issues
    await act(async () => useValidationStore.setState({ issues: [second] }))
    expect(rows().length).toBe(1)
    expect(cursorBar()?.textContent).toContain('已处理，还剩 1 项')
    await click(byLabel('下一项')!)
    expect(rows()[0].getAttribute('aria-current')).toBe('true')
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xlabel'])
  })

  it('清单空了游标就撤掉；「结束逐项处理」也能手动撤掉', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    await click(rows()[0])
    expect(useUiStore.getState().problemCursor).not.toBeNull()
    await click(buttons().find((b) => b.getAttribute('aria-label') === '结束逐项处理')!)
    expect(useUiStore.getState().problemCursor).toBeNull()
    expect(cursorBar()).toBeNull()

    await click(rows()[0])
    await act(async () => useValidationStore.setState({ issues: [] }))
    expect(useUiStore.getState().problemCursor).toBeNull()
  })

  it('叶子行保留稳定机器标识（教程与 e2e 靠它选行）', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    const row = container.querySelector(
      '[data-issue-row][data-issue-rule="font-below-absolute-floor"][data-issue-object="p1"]',
    )
    expect(row).toBeTruthy()
  })
})

/* ------------------- 长列表（Visual Consolidation Session 4） ------------------- */

/** 一张图上八处 6 pt 的文字：同一条规则、同一组、八行几乎一样的东西 */
const MANY = 8
const manifestMany = {
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: Array.from({ length: MANY }, (_, i) => ({
    gid: `axes_0.text_${i}`,
    role: 'annotation',
    label: `标注 ${i + 1}`,
    bbox: [0.1, 0.1 + i * 0.08, 0.3, 0.05],
    draggable: false,
    editable: [{ prop: 'fontsize', type: 'number', value: 6 }],
  })),
}

async function seedMany() {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_many')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 60 }
    d.objects = [{ ...panel }]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  seedExactRender(panel, manifestMany as never)
  runValidation()
}

const showRest = () => container.querySelector<HTMLButtonElement>('[data-issue-show-rest]')
const groupRows = (rule: string) =>
  container.querySelectorAll(`[data-issue-group="${rule}"] [data-issue-row]`).length

describe('长列表：一组默认只展开前几行', () => {
  it('八条同类问题默认只列前 5 条，其余收进「显示其余 3 项」；点开后全在', async () => {
    await seedMany()
    await mount(<ProblemPanel />)
    await openCard()
    const rule = 'font-below-absolute-floor'
    expect(
      useValidationStore.getState().issues.filter((i) => i.ruleCode === rule).length,
      '夹具没有产出足够多的同类问题，下面的判据量不到折叠',
    ).toBe(MANY)
    expect(groupRows(rule)).toBe(PREVIEW_ROWS)
    // 组头照旧报全部对象数：折起来的是行，不是事实
    expect(container.querySelector(`[data-issue-group="${rule}"]`)?.textContent).toContain(
      `${MANY} 个对象`,
    )
    const more = showRest()
    expect(more?.textContent).toContain(`显示其余 ${MANY - PREVIEW_ROWS} 项`)
    await click(more!)
    expect(groupRows(rule)).toBe(MANY)
    expect(showRest()).toBeNull()
  })

  it('只差一两条就不折：省下的那一行不值得多一次点击', async () => {
    // 6 条：折了只剩「显示其余 1 项」，比直接列出来更啰嗦
    const six = { ...manifestMany, elements: manifestMany.elements.slice(0, PREVIEW_ROWS + 1) }
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_six')
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.page = { w: 80, h: 60 }
      d.objects = [{ ...panel }]
    })
    useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
    seedExactRender(panel, six as never)
    runValidation()
    await mount(<ProblemPanel />)
    await openCard()
    expect(groupRows('font-below-absolute-floor')).toBe(PREVIEW_ROWS + 1)
    expect(showRest()).toBeNull()
  })

  it('「下一项」走进折起的那部分时整组自动展开，当前行看得见', async () => {
    await seedMany()
    await mount(<ProblemPanel />)
    await openCard()
    await click(rows()[PREVIEW_ROWS - 1])
    expect(rows()[PREVIEW_ROWS - 1].getAttribute('aria-current')).toBe('true')
    expect(rows().length).toBe(PREVIEW_ROWS)
    await click(byLabel('下一项')!)
    // 第 6 条成了「当前」：它必须在 DOM 里且带标记，而不是消失在折叠之后
    expect(rows().length).toBe(MANY)
    expect(rows()[PREVIEW_ROWS].getAttribute('aria-current')).toBe('true')
    expect(showRest()).toBeNull()
  })
})

/**
 * 2026-09-13 审计 B55 / B06：两个范围页签各带自己的计数；「无法核验」的组另起一段，
 * 带一行小标题，排在需要处理的组之后——它不是通过，也不是错误。
 */
describe('页签计数与「无法自动检查」分段', () => {
  it('「当前图」与「整份排版」各带自己的数：胶囊上是生效那一档，菜单里两档的数同时看得见', async () => {
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    const figureCount = useValidationStore
      .getState()
      .issues.filter((i) => i.objectRef.objectId === 'p1').length
    expect(figureCount).toBeGreaterThan(0)
    expect(figureCount).toBeLessThan(total())
    expect(scopePill().textContent).toContain(String(figureCount))
    // 可达名也带数：读屏不用切过去才知道这一档有几条
    expect(scopePill().getAttribute('aria-label')).toContain(String(figureCount))
    await openMenu(scopePill())
    expect(scopeItem('figure').textContent).toContain(String(figureCount))
    expect(scopeItem('document').textContent).toContain(String(total()))
  })

  it('无法核验的不进分桶：树底只有一行，展开是单独的一层', async () => {
    await seed()
    const issues = useValidationStore.getState().issues
    const base = issues[0]
    useValidationStore.setState({
      issues: [
        ...issues,
        {
          ...base,
          issueId: 'nv|1',
          ruleCode: 'panel-text-not-verifiable',
          severity: 'not_verifiable',
          propertyPath: null,
          fixKind: 'none',
        },
      ],
    })
    await mount(<ProblemPanel />)
    // 卡片只装需要处理的：那条无法核验的不算在任何一张卡片里
    const entry = container.querySelector<HTMLElement>('li[data-problem-card="unverifiable"]')!
    // 树底一行：虚线圆 + 名字 + 项数
    expect(container.querySelector('[data-problem-tree] > li:last-child')).toBe(entry)
    expect(entry.textContent).toContain('无法自动检查')
    expect(entry.dataset.problemCardCount).toBe('1')
    expect(entry.querySelector('svg.icon-circle-dashed')).toBeTruthy()
    // 需要处理的那一支展开，看不到无法核验的组
    await openCard('p1')
    expect(container.querySelector('[data-issue-group="panel-text-not-verifiable"]')).toBeNull()
    expect(
      Number(container.querySelector<HTMLElement>('li[data-problem-card="figure"]')!.dataset.problemCardCount),
    ).toBe(issues.length)
    await click(entry.querySelector(':scope > button')!)
    const inEntry = [...entry.querySelectorAll<HTMLElement>('[data-problem-tier]')].map((n) => n.dataset.problemTier)
    expect(inEntry).toEqual(['unverifiable'])
    expect(entry.querySelector('[data-issue-group="panel-text-not-verifiable"]')).not.toBeNull()
    expect(entry.querySelector('[data-issue-group="font-below-absolute-floor"]')).toBeNull()
  })
})

/* ------------------------ 卡片层（2026-09-28） ------------------------ */

/**
 * 一张三联图（照 Figure 2 的结构）：(a) + 它的色条轴、(b)、(c)。面板标签写在各自
 * 坐标系里；6 pt 的字 (a) 有两处（一处在色条轴上）、(c) 有两处，(b) 干净。
 */
const tagText = (gid: string, value: string, size: number, bbox: number[]) => ({
  gid,
  role: 'annotation',
  label: `文字 “${value}”`,
  bbox,
  draggable: false,
  editable: [
    { prop: 'text', type: 'text', value },
    { prop: 'fontsize', type: 'number', value: size },
  ],
})
const manifestTriptych = {
  stem: 'Figure2',
  size_mm: [80, 60],
  elements: [
    { gid: 'axes_0', role: 'axes', label: '子图 1', bbox: [0.1, 0.05, 0.75, 0.4], draggable: false, editable: [], follow_gids: ['axes_1'] },
    { gid: 'axes_1', role: 'axes', label: '色条轴', bbox: [0.88, 0.05, 0.03, 0.4], draggable: false, editable: [], is_colorbar: true },
    { gid: 'axes_2', role: 'axes', label: '子图 2', bbox: [0.1, 0.55, 0.35, 0.35], draggable: false, editable: [] },
    { gid: 'axes_3', role: 'axes', label: '子图 3', bbox: [0.5, 0.55, 0.35, 0.35], draggable: false, editable: [] },
    tagText('axes_0.texts_0', '(a)', 9, [0.1, 0.01, 0.03, 0.03]),
    tagText('axes_2.texts_0', '(b)', 9, [0.1, 0.51, 0.03, 0.03]),
    tagText('axes_3.texts_0', '(c)', 9, [0.5, 0.51, 0.03, 0.03]),
    tagText('axes_0.texts_1', 'Vacuum', 6, [0.15, 0.3, 0.1, 0.03]),
    tagText('axes_1.texts_0', '×10⁻³', 6, [0.88, 0.02, 0.03, 0.02]),
    tagText('axes_3.texts_1', 'Theory', 6, [0.6, 0.6, 0.1, 0.03]),
    tagText('axes_3.texts_2', 'FFT', 6, [0.6, 0.65, 0.1, 0.03]),
  ],
}

async function seedTriptych(withSecond = false) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_tri')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 140 }
    d.objects = withSecond ? [{ ...panel }, { ...panel2 }] : [{ ...panel }]
  })
  useAssetStore.setState({
    byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 }, 'Fig2.pdf': { id: 'Fig2.pdf', mtime: 1 } },
  } as never)
  seedExactRender(panel, manifestTriptych as never)
  if (withSecond) seedExactRender(panel2, manifest2 as never)
  runValidation()
}

const partCard = (tag: string) =>
  [...container.querySelectorAll<HTMLElement>('li[data-problem-card="part"]')].find((c) =>
    c.textContent?.includes(`子图 ${tag}`),
  )
const floorIssues = (gidPrefix: string) =>
  useValidationStore
    .getState()
    .issues.filter(
      (i) =>
        i.ruleCode === 'font-below-absolute-floor' &&
        i.objectRef.objectId === 'p1' &&
        i.objectRef.gid?.startsWith(gidPrefix),
    )
/** 这张组图（p1）上的问题数：「当前图」范围里卡片装的就是这些（页面级那条不在） */
const onP1 = () => useValidationStore.getState().issues.filter((i) => i.objectRef.objectId === 'p1').length

describe('卡片层：一张组图拆成子图（2026-09-28）', () => {
  it('当前图下按子图列卡片：名字取图里的「(a)」、色条轴的问题归 (a)，干净的 (b) 没有卡片', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    expect(floorIssues('axes_').length, '夹具没产出预期的四条字号问题').toBe(4)
    expect(rows(), '分桶默认收着，不铺逐条清单').toHaveLength(0)
    const names = [...container.querySelectorAll('li[data-problem-card] > button')].map((b) =>
      b.getAttribute('aria-label'),
    )
    expect(names.some((n) => n?.startsWith('子图 (a)：2 项'))).toBe(true)
    expect(names.some((n) => n?.startsWith('子图 (c)：2 项'))).toBe(true)
    expect(partCard('(b)')).toBeUndefined()
    expect(cardTotal()).toBe(onP1())
    // 尾随格静止时说阻断数与总数；各等级几项在可达名里，不列对象
    const c = partCard('(c)')!
    expect(c.querySelector(':scope > button')!.getAttribute('aria-label')).toContain('阻断 2')
    expect(c.querySelector('[data-problem-trail-rest]')!.textContent).toBe('2·2')
    expect(c.textContent).not.toContain('Theory')
  })

  it('子图卡片上的「修复 N」只修这一个子图，一次历史', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    const card = partCard('(c)')!
    const fix = card.querySelector<HTMLButtonElement>(':scope > [data-problem-trail] [data-problem-fix-count]')!
    expect(fix.textContent).toBe('修复 2')
    const past = useDocumentStore.getState().past.length
    engineSpecfix.mockClear()
    await click(fix)
    const only = engineSpecfix.mock.calls[0][4] as { gid: string }[]
    expect(only.map((o) => o.gid).sort()).toEqual(['axes_3.texts_1', 'axes_3.texts_2'])
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
  })

  it('就地展开子图只列它的行（不整页钻入）；再点一下收起', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    const toggle = partCard('(a)')!.querySelector<HTMLButtonElement>(':scope > button')!
    await click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(liveDrill()).toEqual({ kind: 'part', figure: 'p1', key: 'axes_0' })
    expect(rows()).toHaveLength(2)
    expect(rows().every((r) => partCard('(a)')!.contains(r))).toBe(true)
    // 别的子图还在原地（同一屏，不是换了一页）
    expect(partCard('(c)')).toBeTruthy()
    await click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(liveDrill()).toBeNull()
    expect(rows()).toHaveLength(0)
  })

  it('按类别：一类一张卡，项数加起来是全部；换分组方式退回总览', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    useUiStore.getState().setProblemDrill({ kind: 'part', figure: 'p1', key: 'axes_0' }, problemContextNow())
    await mount(<ProblemPanel />)
    expect(rows().length).toBeGreaterThan(0)
    await chooseView('category')
    expect(useUiStore.getState().problemView).toBe('category')
    expect(liveDrill()).toBeNull()
    expect(rows()).toHaveLength(0)
    const cats = [...container.querySelectorAll<HTMLElement>('li[data-problem-card="category"]')]
    expect(cats.length).toBeGreaterThan(0)
    expect(cats[0].textContent).toContain('文字')
    expect(cardTotal()).toBe(onP1())
  })

  it('整份排版：组图是一行 32px 的图头（默认展开、带「修复 N」），子图挂在它下面；普通图仍是一支', async () => {
    await seedTriptych(true)
    useUiStore.setState({ problemScope: 'document' })
    await mount(<ProblemPanel />)
    const head = container.querySelector<HTMLElement>('li[data-problem-card="figure"][data-problem-card-objects~="p1"]')!
    expect(head).toBeTruthy()
    expect(head.querySelector(':scope > button')!.className).toContain('h-8')
    expect(head.querySelector(':scope > button')!.getAttribute('aria-expanded')).toBe('true')
    expect(head.querySelectorAll('li[data-problem-card="part"]').length).toBeGreaterThanOrEqual(2)
    const fixFigure = head.querySelector(':scope > [data-problem-trail] [data-problem-fix-count]')
    expect(fixFigure?.textContent).toBe(`修复 ${floorIssues('axes_').length}`)
    expect(container.querySelector('li[data-problem-card="figure"][data-problem-card-objects~="p2"]')).toBeTruthy()
    expect(cardTotal()).toBe(useValidationStore.getState().issues.length)
  })

  it('从别处直达一条问题（openProblemAt）：进它所在的子图卡片，那一行是「当前」', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    const target = floorIssues('axes_3')[1]
    const { openProblemAt } = await import('@/lib/issueFocus')
    await act(async () => {
      openProblemAt(target, useValidationStore.getState().issues, 'p1')
    })
    expect(liveDrill()).toEqual({ kind: 'part', figure: 'p1', key: 'axes_3' })
    const current = rows().find((r) => r.getAttribute('aria-current') === 'true')
    expect(current, '「当前」那一行不在页面上').toBeTruthy()
    expect(cursorBar()?.textContent).toContain('第 2 / 2 项')
  })

  /** 从别处直达 (c) 里的一条：面板钻进 (c)、游标落在那一行 */
  const reachC = async (figureId = 'p1') => {
    const { openProblemAt } = await import('@/lib/issueFocus')
    await act(async () => {
      openProblemAt(floorIssues('axes_3')[1], useValidationStore.getState().issues, figureId)
    })
    expect(liveDrill()).toEqual({ kind: 'part', figure: 'p1', key: 'axes_3' })
  }
  /** 范围（胶囊菜单）与分组方式（「⋯」菜单）都算：用户显式换视图 */
  const pick = (label: '整份排版' | '当前图' | '按类别') =>
    label === '按类别' ? chooseView('category') : chooseScope(label === '整份排版' ? 'document' : 'figure')

  it('直达过一条之后，用户切范围：留在新范围的总览，不被游标钻回那张卡片', async () => {
    await seedTriptych(true)
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    await reachC()
    await pick('整份排版')
    expect(useUiStore.getState().problemScope).toBe('document')
    expect(liveDrill()).toBeNull()
    expect(rows(), '总览不展开任何一支').toHaveLength(0)
    expect(cardTotal()).toBe(useValidationStore.getState().issues.length)
    await pick('当前图')
    expect(liveDrill()).toBeNull()
    expect(partCard('(c)')).toBeTruthy()
  })

  it('单图详情里直达过一条之后，用户换分组方式：留在「按类别」的总览', async () => {
    // 单子图的普通图没有分桶层（规则直接在顶层）——游标还指着那一条
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    const { openProblemAt } = await import('@/lib/issueFocus')
    const target = useValidationStore.getState().issues.find((i) => i.objectRef.objectId === 'p1')!
    await act(async () => {
      openProblemAt(target, useValidationStore.getState().issues, 'p1')
    })
    expect(rows().find((r) => r.getAttribute('aria-current') === 'true')).toBeTruthy()
    await pick('按类别')
    expect(useUiStore.getState().problemView).toBe('category')
    expect(liveDrill()).toBeNull()
    expect(rows(), '总览不列逐条清单').toHaveLength(0)
    expect(container.querySelectorAll('li[data-problem-card="category"]').length).toBeGreaterThan(0)
  })

  it('面板里点过一行（游标来自面板自己）再切范围：同样回到总览', async () => {
    await seedTriptych(true)
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    await click(partCard('(c)')!.querySelector(':scope > button')!)
    await click(rows()[0])
    expect(useUiStore.getState().problemCursor).not.toBeNull()
    await pick('整份排版')
    expect(liveDrill()).toBeNull()
    expect(rows()).toHaveLength(0)
  })

  it('直达过一条之后，导出对话框把用户交回问题面板（openProblems）：交回的是总览', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    await reachC()
    const { openProblems } = await import('@/lib/issueFocus')
    await act(async () => {
      openProblems({ severities: ['error'] })
    })
    expect(liveDrill()).toBeNull()
    expect(rows(), '总览不列逐条清单').toHaveLength(0)
  })

  it('抽屉开着换了「当前图」（快编另一张）：退回那张图的总览，不留着上一张图的卡片冒充「都处理完了」', async () => {
    await seedTriptych(true)
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    await click(partCard('(a)')!.querySelector(':scope > button')!)
    await click(rows()[0])
    expect(useUiStore.getState().problemCursor).not.toBeNull()
    // 点过一行，定位已把 p1 设成快编中的图（它压过 elementPanelId）：换图就换这一个
    await act(async () => {
      useWorkspaceStore.getState().enterFastEdit('p2')
    })
    expect(liveDrill()).toBeNull()
    expect(cursorBar(), '上一张图的游标不该跟过来').toBeNull()
    const onP2 = useValidationStore.getState().issues.filter((i) => i.objectRef.objectId === 'p2').length
    expect(onP2, '夹具里 p2 得有问题').toBeGreaterThan(0)
    // p2 是拆不出子图的普通图：它的总览就是它自己的清单
    expect(rows()).toHaveLength(onP2)
    // 换回组图：回到它的子图卡片层，不是刚才那张 (a)
    await act(async () => {
      useWorkspaceStore.getState().enterFastEdit('p1')
    })
    expect(liveDrill()).toBeNull()
    expect(partCard('(a)')).toBeTruthy()
  })

  it('直达另一张图上的一条（定位进了那张图的快编）：进那张图，那一行是「当前」', async () => {
    await seedTriptych(true)
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    await click(partCard('(a)')!.querySelector(':scope > button')!)
    const target = useValidationStore.getState().issues.find((i) => i.objectRef.objectId === 'p2')!
    const { openProblemAt } = await import('@/lib/issueFocus')
    await act(async () => {
      openProblemAt(target, useValidationStore.getState().issues, 'p2')
      useWorkspaceStore.getState().enterFastEdit('p2')
    })
    expect(useUiStore.getState().problemCursor?.issueId).toBe(target.issueId)
    expect(rows().find((r) => r.getAttribute('aria-current') === 'true')).toBeTruthy()
  })

  it('离开「问题」页签期间换了项目，回来是新项目的总览，不带着上一个项目点进的卡片', async () => {
    await seedTriptych(true)
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    await click(partCard('(c)')!.querySelector(':scope > button')!)
    expect(liveDrill()).toEqual({ kind: 'part', figure: 'p1', key: 'axes_3' })
    // 切到别的页签：面板被卸载，它的 effect 看不见接下来的换项目
    await act(async () => {
      root.render(<TooltipProvider><div /></TooltipProvider>)
    })
    await seedTriptych(true)
    useUiStore.setState({ elementPanelId: 'p1' })
    await act(async () => {
      root.render(<TooltipProvider><ProblemPanel /></TooltipProvider>)
    })
    expect(rows(), '不该还在上一个项目的 (c) 里').toHaveLength(0)
    expect(partCard('(a)')).toBeTruthy()
  })

  it('展开的那一支被等级筛选筛光：它从树上暂时消失、人不被踢回总览；取消筛选它带着展开态回来', async () => {
    await seedTriptych(true)
    useUiStore.setState({ problemScope: 'document' })
    await mount(<ProblemPanel />)
    await click(partCard('(c)')!.querySelector(':scope > button')!)
    const inCard = new Set(
      useValidationStore
        .getState()
        .issues.filter((i) => i.objectRef.objectId === 'p1' && i.objectRef.gid?.startsWith('axes_3'))
        .map((i) => i.severity),
    )
    const other = useValidationStore.getState().issues.find((i) => !inCard.has(i.severity))
    expect(other, '夹具里得有一个 (c) 没有的等级').toBeTruthy()
    await act(async () => {
      useUiStore.getState().setProblemFilter([other!.severity])
    })
    expect(liveDrill(), '在一支里筛选不把人踢回总览').toEqual({ kind: 'part', figure: 'p1', key: 'axes_3' })
    expect(partCard('(c)'), '筛掉的那一支不画空壳').toBeUndefined()
    await act(async () => {
      useUiStore.getState().setProblemFilter(null)
    })
    expect(partCard('(c)')!.querySelector(':scope > button')!.getAttribute('aria-expanded')).toBe('true')
    expect(rows().length).toBeGreaterThan(0)
  })

  it('卡片与游标是派生的：现场换了，读出来就是 null（不靠面板里的 effect，别处读也一样）', async () => {
    await seedTriptych(true)
    useUiStore.setState({ elementPanelId: 'p1' })
    function Probe() {
      const nav = useScopedProblems()
      return <i data-drill={nav.drill ? drillKey(nav.drill) : ''} />
    }
    await mount(<Probe />)
    const probe = () => container.querySelector('i')!.getAttribute('data-drill')
    await act(async () => {
      useUiStore.getState().setProblemDrill({ kind: 'part', figure: 'p1', key: 'axes_3' }, problemContextNow())
    })
    expect(probe()).toBe('part:p1:axes_3')
    await act(async () => {
      useWorkspaceStore.getState().enterFastEdit('p2')
    })
    expect(probe(), '换了当前图').toBe('')
  })

  it('缩略图走 /api/render 时一次失败先按退避表重取，不立刻换成图标；真取不到才换', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    const thumb = () => partCard('(c)')!.querySelector<HTMLImageElement>('[data-problem-thumb] img')
    const base = thumb()?.getAttribute('src') ?? ''
    expect(base, 'PDF 的缩略图走 /api/render').toContain('/api/render?')
    vi.useFakeTimers()
    try {
      for (let i = 0; i < RENDER_RETRY_DELAYS_MS.length; i++) {
        await act(async () => {
          thumb()!.dispatchEvent(new Event('error'))
        })
        expect(thumb(), `第 ${i + 1} 次失败就换成了图标`).toBeTruthy()
        await act(async () => {
          vi.advanceTimersByTime(RENDER_RETRY_DELAYS_MS[i])
        })
        expect(thumb()!.getAttribute('src')).toContain(`r=${i + 1}`)
      }
      await act(async () => {
        thumb()!.dispatchEvent(new Event('error'))
      })
      expect(thumb(), '退避表用完仍失败：退回图标').toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('native 图（tavotto run）的问题不进任何批量修：子图、图头、组头、「全部修复」口径一致（#690 评审）', async () => {
    await seedTriptych(true)
    useUiStore.setState({ problemScope: 'document' })
    await mount(<ProblemPanel />)
    const fixOf = (el: Element | null | undefined) => el?.querySelector(':scope > [data-problem-trail] [data-problem-fix-count]')
    expect(fixOf(partCard('(c)')), '对照：普通图的子图有「修复 N」').toBeTruthy()
    const autofix = () => container.querySelector('[data-problem-autofix]')?.textContent ?? ''
    const before = autofix()
    try {
      // 素材档案晚到：p1（Fig1.pdf）原来是 tavotto run 打开的 live 图
      await act(async () => {
        useRuntimeAssetStore.setState({
          byId: { 'Fig1.pdf': { status: 'fresh', cached: true, registered: true, profile: 'native', checked: true } },
        } as never)
      })
      expect(fixOf(partCard('(c)')), '子图不给「修复 N」').toBeFalsy()
      expect(fixOf(container.querySelector('li[data-problem-card="figure"][data-problem-card-objects~="p1"]'))).toBeFalsy()
      expect(autofix(), '「全部修复」的计数不含 native 图上的').not.toBe(before)
      await click(partCard('(c)')!.querySelector(':scope > button')!)
      expect(partCard('(c)')!.querySelector('[data-issue-group-head] [data-problem-fix-count]'), '组头也不给').toBeNull()
      // 逐行按钮仍是禁用的那颗（口径与批量一致）
      expect(container.querySelectorAll('[data-fix-native-unsupported]').length).toBeGreaterThan(0)
    } finally {
      useRuntimeAssetStore.setState({ byId: {} })
    }
  })

  it('外部直达仍会先换范围再钻进卡片：从「整份排版」的总览出发也落到 (c) 的那一行', async () => {
    await seedTriptych(true)
    useUiStore.setState({ elementPanelId: 'p1', problemScope: 'document' })
    await mount(<ProblemPanel />)
    await reachC()
    expect(useUiStore.getState().problemScope).toBe('figure')
    expect(rows().find((r) => r.getAttribute('aria-current') === 'true')).toBeTruthy()
  })
})
