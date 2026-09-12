/**
 * AI 执行器 / 模型与推理强度弹层。
 *
 * 要钉住的（修改前见
 * `docs/ux/img/ux-consistency-pass/before/zh-1440-ai-popover.png`：
 * 六档等宽按钮在 232px 弹层里两头都被切掉，正常状态还常驻一段快照说明）：
 *   1. 执行器与模型是**一个**紧凑选择器（审计 T37）；选不出第二项时不摆
 *      一个选不动的控件，但那一项写的是什么仍要看得见；
 *   2. 模型清单来自 caps，为空时不伪造模型；
 *   3. 推理强度是**真实能力数组驱动**的离散滑杆，档位数 = 数组长度，
 *      滑到第 i 格写的就是 efforts[i]，绝不生成数组里没有的值；
 *   4. 只有一档时滑杆不可调；一档都没有时整块不出现；
 *   5. 键盘方向键可调；
 *   6. 正常状态不常驻快照 / CLI / 实现说明（2026-09-11 起弹层里也没有技术详情折叠，
 *      那些内容在设置 → 编码 Agent 的详情页）；
 *   7. 切 Agent 各自保留模型与强度偏好。
 *
 * 合并只是**呈现**：底下仍是 aiStore 的 agent 与 models[agent] 两个字段。
 * 「切到 B 再切回 A，A 的模型还是我上次选的」这条正是那个结构在被检验。
 * 推理强度的滑杆按需展开，**当前档位在收起时就写着**——藏起来的是控件不是值。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { AiCapabilities } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, claudeCaps } from '@/components/settings/testCaps'
import { useAiStore } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ScopeAgentContent } from './AiPanel'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

/** codex 的真实形状：一个模型 + 六档强度（含用户配置带出来的 xhigh） */
const codexSix = agentCaps({
  models: ['gpt-5.6-sol'],
  default_model: 'gpt-5.6-sol',
  efforts: ['minimal', 'low', 'medium', 'high', 'max', 'xhigh'],
  default_effort: 'xhigh',
})

const capsOf = (...agents: AiCapabilities['agents']): AiCapabilities =>
  ({ agents, endpoints: [] }) as unknown as AiCapabilities

const panelOf = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 75,
    fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 75,
    script: '/tmp/figs/fig1_kinetics.py', overrides: [],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ScopeAgentContent
          panel={panelOf()}
          element={null}
          axes={null}
          scope="figure"
          scopes={['figure']}
        />
      </TooltipProvider>,
    )
  })
}

const textOf = () => host.textContent ?? ''
const buttons = () => Array.from(host.querySelectorAll('button'))
const range = () => host.querySelector('input[type="range"]') as HTMLInputElement | null
/** 合成后的「执行器与模型」选择器；只有一项可选时它整个不存在 */
const pairTrigger = () =>
  host.querySelector('[role="combobox"][aria-label="执行器与模型"]') as HTMLElement | null
/** Radix 的选项挂在 body 的 Portal 上，不在 host 里 */
const pairOptions = async (): Promise<HTMLElement[]> => {
  await act(async () => {
    pairTrigger()!.click()
  })
  return [...document.body.querySelectorAll('[role="option"]')] as HTMLElement[]
}
const pickPair = async (label: string) => {
  const opts = await pairOptions()
  const hit = opts.find((o) => o.textContent?.includes(label))
  expect(hit, `选项里没有「${label}」`).toBeTruthy()
  await act(async () => {
    hit!.click()
  })
}
/** 推理强度默认收起：要动滑杆先展开 */
const openEffort = async () => {
  const btn = buttons().find((b) => b.textContent?.includes('推理强度'))!
  await act(async () => {
    btn.click()
  })
}

function setRange(el: HTMLInputElement, v: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, v)
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ai')
  useAiStore.setState({ caps: null, agent: null, models: {}, efforts: {} })
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
  })
})

/* ------------------------------- Provider -------------------------------- */

