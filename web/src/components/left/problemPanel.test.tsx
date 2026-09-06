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
import { LeftPanel } from './LeftPanel'
import { LeftRail } from './LeftRail'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
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
  useUiStore.setState({
    problemFilter: null,
    problemScope: null,
    problemCursor: null,
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

describe('这一轮查砸了、上一轮的结果还留着', () => {
  const list = () => container.querySelector('ul[aria-label]')

  it('失败提示与**那份留下来的清单**同时在场，不是二选一', async () => {
    await seed() // ready=true，issues 非空
    const kept = useValidationStore.getState().issues.length
    expect(kept).toBeGreaterThan(0)
    // 下一轮查砸了，`validationStore` 刻意把上一轮的结果留着
    useValidationStore.setState({ failed: true })
    await mount(<ProblemPanel />)

    // 失败要说出来——那句话本身就承诺了「下面列的是上一次的结果」
    expect(text()).toContain('下面列的是上一次查出来的结果')
    // ……那就真的得列出来。它们仍算在计数条与导出摘要里，
    // 藏起来就成了「看得见数字、找不到东西」
    expect(list(), '整屏被换成错误空态，留下来的问题在唯一一份清单里翻不到').toBeTruthy()
    expect(list()!.querySelectorAll('[data-issue-row]').length).toBe(kept)
    expect(text()).toContain('X 刻度文字')
  })

  it('上一轮什么都没有时仍然只出错误空态，不摆一条没有清单的横幅', async () => {
    useValidationStore.setState({ ready: true, failed: true, issues: [], results: [] })
    await mount(<ProblemPanel />)
    expect(text()).toContain('这一次没查成')
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
const radios = () => [...container.querySelectorAll<HTMLButtonElement>('[role="radio"]')]
const checkedRadio = () => radios().find((r) => r.getAttribute('aria-checked') === 'true')
const radioNamed = (s: string) => radios().find((r) => r.textContent?.includes(s))!
const cursorBar = () => container.querySelector('[data-problem-cursor]')
/** 全文档的问题数（三张图的 + 页面级那条：80×200 的页面比例不合规范） */
const total = () => useValidationStore.getState().issues.length
const severityChip = () => buttons().find((b) => (b.getAttribute('aria-label') ?? '').includes('只看'))!

describe('按规则聚合（审计 T09）', () => {
  it('同一条规则合成一组：标题只在组头说一遍，组头给受影响对象数，行里各说各的数字', async () => {
    await seed() // 两条 font-below-absolute-floor：xticks 6 pt、xlabel 7 pt
    await mount(<ProblemPanel />)
    const groups = container.querySelectorAll('[data-issue-group]')
    expect(groups.length).toBe(1)
    expect(groups[0].getAttribute('data-issue-group')).toBe('font-below-absolute-floor')
    expect(text().split('字号低于绝对下限').length - 1, '标题逐行重复').toBe(1)
    expect(text()).toContain('2 个对象')
    expect(rows().length).toBe(2)
    expect(text()).toMatch(/6\.00pt\s*→\s*大于 8pt/)
    expect(text()).toMatch(/7\.00pt\s*→\s*大于 8pt/)
    // 等级不只靠颜色：组头写着等级文字
    expect(groups[0].textContent).toContain('阻断')
  })

  it('组头可折叠：折起来行就不在，展开又回来', async () => {
    await seed()
    await mount(<ProblemPanel />)
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
    const past = useDocumentStore.getState().past.length
    await click(byText('修复 2 项')!)
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(p.overrides.length).toBe(2)
    useDocumentStore.getState().undo()
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).overrides).toEqual([])
  })
})

describe('范围：当前图 / 整个文档（审计 T09）', () => {
  it('在图内编辑里打开面板，默认只看这张图；切到整个文档才列别的图', async () => {
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<ProblemPanel />)
    expect(checkedRadio()?.textContent).toContain('当前图')
    expect(text()).toContain('X 刻度文字')
    expect(text()).not.toContain('Y 刻度文字')
    await click(radioNamed('整个文档'))
    expect(useUiStore.getState().problemScope).toBe('document')
    expect(text()).toContain('Y 刻度文字')
    // 页面级那条（主语是整张画布）也只在「整个文档」里出现
    expect(rows().length).toBe(total())
    expect(text()).toContain('页面比例不合规范')
  })

  it('快速编辑中的那张图也算「当前图」（没有进图内编辑也一样）', async () => {
    await seedThree()
    useWorkspaceStore.getState().enterFastEdit('p2')
    await mount(<ProblemPanel />)
    expect(checkedRadio()?.textContent).toContain('当前图')
    expect(text()).toContain('Y 刻度文字')
    expect(text()).not.toContain('X 刻度文字')
    // 图名就在范围旁边，用户知道「当前图」指的是谁
    expect(text()).toContain('Fig2.pdf')
  })

  it('没有正在编辑或选中的图：「当前图」灰掉并说明原因，实际看整个文档', async () => {
    await seedThree()
    useUiStore.setState({ problemScope: 'figure' })
    await mount(<ProblemPanel />)
    const fig = radioNamed('当前图')
    expect(fig.disabled).toBe(true)
    expect(fig.getAttribute('title')).toContain('没有正在编辑或选中的图')
    expect(checkedRadio()?.textContent).toContain('整个文档')
    expect(rows().length).toBe(total())
  })

  it('范围裁到一张没有问题的图：说「这张图上没有问题」并给回整个文档的出口，不冒充「未发现问题」', async () => {
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p3' })
    await mount(<ProblemPanel />)
    expect(text()).toContain('这张图上没有问题')
    expect(text()).toContain(`整个文档里还有 ${total()} 项问题`)
    expect(text()).not.toContain('未发现问题')
    await click(byText('整个文档')!)
    expect(rows().length).toBe(total())
  })

  it('计数条按范围算；抽屉标题的计数与面板同一个范围', async () => {
    await seedThree()
    useUiStore.setState({ elementPanelId: 'p1' })
    await mount(<LeftPanel />)
    // 标题里的计数 = 当前图的 2 条，不是全文档的 3 条
    const heading = container.querySelector('h2')!
    expect(heading.parentElement?.textContent).toContain('2')
    expect(heading.parentElement?.textContent).not.toContain(String(total()))
    expect(severityChip().getAttribute('aria-label')).toContain('2')
  })
})

describe('定位后清单留在原地（审计 T09）', () => {
  it('点一行：左栏仍是「问题」页，那行带「当前」标记，底部给第几条与「下一项」', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await click(rows()[0])
    expect(useUiStore.getState().leftTab, '元素树把问题清单顶掉了').toBe('problems')
    expect(useUiStore.getState().elementPanelId).toBe('p1')
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xticks'])
    expect(rows()[0].getAttribute('aria-current')).toBe('true')
    expect(rows()[0].textContent).toContain('当前')
    expect(rows()[1].getAttribute('aria-current')).toBeNull()
    expect(cursorBar()?.textContent).toContain('第 1 / 2 项')
    expect(cursorBar()?.textContent).toContain('X 刻度文字')

    await click(byText('下一项')!)
    expect(rows()[1].getAttribute('aria-current')).toBe('true')
    expect(rows()[0].getAttribute('aria-current')).toBeNull()
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xlabel'])
    expect(cursorBar()?.textContent).toContain('第 2 / 2 项')
    // 到底了：下一项不可按
    expect(byText('下一项')!.disabled).toBe(true)
  })

  it('当前那条修好消失之后，「下一项」指向顶上来的那条，不必重开清单', async () => {
    await seed()
    await mount(<ProblemPanel />)
    await click(rows()[0])
    const [, second] = useValidationStore.getState().issues
    await act(async () => useValidationStore.setState({ issues: [second] }))
    expect(rows().length).toBe(1)
    expect(cursorBar()?.textContent).toContain('已处理，还剩 1 项')
    await click(byText('下一项')!)
    expect(rows()[0].getAttribute('aria-current')).toBe('true')
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.xlabel'])
  })

  it('清单空了游标就撤掉；「结束逐项处理」也能手动撤掉', async () => {
    await seed()
    await mount(<ProblemPanel />)
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
    const row = container.querySelector(
      '[data-issue-row][data-issue-rule="font-below-absolute-floor"][data-issue-object="p1"]',
    )
    expect(row).toBeTruthy()
  })
})
