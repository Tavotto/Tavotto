/**
 * 推理强度：输入框底栏的文字胶囊 + 带说明的菜单（2026-10-16，取代 StepSlider）。
 *
 * 要钉住的：
 *   1. 胶囊常驻底栏，写着当前档位；一档都没有（claude）时整颗不出现；
 *   2. 菜单一档一行（role=menuitemradio），名称 + 一句说明，当前档 aria-checked，默认档带「默认」小标；
 *   3. 选一档写的就是 `efforts[i]`，一步完成、菜单随即关闭；键盘 ↑↓ Enter 同样可达；
 *   4. 开集：CLI 声明了表里没有的档位 → 名称回退原值、不编说明；记忆里的档位不在清单里 → 回落到第一档；
 *   5. 后端会给的档位（minimal/low/medium/high/max/xhigh）中英都有名称与说明。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { AiCapabilities } from '@/lib/api'
import { i18n } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, claudeCaps } from '@/components/settings/testCaps'
import { useAiStore } from '@/store/aiStore'
import { EffortPill } from './AiPanel'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const KNOWN = ['minimal', 'low', 'medium', 'high', 'max', 'xhigh']
const codexSix = agentCaps({ efforts: KNOWN, default_effort: 'medium' })
const capsOf = (...agents: AiCapabilities['agents']): AiCapabilities =>
  ({ agents, endpoints: [] }) as unknown as AiCapabilities

let root: Root
let host: HTMLDivElement

async function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <EffortPill />
      </TooltipProvider>,
    )
  })
}

const pill = () => host.querySelector<HTMLButtonElement>('[data-ai-pill="effort"]')
const items = () => [...document.body.querySelectorAll<HTMLElement>('[role="menuitemradio"]')]
const menu = () => document.body.querySelector<HTMLElement>('[data-ai-effort="menu"]')
const tick = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

/** Radix 触发器在 pointerdown 上开菜单（不是 click） */
async function openByPointer() {
  await act(async () => {
    pill()!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, pointerType: 'mouse' }))
  })
  await tick()
}
const key = async (target: Element, k: string) => {
  await act(async () => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
  })
  await tick()
}

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  await i18n.changeLanguage('zh-CN')
  useAiStore.setState({ caps: null, agent: null, models: {}, efforts: {} })
})
afterEach(async () => {
  await act(async () => root?.unmount())
})

describe('胶囊', () => {
  it('常驻，写着当前档位；菜单没开时不渲染任何档位行', async () => {
    useAiStore.setState({ caps: capsOf(codexSix), agent: 'codex' })
    useAiStore.getState().setEffort('codex', 'high')
    await mount()
    expect(pill()!.textContent).toBe('推理 高')
    expect(pill()!.getAttribute('aria-haspopup')).toBe('menu')
    expect(items()).toHaveLength(0)
  })

  it('没有强度能力（claude）时整颗不出现', async () => {
    useAiStore.setState({ caps: capsOf(claudeCaps()), agent: 'claude' })
    await mount()
    expect(pill()).toBeNull()
  })

  it('跟随 CLI 默认：没存过偏好就显示默认档', async () => {
    useAiStore.setState({ caps: capsOf(codexSix), agent: 'codex' })
    await mount()
    expect(pill()!.textContent).toBe('推理 中')
  })
})

describe('菜单', () => {
  it('一档一行：名称 + 说明；当前档 aria-checked；默认档带小标；菜单角色正确', async () => {
    useAiStore.setState({ caps: capsOf(codexSix), agent: 'codex' })
    useAiStore.getState().setEffort('codex', 'high')
    await mount()
    await openByPointer()
    expect(document.body.querySelector('[role="menu"]')).toBeTruthy()
    const rows = items()
    expect(rows.map((r) => r.dataset.aiEffortItem)).toEqual(KNOWN)
    expect(rows.map((r) => r.getAttribute('aria-checked'))).toEqual(
      KNOWN.map((k) => String(k === 'high')),
    )
    for (const r of rows) expect(r.textContent).toContain(i18n.t(`ai:effortDesc.${r.dataset.aiEffortItem}`))
    const tagged = rows.filter((r) => r.textContent?.includes('默认'))
    expect(tagged.map((r) => r.dataset.aiEffortItem)).toEqual(['medium'])
  })

  it('点一档：写的就是 efforts[i]，一步完成，菜单关闭，胶囊随即换字', async () => {
    useAiStore.setState({ caps: capsOf(codexSix), agent: 'codex' })
    await mount()
    for (const e of KNOWN) {
      await openByPointer()
      const row = items().find((r) => r.dataset.aiEffortItem === e)!
      await act(async () => row.click())
      await tick()
      expect(useAiStore.getState().efforts.codex).toBe(e)
      expect(KNOWN).toContain(useAiStore.getState().efforts.codex)
      expect(items()).toHaveLength(0)
    }
    expect(pill()!.textContent).toBe('推理 极高')
  })

  it('键盘：Enter 打开、↓ 移动、Enter 选中；Esc 关闭且不改值', async () => {
    useAiStore.setState({ caps: capsOf(codexSix), agent: 'codex' })
    await mount()
    pill()!.focus()
    await key(pill()!, 'Enter')
    expect(items().length).toBe(KNOWN.length)
    const content = document.body.querySelector<HTMLElement>('[role="menu"]')!
    // Radix 键盘打开时焦点落在第一项
    expect(document.activeElement).toBe(items()[0])
    await key(document.activeElement!, 'ArrowDown')
    expect(document.activeElement).toBe(items()[1])
    await key(document.activeElement!, 'End')
    expect(document.activeElement).toBe(items()[KNOWN.length - 1])
    await key(document.activeElement!, 'Home')
    expect(document.activeElement).toBe(items()[0])
    await key(document.activeElement!, 'ArrowDown')
    await key(document.activeElement!, 'Enter')
    expect(useAiStore.getState().efforts.codex).toBe('low')
    expect(content.isConnected && items().length).toBeFalsy()

    await key(pill()!, 'Enter')
    await key(document.activeElement!, 'Escape')
    expect(items()).toHaveLength(0)
    expect(useAiStore.getState().efforts.codex).toBe('low')
  })
})

