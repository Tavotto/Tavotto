/**
 * AI 模型与推理强度弹层。
 *
 * 要钉住的（修改前见
 * `docs/ux/img/ux-consistency-pass/before/zh-1440-ai-popover.png`：
 * 六档等宽按钮在 232px 弹层里两头都被切掉，正常状态还常驻一段快照说明）：
 *   1. 只装一个 Agent 时不出只有一项的「双选」；两个时才出；
 *   2. 模型清单来自 caps，为空时不伪造模型；
 *   3. 推理强度是**真实能力数组驱动**的离散滑杆，档位数 = 数组长度，
 *      滑到第 i 格写的就是 efforts[i]，绝不生成数组里没有的值；
 *   4. 只有一档时滑杆不可调；一档都没有时整块不出现；
 *   5. 键盘方向键可调；
 *   6. 正常状态不常驻快照 / CLI / 实现说明，它们在「技术详情」里；
 *   7. 切 Agent 各自保留模型与强度偏好。
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
const providerGroup = () =>
  host.querySelector('[role="radiogroup"][aria-label="执行改动的命令行工具"]')
const modelTrigger = () =>
  host.querySelector('[aria-label="模型"]') as HTMLElement | null
const openDetails = async () => {
  const btn = buttons().find((b) => b.textContent?.trim() === '技术详情')!
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

describe('Provider 切换', () => {
  it('只装一个 Agent 时不出双选控件', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    expect(providerGroup()).toBeNull()
  })

  it('两个都装了才出双选', async () => {
    useAiStore.setState({ caps: capsOf(codexSix, claudeCaps()) })
    await mount()
    expect(providerGroup()).toBeTruthy()
    expect(textOf()).toContain('Codex')
    expect(textOf()).toContain('Claude Code')
  })

  it('一个可用的都没有时给恢复入口，不摆一个死掉的选择器', async () => {
    useAiStore.setState({ caps: capsOf(agentCaps({ usable: false, installed: false })) })
    await mount()
    expect(providerGroup()).toBeNull()
    expect(range()).toBeNull()
    expect(buttons().some((b) => b.textContent?.includes('打开编码 Agent 设置'))).toBe(true)
  })

  it('每个 Agent 各自保留模型与强度偏好', async () => {
    useAiStore.setState({ caps: capsOf(codexSix, claudeCaps()) })
    useAiStore.getState().setEffort('codex', 'low')
    useAiStore.getState().setModel('claude', 'opus')
    await mount()
    // 当前是 codex：强度显示 low
    expect(range()!.value).toBe('1') // ['minimal','low',...] → index 1
    // 切到 claude：它没有强度，滑杆整块消失；模型保留 opus
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('Claude Code'))!.click()
    })
    expect(range()).toBeNull()
    expect(textOf()).toContain('opus')
    // 切回 codex：强度还是 low
    await act(async () => {
      buttons().find((b) => b.textContent?.includes('Codex'))!.click()
    })
    expect(range()!.value).toBe('1')
  })
})

/* --------------------------------- 模型 ---------------------------------- */

describe('模型选择', () => {
  it('模型清单来自 caps', async () => {
    useAiStore.setState({ caps: capsOf(claudeCaps()) })
    await mount()
    expect(modelTrigger()).toBeTruthy()
    expect(textOf()).toContain('sonnet')
  })

  it('清单为空 = 跟随 CLI 默认，不伪造一个模型名', async () => {
    useAiStore.setState({
      caps: capsOf(agentCaps({ models: [], default_model: null })),
    })
    await mount()
    expect(modelTrigger()).toBeNull()
  })
})

/* ------------------------------- 推理强度 -------------------------------- */

describe('推理强度滑杆', () => {
  it('档位数 = caps 的真实数组长度', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    const r = range()!
    expect(r.min).toBe('0')
    expect(r.max).toBe('5') // 六档 → 0..5
  })

  it('滑到第 i 格写的就是 efforts[i]，不生成数组里没有的值', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    const list = codexSix.efforts
    for (const [i, expected] of list.entries()) {
      await act(async () => {
        setRange(range()!, String(i))
      })
      expect(useAiStore.getState().efforts.codex).toBe(expected)
      expect(list).toContain(useAiStore.getState().efforts.codex)
    }
  })

  it('当前档位在滑杆上方以当前语言显示，aria-valuetext 同步', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    useAiStore.getState().setEffort('codex', 'high')
    await mount()
    expect(range()!.getAttribute('aria-valuetext')).toBe('高')
    expect(textOf()).toContain('高')
  })

  it('CLI 声明了表里没有的档位时回退原文，不显示空白', async () => {
    useAiStore.setState({
      caps: capsOf(agentCaps({ efforts: ['low', 'turbo'], default_effort: 'turbo' })),
    })
    await mount()
    expect(range()!.getAttribute('aria-valuetext')).toBe('turbo')
  })

  it('只有一档时滑杆不可调', async () => {
    useAiStore.setState({
      caps: capsOf(agentCaps({ efforts: ['medium'], default_effort: 'medium' })),
    })
    await mount()
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
    expect(range()!.value).toBe('0')
  })

  it('是原生 range：方向键与触摸免费拿到，且有可达名', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
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

  it('展开「技术详情」后它们都在，长路径截断但有 title', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    await openDetails()
    expect(textOf()).toContain('自动快照')
    expect(textOf()).toContain('codex-cli')
    expect(textOf()).toContain('fig1_kinetics.py')
    const pathNode = Array.from(host.querySelectorAll('p')).find((p) =>
      p.textContent?.includes('/opt/homebrew'),
    )
    expect(pathNode).toBeTruthy()
    expect(pathNode!.className).toContain('truncate')
    expect(pathNode!.getAttribute('title')).toBe('/opt/homebrew/bin/codex')
  })

  it('强度的原始值只在技术详情里出现（正常状态给的是当前语言的名字）', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    useAiStore.getState().setEffort('codex', 'xhigh')
    await mount()
    expect(textOf()).toContain('极高')
    expect(textOf()).not.toContain('xhigh')
    await openDetails()
    expect(textOf()).toContain('xhigh')
  })
})