describe('执行器与模型（一个选择器）', () => {
  it('只有一项可选时不摆选择器，但那一项写的是什么仍看得见', async () => {
    // 一个 Agent + 一个模型 = 一种组合：选择器换成一行静态文字
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    expect(pairTrigger()).toBeNull()
    expect(textOf()).toContain('Codex · gpt-5.6-sol')
  })

  it('两个 Agent 的模型摊进同一个选择器，每项都带执行器名', async () => {
    useAiStore.setState({ caps: capsOf(codexSix, claudeCaps()) })
    await mount()
    expect(pairTrigger()).toBeTruthy()
    const labels = (await pairOptions()).map((o) => o.textContent?.trim())
    expect(labels).toEqual([
      'Codex · gpt-5.6-sol',
      'Claude Code · sonnet',
      'Claude Code · opus',
    ])
  })

  it('选一项：执行器与模型两个字段各写各的，不是一个合并出来的新字段', async () => {
    useAiStore.setState({ caps: capsOf(codexSix, claudeCaps()) })
    await mount()
    await pickPair('Claude Code · opus')
    expect(useAiStore.getState().agent).toBe('claude')
    expect(useAiStore.getState().models.claude).toBe('opus')
    // codex 的模型记忆没有被这一次选择顺手改掉
    expect(useAiStore.getState().models.codex).toBeUndefined()
  })

  it('一个可用的都没有时给恢复入口，不摆一个死掉的选择器', async () => {
    useAiStore.setState({ caps: capsOf(agentCaps({ usable: false, installed: false })) })
    await mount()
    expect(pairTrigger()).toBeNull()
    expect(range()).toBeNull()
    expect(buttons().some((b) => b.textContent?.includes('打开编码 Agent 设置'))).toBe(true)
  })

  /**
   * e2e 的三个锚点在这里钉住。
   *
   * `e2e/ux-consistency.spec.ts` 的流程 C 必须按机器上装没装 Agent 分支
   * （CI runner 上可能一个都没有），而**条件分支里的定位是假绿最好的藏身处**：
   * 审计 T37 把执行器从 radiogroup 换成 Select、把推理强度收进折叠区之后，
   * 那条用例按旧 role/文案找到 0 个元素，于是每一条断言都被静默跳过，
   * CI 一路绿（2026-09-07 复核才发现）。所以锚点的存在性由这里的正向用例负责，
   * e2e 那边的 `if` 才是安全的。
   */
  it('三个 e2e 锚点各自出现在它该出现的形态里', async () => {
    // 两个 Agent → 选择器形态
    useAiStore.setState({ caps: capsOf(codexSix, claudeCaps()) })
    await mount()
    expect(document.body.querySelector('[data-ai-agent-model="select"]')).toBeTruthy()
    expect(document.body.querySelector('[data-ai-agent-model="static"]')).toBeNull()
    // codex 支持推理强度 → 折叠入口在，默认收起
    const disclosure = document.body.querySelector('[data-ai-effort="disclosure"]')
    expect(disclosure).toBeTruthy()
    expect(disclosure!.getAttribute('aria-expanded')).toBe('false')

    // 一个 Agent 一个模型 → 静态文字形态
    await act(async () => {
      root?.unmount()
    })
    document.body.innerHTML = ''
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    expect(document.body.querySelector('[data-ai-agent-model="select"]')).toBeNull()
    expect(document.body.querySelector('[data-ai-agent-model="static"]')).toBeTruthy()

    // 一个可用的都没有 → 两种形态都不出现，只剩恢复入口
    await act(async () => {
      root?.unmount()
    })
    document.body.innerHTML = ''
    useAiStore.setState({ caps: capsOf(agentCaps({ usable: false, installed: false })) })
    await mount()
    expect(document.body.querySelector('[data-ai-agent-model]')).toBeNull()
    expect(document.body.querySelector('[data-ai-open-settings]')).toBeTruthy()
  })

  it('每个 Agent 各自保留模型与强度偏好', async () => {
    useAiStore.setState({ caps: capsOf(codexSix, claudeCaps()) })
    useAiStore.getState().setEffort('codex', 'low')
    useAiStore.getState().setModel('claude', 'opus')
    await mount()
    // 当前是 codex：展开强度后滑杆停在 low
    await openEffort()
    expect(range()!.value).toBe('1') // ['minimal','low',...] → index 1
    // 切到 claude：它没有强度，滑杆整块消失；模型保留 opus
    await pickPair('Claude Code · opus')
    expect(range()).toBeNull()
    expect(textOf()).toContain('opus')
    // 切回 codex：强度还是 low（展开状态是弹层自己的，切一圈回来它还开着）
    await pickPair('Codex · gpt-5.6-sol')
    expect(range()!.value).toBe('1')
  })

  it('记忆里的模型已不在清单里时照实显示它，不静默换成别的一项', async () => {
    // 控件上写着 A、任务却交给 B，是最难查的一类错
    useAiStore.setState({
      caps: capsOf(codexSix, claudeCaps()),
      agent: 'claude',
      models: { claude: '已下架的模型' },
    })
    await mount()
    const labels = (await pairOptions()).map((o) => o.textContent?.trim())
    expect(labels[0]).toBe('Claude Code · 已下架的模型')
  })
})

/* --------------------------------- 模型 ---------------------------------- */