describe('开集与回退', () => {
  it('CLI 声明了表里没有的档位：名称回退原值，不编说明', async () => {
    useAiStore.setState({
      caps: capsOf(agentCaps({ efforts: ['low', 'turbo'], default_effort: 'turbo' })),
      agent: 'codex',
    })
    await mount()
    expect(pill()!.textContent).toBe('推理 turbo')
    await openByPointer()
    const turbo = items().find((r) => r.dataset.aiEffortItem === 'turbo')!
    expect(turbo.textContent).toContain('turbo')
    expect(turbo.textContent).toContain('默认')
    expect(turbo.textContent).not.toContain('effortDesc')
    // 只有名称 + 小标，没有第二行说明
    expect(turbo.querySelectorAll('span.text-ink-3').length).toBe(1)
  })

  it('记忆里的档位已不在清单里：回落到默认档（2026-10-22 起；原先是第一档），菜单里选中的是它', async () => {
    useAiStore.setState({ caps: capsOf(codexSix), agent: 'codex', efforts: { codex: '不存在的档位' } })
    await mount()
    expect(pill()!.textContent).toBe('推理 中')
    await openByPointer()
    const checked = items().filter((r) => r.getAttribute('aria-checked') === 'true')
    expect(checked.map((r) => r.dataset.aiEffortItem)).toEqual(['medium'])
  })

  it('档位跟着型号走：菜单只列当前型号支持的档位，「默认」小标挂在该型号自己的默认档上', async () => {
    const perModel = agentCaps({
      models: ['big', 'small'],
      default_model: 'big',
      efforts: ['low', 'medium', 'high', 'ultra'],
      default_effort: 'medium',
      model_details: [
        { id: 'big', display_name: 'Big', description: '', efforts: ['low', 'medium', 'high', 'ultra'], default_effort: 'high' },
        { id: 'small', display_name: 'Small', description: '', efforts: ['low', 'medium'], default_effort: 'low' },
      ],
    })
    useAiStore.setState({ caps: capsOf(perModel), agent: 'codex', models: { codex: 'small' }, efforts: { codex: 'ultra' } })
    await mount()
    // 记着 ultra，但 small 不支持 -> small 自己的默认档 low
    expect(pill()!.textContent).toBe('推理 低')
    await openByPointer()
    expect(items().map((r) => r.dataset.aiEffortItem)).toEqual(['low', 'medium'])
    expect(items().filter((r) => r.textContent?.includes('默认')).map((r) => r.dataset.aiEffortItem)).toEqual(['low'])
  })

  it('目录明确说该型号没有档位：不显示强度胶囊（不借兄弟型号的并集）；未知型号仍回落 Agent 级', async () => {
    const mixed = agentCaps({
      models: ['tiered', 'plain', 'cfg-only'],
      default_model: 'plain',
      efforts: ['low', 'high'],
      default_effort: 'high',
      model_details: [
        { id: 'tiered', display_name: 'T', description: '', efforts: ['low', 'high'], efforts_known: true, default_effort: 'high' },
        { id: 'plain', display_name: 'P', description: '', efforts: [], efforts_known: true, default_effort: null },
        { id: 'cfg-only', display_name: 'C', description: '', efforts: [], default_effort: null },
      ],
    })
    useAiStore.setState({ caps: capsOf(mixed), agent: 'codex', models: { codex: 'plain' } })
    await mount()
    expect(pill()).toBeNull()
    await act(async () => {
      useAiStore.setState({ models: { codex: 'cfg-only' } })
    })
    expect(pill()).not.toBeNull()
  })

  it('只有一档：菜单一行，已选中', async () => {
    useAiStore.setState({
      caps: capsOf(agentCaps({ efforts: ['medium'], default_effort: 'medium' })),
      agent: 'codex',
    })
    await mount()
    await openByPointer()
    expect(items()).toHaveLength(1)
    expect(items()[0].getAttribute('aria-checked')).toBe('true')
  })
})

describe('双语', () => {
  it.each(['zh-CN', 'en-US'])('%s：每个后端会给的档位都有名称与说明（名称不回退成原值）', async (lng) => {
    await i18n.changeLanguage(lng)
    for (const e of KNOWN) {
      const name = i18n.t(`ai:effortLabel.${e}`)
      const desc = i18n.t(`ai:effortDesc.${e}`, { defaultValue: '' })
      expect(name, `${lng} ${e} 名称`).not.toBe(e)
      expect(desc, `${lng} ${e} 说明`).toBeTruthy()
    }
    expect(i18n.t('ai:panel.effortDefault')).toBeTruthy()
  })

  it('英文界面：胶囊与菜单行', async () => {
    await i18n.changeLanguage('en-US')
    useAiStore.setState({ caps: capsOf(codexSix), agent: 'codex' })
    await mount()
    expect(pill()!.textContent).toBe('Reasoning Medium')
    await openByPointer()
    expect(menu()).toBeTruthy()
    expect(items()[2].textContent).toContain('Balances speed and quality')
    expect(items()[2].textContent).toContain('Default')
  })
})
