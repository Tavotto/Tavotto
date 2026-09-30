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
import { PREVIEW_ROWS, ProblemPanel } from './ProblemPanel'
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
/** 卡片层上各卡片的项数之和（整份排版范围里它应当等于全部问题数） */
const cardTotal = () =>
  [...container.querySelectorAll<HTMLElement>('li[data-problem-card]')].reduce(
    (n, c) => n + Number(c.dataset.problemCardCount),
    0,
  )

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

  it('技术详情里有 gid，而且默认是收起的', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await openCard()
    const details = container.querySelector('details')!
    expect(details.open).toBe(false)
    expect(details.textContent).toContain('axes_0.xticks')
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
    const all = container.querySelector<HTMLButtonElement>('[data-problem-autofix] button')!
    await click(all)
    expect(all.disabled).toBe(true)
    expect(all.textContent).toBe('正在修复…')
    expect(byText('修复')?.hasAttribute('disabled')).toBe(true)
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
    const fixButtons = buttons().filter((b) => b.textContent === '修复').length
    expect(fixButtons).toBeLessThanOrEqual(fixable)
    expect(fixButtons).toBeGreaterThan(0)
  })
})

describe('左轨入口', () => {
  it('有阻断项时图标上是一颗中性小点，不挂红底数字；问题数在可达名里（2026-09-28 用户反馈）', async () => {
    await seed() // 两条都是阻断（字号低于绝对下限）
    useUiStore.setState({ leftOpen: false })
    await mount(<LeftRail />)
    const entry = container.querySelector('[data-rail="problems"]')!
    expect(entry).toBeTruthy()
    const n = useValidationStore.getState().issues.length
    const dot = entry.querySelector('[data-rail-blocking]')
    expect(dot, '有阻断项时要有提示').toBeTruthy()
    expect(dot!.className).not.toContain('danger')
    // 轨钮下面写的是短名（2026-09-30 重设计），不是数字
    expect(entry.textContent?.trim(), '轨道上不再写数字').not.toMatch(/\d/)
    expect(entry.getAttribute('aria-label')).toContain(String(n))
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
// 「当前图 / 整份排版」是看哪一页的清单——页签（role=tab），不是取值（radio）
const radios = () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
const checkedRadio = () => radios().find((r) => r.getAttribute('aria-selected') === 'true')
const radioNamed = (s: string) => radios().find((r) => r.textContent?.includes(s))!
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
    await click(byText('全部修复')!)
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
    expect(checkedRadio()?.textContent).toContain('当前图')
    await openCard()
    expect(text()).toContain('X 轴刻度')
    expect(text()).not.toContain('Y 轴刻度')
    await click(radioNamed('整份排版'))
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
    expect(checkedRadio()?.textContent).toContain('当前图')
    await openCard()
    expect(text()).toContain('Y 轴刻度')
    expect(text()).not.toContain('X 轴刻度')
    // 图名写在「当前图」页签的 title 里（2026-09-15 打磨批次 E：不再在页签下面挂一行图名）
    expect(text()).not.toContain('Fig2.pdf')
    expect(checkedRadio()?.getAttribute('title')).toContain('Fig2.pdf')
  })

  it('没有正在编辑或选中的图：「当前图」灰掉并说明原因，实际看整份排版', async () => {
    await seedThree()
    useUiStore.setState({ problemScope: 'figure' })
    await mount(<ProblemPanel />)
    const fig = radioNamed('当前图')
    expect(fig.disabled).toBe(true)
    expect(fig.getAttribute('title')).toContain('没有正在编辑或选中的图')
    expect(checkedRadio()?.textContent).toContain('整份排版')
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

  it('计数条按范围算；抽屉标题不再带计数（二审 C2：页签已把两个范围各说一遍）', async () => {
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<LeftPanel />)
    // 标题行只有「问题」两个字：2 / 3 都不在标题里，范围数字只在页签与计数条
    const heading = container.querySelector('h2')!
    // 标题旁没有计数节点：h2 之后紧跟的是占位的 flex-1，不是 type-meta 的数字
    expect(heading.nextElementSibling?.textContent?.trim()).toBe('')
    expect(severityChip().getAttribute('aria-label')).toContain('2')
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
    expect(rows()[0].textContent).toContain('当前')
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
    // 到底了：下一项不可按
    expect(byLabel('下一项')!.disabled).toBe(true)
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
  it('「当前图」与「整份排版」各带自己的数，两个数同时看得见', async () => {
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    const fig = radioNamed('当前图')
    const doc = radioNamed('整份排版')
    const figureCount = useValidationStore
      .getState()
      .issues.filter((i) => i.objectRef.objectId === 'p1').length
    expect(figureCount).toBeGreaterThan(0)
    expect(figureCount).toBeLessThan(total())
    expect(fig.textContent).toContain(String(figureCount))
    expect(doc.textContent).toContain(String(total()))
    // 可达名也带数：读屏不用切过去才知道那一档有几条
    expect(fig.getAttribute('aria-label')).toContain(String(figureCount))
    expect(doc.getAttribute('aria-label')).toContain(String(total()))
  })

  it('无法核验的不进卡片：卡片层只有一行入口，点进去是单独的一层', async () => {
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
    expect(cardTotal()).toBe(issues.length)
    const entry = container.querySelector<HTMLButtonElement>('button[data-problem-card="unverifiable"]')!
    expect(entry.textContent).toContain('1 项无法自动检查')
    // 需要处理的那张卡片点进去，看不到无法核验的组
    await openCard()
    expect(container.querySelector('[data-issue-group="panel-text-not-verifiable"]')).toBeNull()
    await click(container.querySelector('[data-problem-back]')!)
    await click(container.querySelector('button[data-problem-card="unverifiable"]')!)
    const tiers = [...container.querySelectorAll<HTMLElement>('[data-problem-tier]')].map(
      (n) => n.dataset.problemTier,
    )
    expect(tiers).toEqual(['unverifiable'])
    expect(container.querySelector('[data-issue-group="panel-text-not-verifiable"]')).not.toBeNull()
    expect(container.querySelector('[data-issue-group="font-below-absolute-floor"]')).toBeNull()
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
    expect(rows(), '卡片层不列逐条清单').toHaveLength(0)
    const names = [...container.querySelectorAll('li[data-problem-card] button')].map((b) =>
      b.getAttribute('aria-label'),
    )
    expect(names.some((n) => n?.startsWith('子图 (a)：2 项'))).toBe(true)
    expect(names.some((n) => n?.startsWith('子图 (c)：2 项'))).toBe(true)
    expect(partCard('(b)')).toBeUndefined()
    expect(cardTotal()).toBe(onP1())
    // 卡片副标题说阻断数与最主要的检查项，不列对象
    expect(partCard('(c)')!.textContent).toContain('阻断 2')
    expect(partCard('(c)')!.textContent).not.toContain('Theory')
  })

  it('子图卡片上的「修复 N」只修这一个子图，一次历史', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    const card = partCard('(c)')!
    const fix = [...card.querySelectorAll('button')].find((b) => b.textContent === '修复 2')!
    expect(fix).toBeTruthy()
    const past = useDocumentStore.getState().past.length
    engineSpecfix.mockClear()
    await click(fix)
    const only = engineSpecfix.mock.calls[0][4] as { gid: string }[]
    expect(only.map((o) => o.gid).sort()).toEqual(['axes_3.texts_1', 'axes_3.texts_2'])
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
  })

  it('点进子图只列它的行；返回回到卡片层', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    await click(partCard('(a)')!.querySelector('button')!)
    expect(liveDrill()).toEqual({ kind: 'part', figure: 'p1', key: 'axes_0' })
    const gids = rows().map((r) => r.closest('li')?.querySelector('details')?.textContent ?? '')
    expect(rows()).toHaveLength(2)
    expect(gids.every((g) => g.includes('axes_0') || g.includes('axes_1'))).toBe(true)
    expect(text()).toContain('修复此子图')
    await click(container.querySelector('[data-problem-back]')!)
    expect(liveDrill()).toBeNull()
    expect(partCard('(a)')).toBeTruthy()
  })

  it('按类别：一类一张卡，项数加起来是全部；换分组方式退回总览', async () => {
    await seedTriptych()
    useUiStore.setState({ elementPanelId: 'p1' })
    useUiStore.getState().setProblemDrill({ kind: 'part', figure: 'p1', key: 'axes_0' }, problemContextNow())
    await mount(<ProblemPanel />)
    await click(container.querySelector('[data-problem-back]')!)
    const radio = [...container.querySelectorAll<HTMLElement>('[role="radio"]')].find((r) =>
      r.textContent?.includes('按类别'),
    )!
    await click(radio)
    expect(useUiStore.getState().problemView).toBe('category')
    const cats = [...container.querySelectorAll<HTMLElement>('li[data-problem-card="category"]')]
    expect(cats.length).toBeGreaterThan(0)
    expect(cats[0].textContent).toContain('文字')
    expect(cardTotal()).toBe(onP1())
  })

  it('整份排版：组图有一行图头带「修复本图」，子图卡片挂在它下面；普通图仍是一张卡', async () => {
    await seedTriptych(true)
    useUiStore.setState({ problemScope: 'document' })
    await mount(<ProblemPanel />)
    const head = container.querySelector('li[data-problem-figure]')!
    expect(head).toBeTruthy()
    expect(head.querySelectorAll('li[data-problem-card="part"]').length).toBeGreaterThanOrEqual(2)
    const fixFigure = [...head.querySelectorAll('button')].find((b) => b.textContent?.startsWith('修复本图'))
    expect(fixFigure?.textContent).toContain(String(floorIssues('axes_').length))
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
  /** 范围页签（tab）与分组开关（radio）都算：用户显式换视图 */
  const pick = (label: string) =>
    click(
      [...container.querySelectorAll<HTMLElement>('[role="radio"], [role="tab"]')].find((r) =>
        r.textContent?.includes(label),
      )!,
    )

  it('直达过一条之后，用户切范围：留在新范围的总览，不被游标钻回那张卡片', async () => {
    await seedTriptych(true)
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    await reachC()
    await pick('整份排版')
    expect(useUiStore.getState().problemScope).toBe('document')
    expect(liveDrill()).toBeNull()
    expect(rows(), '总览不列逐条清单').toHaveLength(0)
    expect(cardTotal()).toBe(useValidationStore.getState().issues.length)
    await pick('当前图')
    expect(liveDrill()).toBeNull()
    expect(partCard('(c)')).toBeTruthy()
  })

  it('单图详情里直达过一条之后，用户换分组方式：留在「按类别」的总览', async () => {
    // 单子图的普通图没有卡片层，详情头上就是分组开关——游标还指着那一条
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
    await click(partCard('(c)')!.querySelector('button')!)
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
    await click(partCard('(a)')!.querySelector('button')!)
    await click(rows()[0])
    expect(useUiStore.getState().problemCursor).not.toBeNull()
    // 点过一行，定位已把 p1 设成快编中的图（它压过 elementPanelId）：换图就换这一个
    await act(async () => {
      useWorkspaceStore.getState().enterFastEdit('p2')
    })
    expect(liveDrill()).toBeNull()
    expect(cursorBar(), '上一张图的游标不该跟过来').toBeNull()
    expect(text()).not.toContain('这里的问题都处理完了')
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
    await click(partCard('(a)')!.querySelector('button')!)
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
    await click(partCard('(c)')!.querySelector('button')!)
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
    expect(container.querySelector('[data-problem-back]'), '不该还在上一个项目的 (c) 里').toBeNull()
    expect(partCard('(a)')).toBeTruthy()
    expect(text()).not.toContain('这里的问题都处理完了')
  })

  it('卡片里的问题被等级筛选筛光：说「当前筛选下没有问题」、给「显示全部」，不冒充「都处理完了」', async () => {
    await seedTriptych(true)
    useUiStore.setState({ problemScope: 'document' })
    await mount(<ProblemPanel />)
    await click(partCard('(c)')!.querySelector('button')!)
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
    expect(liveDrill(), '在卡片里筛选不把人踢回总览').toEqual({ kind: 'part', figure: 'p1', key: 'axes_3' })
    expect(text()).toContain('当前筛选下没有问题')
    expect(text()).not.toContain('这里的问题都处理完了')
    await click(byText('显示全部')!)
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

  it('native 图（tavotto run）的问题不进任何批量修：卡片、图头、详情头、组头、「全部处理」口径一致（#690 评审）', async () => {
    await seedTriptych(true)
    useUiStore.setState({ problemScope: 'document' })
    await mount(<ProblemPanel />)
    const fixOn = (el: Element | null | undefined, prefix: string) =>
      [...(el?.querySelectorAll('button') ?? [])].find((b) => b.textContent?.startsWith(prefix))
    expect(fixOn(partCard('(c)'), '修复'), '对照：普通图的卡片有「修复 N」').toBeTruthy()
    const autofix = () => container.querySelector('[data-problem-autofix]')?.textContent ?? ''
    const before = autofix()
    try {
      // 素材档案晚到：p1（Fig1.pdf）原来是 tavotto run 打开的 live 图
      await act(async () => {
        useRuntimeAssetStore.setState({
          byId: { 'Fig1.pdf': { status: 'fresh', cached: true, registered: true, profile: 'native', checked: true } },
        } as never)
      })
      expect(fixOn(partCard('(c)'), '修复'), '子图卡片不给「修复 N」').toBeUndefined()
      expect(fixOn(container.querySelector('li[data-problem-figure]'), '修复本图')).toBeUndefined()
      expect(autofix(), '「全部处理」的计数不含 native 图上的').not.toBe(before)
      await click(partCard('(c)')!.querySelector('button')!)
      expect(text()).not.toContain('修复此子图')
      expect(byText('全部修复')).toBeFalsy()
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
