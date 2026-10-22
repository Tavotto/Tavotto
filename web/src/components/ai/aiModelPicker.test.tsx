/**
 * AI 执行器 / 模型与推理强度弹层。
 *
 * 要钉住的（修改前见
 * `docs/ux/img/ux-consistency-pass/before/zh-1440-ai-popover.png`：
 * 六档等宽按钮在 232px 弹层里两头都被切掉，正常状态还常驻一段快照说明）：
 *   1. 执行器与模型是**一个**紧凑选择器（审计 T37）；选不出第二项时不摆
 *      一个选不动的控件，但那一项写的是什么仍要看得见；
 *   2. 模型清单来自 caps，为空时不伪造模型；
 *   3. 推理强度不在这个弹层里了（2026-10-16：底栏胶囊 + 菜单，见 `effortMenu.test.tsx`），
 *      这里只钉「弹层里不再有它」；
 *   6. 正常状态不常驻快照 / CLI / 实现说明（2026-09-11 起弹层里也没有技术详情折叠，
 *      那些内容在设置 → 编码 Agent 的详情页）；
 *   7. 切 Agent 各自保留模型与强度偏好。
 *
 * 两级选择器（家族 → 型号）只是**呈现**：底下仍是 aiStore 的 agent 与 models[agent] 两个字段。
 * 「切到 B 再切回 A，A 的模型还是我上次选的」这条正是那个结构在被检验。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { AiCapabilities } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, claudeCaps } from '@/components/settings/testCaps'
import { effortsForModel, resolveEffort, useAiStore } from '@/store/aiStore'
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
/** 两级选择器的触发按钮；只有一项可选时它整个不存在 */
const pairTrigger = () =>
  host.querySelector('[data-ai-model-picker="trigger"]') as HTMLElement | null