describe('模型选择', () => {
  it('模型清单来自 caps', async () => {
    useAiStore.setState({ caps: capsOf(claudeCaps()) })
    await mount()
    expect(pairTrigger()).toBeTruthy()
    expect(textOf()).toContain('sonnet')
  })

  it('清单为空 = 跟随 CLI 默认，不伪造一个模型名', async () => {
    useAiStore.setState({
      caps: capsOf(agentCaps({ models: [], default_model: null })),
    })
    await mount()
    expect(pairTrigger()).toBeNull()
    // 静态那一行也不出现：没有模型名可写，编一个才是错的
    expect(textOf()).not.toContain('执行器与模型')
  })
})

/* ------------------------------- 推理强度 -------------------------------- */

describe('推理强度滑杆', () => {
  it('档位数 = caps 的真实数组长度', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    await openEffort()
    const r = range()!
    expect(r.min).toBe('0')
    expect(r.max).toBe('5') // 六档 → 0..5
  })

  it('滑到第 i 格写的就是 efforts[i]，不生成数组里没有的值', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    await openEffort()
    const list = codexSix.efforts
    for (const [i, expected] of list.entries()) {
      await act(async () => {
        setRange(range()!, String(i))
      })
      expect(useAiStore.getState().efforts.codex).toBe(expected)
      expect(list).toContain(useAiStore.getState().efforts.codex)
    }
  })

  it('收起时当前档位就写着；展开后 aria-valuetext 与它一致（审计 T37）', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    useAiStore.getState().setEffort('codex', 'high')
    await mount()
    // 按需展示的是**控件**，不是值：没展开也读得出现在是哪一档
    expect(range()).toBeNull()
    expect(textOf()).toContain('推理强度')
    expect(textOf()).toContain('高')
    await openEffort()
    expect(range()!.getAttribute('aria-valuetext')).toBe('高')
  })

  it('CLI 声明了表里没有的档位时回退原文，不显示空白', async () => {
    useAiStore.setState({
      caps: capsOf(agentCaps({ efforts: ['low', 'turbo'], default_effort: 'turbo' })),
    })
    await mount()
    await openEffort()
    expect(range()!.getAttribute('aria-valuetext')).toBe('turbo')
  })

  it('只有一档时滑杆不可调', async () => {
    useAiStore.setState({
      caps: capsOf(agentCaps({ efforts: ['medium'], default_effort: 'medium' })),
    })
    await mount()
    await openEffort()
    expect(range()).toBeTruthy()
    expect(range()!.disabled).toBe(true)
  })

  it('没有强度能力时整块不出现', async () => {
    useAiStore.setState({ caps: capsOf(claudeCaps()) })
    await mount()
    expect(range()).toBeNull()
    expect(textOf()).not.toContain('推理强度')
  })

  it('记忆里的档位已不在清单里时回落到第一格，不越界', async () => {
    useAiStore.setState({ caps: capsOf(codexSix), efforts: { codex: '不存在的档位' } })
    await mount()
    await openEffort()
    expect(range()!.value).toBe('0')
  })

  it('是原生 range：方向键与触摸免费拿到，且有可达名', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    await openEffort()
    expect(range()!.tagName).toBe('INPUT')
    expect(range()!.type).toBe('range')
    expect(range()!.getAttribute('aria-label')).toBe('推理强度')
  })
})

/* ------------------------------- 文案减负 -------------------------------- */

describe('正常状态的文案', () => {
  it('不常驻快照 / CLI 版本 / 路径说明', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    expect(textOf()).not.toContain('自动快照')
    expect(textOf()).not.toContain('codex-cli')
    expect(textOf()).not.toContain('/opt/homebrew')
    expect(textOf()).not.toContain('fig1_kinetics.py')
  })

  /**
   * 2026-09-11 组件工作台批次把弹层里的「技术详情」折叠整个去掉了：快照说明、
   * CLI 包名、解释器路径不再在这个弹层里出现（它们在设置 → 编码 Agent 的详情页）。
   * 守住的仍是第 6 条：正常状态一个字都不常驻。
   */
  it('正常状态不常驻快照 / CLI / 实现说明（弹层里也没有技术详情折叠）', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    expect(textOf()).not.toContain('自动快照')
    expect(textOf()).not.toContain('codex-cli')
    expect(textOf()).not.toContain('/opt/homebrew')
    expect(buttons().find((b) => b.textContent?.trim() === '技术详情')).toBeUndefined()
  })

  it('强度的原始值不出现在弹层里（正常状态给的是当前语言的名字）', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    useAiStore.getState().setEffort('codex', 'xhigh')
    await mount()
    expect(textOf()).toContain('极高')
    expect(textOf()).not.toContain('xhigh')
  })
})