const tick = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })
/** Radix 触发器在 pointerdown 上开菜单（不是 click） */
const openMenu = async () => {
  await act(async () => {
    pairTrigger()!.dispatchEvent(
      new PointerEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, pointerType: 'mouse' }),
    )
  })
  await tick()
}
const key = async (target: Element, k: string) => {
  await act(async () => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
  })
  await tick()
}
/** 第一级：执行器家族（menu 里的 menuitem，子菜单触发项） */
const families = () => [...document.body.querySelectorAll<HTMLElement>('[data-ai-family]')]
const family = (id: string) => families().find((f) => f.dataset.aiFamily === id)!
/** 第二级：型号（menuitemradio） */
const modelItems = () => [...document.body.querySelectorAll<HTMLElement>('[role="menuitemradio"]')]
const openFamily = async (id: string) => {
  family(id).focus()
  await key(family(id), 'ArrowRight')
}
const pickPair = async (agentId: string, model: string) => {
  await openMenu()
  await openFamily(agentId)
  const hit = modelItems().find((o) => o.dataset.aiModelItem === model)
  expect(hit, `${agentId} 下没有型号「${model}」`).toBeTruthy()
  await act(async () => {
    hit!.click()
  })
  await tick()
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

/* ------------------------------- 两级选择 -------------------------------- */

/** codex 的真实形状：config 当前模型 + CLI 目录里的型号，每个型号带自己的强度档位 */
const codexCatalog = agentCaps({
  models: ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-luna'],
  default_model: 'gpt-6-astra',
  efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
  default_effort: 'ultra',
  model_details: [
    { id: 'gpt-6-astra', display_name: 'GPT-6-Astra', description: 'Frontier intelligence', efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'], default_effort: 'medium' },
    { id: 'gpt-6.1-sol', display_name: 'GPT-6.1-Sol', description: 'Latest workhorse', efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'], default_effort: 'low' },
    { id: 'gpt-6-luna', display_name: 'GPT-6-Luna', description: 'Fast and affordable', efforts: ['low', 'medium', 'high', 'xhigh', 'max'], default_effort: 'medium' },
  ],
})
const claudeNamed = claudeCaps({
  model_details: [
    { id: 'sonnet', display_name: 'Sonnet', description: '', efforts: [], default_effort: null },
    { id: 'opus', display_name: 'Opus', description: '', efforts: [], default_effort: null },
  ],
})

describe('执行器与模型（两级选择器）', () => {
  it('只有一项可选时不摆选择器，但那一项写的是什么仍看得见', async () => {
    // 一个 Agent + 一个模型 = 一种组合：选择器换成一行静态文字
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    expect(pairTrigger()).toBeNull()
    expect(textOf()).toContain('Codex · gpt-5.6-sol')
  })

  it('第一级是执行器家族，各带自己的图标；型号在点开家族之后才出现', async () => {
    useAiStore.setState({ caps: capsOf(codexCatalog, claudeNamed) })
    await mount()
    expect(pairTrigger()).toBeTruthy()
    // 触发按钮：当前家族图标 + 「家族 · 型号显示名」
    expect(pairTrigger()!.textContent).toContain('Codex · GPT-6-Astra')
    expect(pairTrigger()!.querySelector('svg')).toBeTruthy()
    await openMenu()
    expect(document.body.querySelector('[role="menu"]')).toBeTruthy()
    expect(families().map((f) => f.dataset.aiFamily)).toEqual(['codex', 'claude'])
    expect(families().map((f) => f.textContent?.trim())).toEqual(['Codex', 'Claude Code'])
    // 每个家族一枚图标，且两家不是同一个形状
    const icons = families().map((f) => f.querySelector('svg')?.innerHTML ?? '')
    expect(icons.every((i) => i.length > 0)).toBe(true)
    expect(icons[0]).not.toBe(icons[1])
    // 第二级还没展开
    expect(modelItems()).toHaveLength(0)
    expect(family('codex').getAttribute('aria-haspopup')).toBe('menu')
  })

  it('第二级列该家族的型号：显示名为主、说明为次，当前型号 aria-checked，默认型号带小标', async () => {
    useAiStore.setState({ caps: capsOf(codexCatalog, claudeNamed) })
    await mount()
    await openMenu()
    await openFamily('codex')
    const rows = modelItems()
    expect(rows.map((r) => r.dataset.aiModelItem)).toEqual(['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-luna'])
    expect(rows[1].textContent).toContain('GPT-6.1-Sol')
    expect(rows[1].textContent).toContain('Latest workhorse')
    expect(rows.map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false'])
    expect(rows[0].textContent).toContain('默认')
  })

  it('选一项：执行器与模型两个字段各写各的，不是一个合并出来的新字段', async () => {
    useAiStore.setState({ caps: capsOf(codexCatalog, claudeNamed) })
    await mount()
    await pickPair('claude', 'opus')
    expect(useAiStore.getState().agent).toBe('claude')
    expect(useAiStore.getState().models.claude).toBe('opus')
    // codex 的模型记忆没有被这一次选择顺手改掉
    expect(useAiStore.getState().models.codex).toBeUndefined()
    expect(pairTrigger()!.textContent).toContain('Claude Code · Opus')
  })

  it('键盘：→ 进子菜单、← 收回一层（菜单仍开）、Esc 关菜单，不改值', async () => {
    useAiStore.setState({ caps: capsOf(codexCatalog, claudeNamed) })
    await mount()
    pairTrigger()!.focus()
    await key(pairTrigger()!, 'Enter')
    expect(document.body.querySelector('[role="menu"]')).toBeTruthy()
    // 键盘打开后焦点在第一项（Radix），↓ 移到第二个家族
    const first = family('codex')
    first.focus()
    await key(first, 'ArrowDown')
    expect(document.activeElement).toBe(family('claude'))
    await key(family('claude'), 'ArrowRight')
    expect(modelItems().map((r) => r.dataset.aiModelItem)).toEqual(['sonnet', 'opus'])
    // 焦点进了第二级的第一项；↓ 到 opus，← 退回家族层
    const sub = modelItems()[0]
    expect(document.activeElement).toBe(sub)
    await key(sub, 'ArrowLeft')
    expect(modelItems()).toHaveLength(0)
    expect(document.body.querySelector('[role="menu"]')).toBeTruthy()
    // 再进一次，Enter 选中第二级的项；值落到 store
    await key(family('claude'), 'ArrowRight')
    const items = modelItems()
    await key(items[0], 'ArrowDown')
    await key(items[1], 'Enter')
    expect(useAiStore.getState().models.claude).toBe('opus')
    expect(useAiStore.getState().agent).toBe('claude')
  })

  it('Esc 关掉整个菜单，值不变', async () => {
    useAiStore.setState({ caps: capsOf(codexCatalog, claudeNamed), agent: 'codex' })
    await mount()
    await openMenu()
    await openFamily('claude')
    await key(modelItems()[0], 'Escape')
    expect(useAiStore.getState().models.claude).toBeUndefined()
    expect(useAiStore.getState().agent).toBe('codex')
  })

  it('一个家族多个型号、另一个没有型号清单：后者是一级直选项（跟随 CLI 默认），不伪造型号', async () => {
    useAiStore.setState({
      caps: capsOf(codexCatalog, claudeCaps({ models: [], default_model: null })),
    })
    await mount()
    await openMenu()
    expect(family('codex').getAttribute('aria-haspopup')).toBe('menu')
    expect(family('claude').getAttribute('aria-haspopup')).toBeNull()
    await act(async () => family('claude').click())
    await tick()
    expect(useAiStore.getState().agent).toBe('claude')
    expect(useAiStore.getState().models.claude).toBeUndefined()
  })

  it('一个可用的都没有时给恢复入口，不摆一个死掉的选择器', async () => {
    useAiStore.setState({ caps: capsOf(agentCaps({ usable: false, installed: false })) })
    await mount()
    expect(pairTrigger()).toBeNull()
    expect(range()).toBeNull()
    expect(buttons().some((b) => b.textContent?.includes('打开改图助手设置'))).toBe(true)
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
    // 推理强度已搬到底栏胶囊：弹层里不再有折叠入口
    expect(document.body.querySelector('[data-ai-effort="disclosure"]')).toBeNull()

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
    // 切到 claude：模型保留 opus
    await pickPair('claude', 'opus')
    expect(textOf()).toContain('opus')
    // 切回 codex：强度记忆还是 low（强度的读写在底栏菜单，见 effortMenu.test.tsx）
    await pickPair('codex', 'gpt-5.6-sol')
    expect(useAiStore.getState().efforts.codex).toBe('low')
  })

  it('记忆里的模型已不在清单里时照实显示它，不静默换成别的一项', async () => {
    // 控件上写着 A、任务却交给 B，是最难查的一类错
    useAiStore.setState({
      caps: capsOf(codexSix, claudeCaps()),
      agent: 'claude',
      models: { claude: '已下架的模型' },
    })
    await mount()
    expect(pairTrigger()!.textContent).toContain('Claude Code · 已下架的模型')
    await openMenu()
    await openFamily('claude')
    expect(modelItems()[0].dataset.aiModelItem).toBe('已下架的模型')
  })

  it('旧后端没有 model_details：型号用 id 原文，照常两级可选', async () => {
    useAiStore.setState({ caps: capsOf(codexSix, claudeCaps()) })
    await mount()
    await openMenu()
    await openFamily('claude')
    expect(modelItems().map((r) => r.textContent?.trim())).toEqual(['sonnet默认', 'opus'])
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

describe('推理强度跟着型号走', () => {
  it('切到只支持较少档位的型号：档位清单变短，记着的档位不在其中就取该型号默认档', async () => {
    useAiStore.setState({ caps: capsOf(codexCatalog), agent: 'codex' })
    useAiStore.getState().setEffort('codex', 'ultra')
    expect(effortsForModel(codexCatalog, 'gpt-6-luna')).toEqual(['low', 'medium', 'high', 'xhigh', 'max'])
    expect(resolveEffort(codexCatalog, 'gpt-6-luna', 'ultra')).toBe('medium') // luna 没有 ultra
    expect(resolveEffort(codexCatalog, 'gpt-6-astra', 'ultra')).toBe('ultra') // astra 有，记忆保留
    expect(resolveEffort(codexCatalog, 'gpt-6.1-sol', undefined)).toBe('low') // 没记过 = 型号默认
  })

  it('型号没声明档位（旧后端 / 配置里的自定义型号）时回落到 Agent 级清单', async () => {
    const c = agentCaps({ efforts: ['low', 'high'], default_effort: 'high' })
    expect(effortsForModel(c, 'whatever')).toEqual(['low', 'high'])
    expect(resolveEffort(c, 'whatever', undefined)).toBe('high')
    expect(resolveEffort(c, 'whatever', 'zzz')).toBe('high')
  })
})

describe('推理强度不在这个弹层里', () => {
  it('弹层里没有滑杆、没有「推理强度」折叠行', async () => {
    useAiStore.setState({ caps: capsOf(codexSix) })
    await mount()
    expect(range()).toBeNull()
    expect(textOf()).not.toContain('推理强度')
  })
})
