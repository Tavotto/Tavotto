/**
 * 「连接 AI」视图下的改图助手：模型选择器一级 = 连接（彩色 logo + 厂商名）、二级 = 型号，
 * 底部「连接更多 AI…」；没有连接时的空态按钮开的是连接小弹窗；老后端（没有 connections）退回旧选择器。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf, claudeCaps, connectCapsOf, connection } from '@/components/settings/testCaps'
import { useAiStore } from '@/store/aiStore'
import { useConnectAiStore } from '@/store/connectAiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel, ScopeAgentContent } from './AiPanel'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const ai = (k: string) => t(k, { ns: 'ai' })

const panelOf = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 75,
    fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 75,
    script: '/tmp/figs/fig1.py', overrides: [],
  }) as unknown as PanelObject

const DS = connection('p_ds', {
  vendor: 'deepseek', label: 'DeepSeek', logo: 'deepseek',
  models: ['ds-chat', 'ds-reason'], default_model: 'ds-chat',
  model_details: [{ id: 'ds-chat', display_name: 'DeepSeek Chat', description: '', efforts: [], default_effort: null }],
})
const CG = connection('sub:codex', {
  vendor: 'chatgpt', label: 'ChatGPT', logo: 'chatgpt', route: 'cli:codex',
  models: ['gpt-x'], default_model: 'gpt-x',
})

let root: Root
let host: HTMLDivElement
async function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
}
const scope = () => (
  <ScopeAgentContent panel={panelOf()} element={null} axes={null} scope="figure" scopes={['figure']} />
)
const tick = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })
const trigger = () => host.querySelector<HTMLElement>('[data-ai-model-picker="trigger"]')
const openMenu = async () => {
  await act(async () => {
    trigger()!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, pointerType: 'mouse' }))
  })
  await tick()
}
const key = async (el: Element, k: string) => {
  await act(async () => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
  })
  await tick()
}
const connItems = () => [...document.body.querySelectorAll<HTMLElement>('[data-ai-connection]')]

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_conn')
  useAiStore.setState({ caps: null, agent: null, connection: null, models: {}, efforts: {} })
  useConnectAiStore.setState({ open: false, reason: 'general', onConnected: null })
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
})
afterEach(async () => {
  await act(async () => root?.unmount())
})

describe('模型选择器：一级连接、二级型号', () => {
  it('触发器写「连接名 · 型号显示名」并带彩色 logo；一级是各个已连接的 AI（彩色 logo + 厂商名）', async () => {
    useAiStore.setState({ caps: connectCapsOf([DS, CG]), connection: 'p_ds' })
    await mount(scope())
    expect(trigger()!.textContent).toContain('DeepSeek · DeepSeek Chat')
    expect(trigger()!.querySelector('[data-brand-logo="deepseek"]')).toBeTruthy()
    await openMenu()
    expect(connItems().map((i) => i.dataset.aiConnection)).toEqual(['p_ds', 'sub:codex'])
    expect(connItems().map((i) => i.textContent)).toEqual(['DeepSeek', 'ChatGPT'])
    expect(connItems().every((i) => i.querySelector('[data-brand-logo]'))).toBe(true)
  })

  it('点开一个连接看到它的型号；选中写 connection 与 models[连接 id]，切回另一个连接时各自的记忆还在', async () => {
    useAiStore.setState({ caps: connectCapsOf([DS, CG]), connection: 'sub:codex' })
    await mount(scope())
    await openMenu()
    const ds = connItems().find((i) => i.dataset.aiConnection === 'p_ds')!
    ds.focus()
    await key(ds, 'ArrowRight')
    const item = [...document.body.querySelectorAll<HTMLElement>('[role="menuitemradio"]')].find((o) => o.dataset.aiModelItem === 'ds-reason')!
    expect(item).toBeTruthy()
    await act(async () => item.click())
    await tick()
    expect(useAiStore.getState().connection).toBe('p_ds')
    expect(useAiStore.getState().models['p_ds']).toBe('ds-reason')
    // 持久化：刷新后还是上次的选择
    expect(localStorage.getItem('tavotto.ai.connection')).toBe('p_ds')
    expect(JSON.parse(localStorage.getItem('tavotto.ai.prefs')!).models.p_ds).toBe('ds-reason')
  })

  it('底部「连接更多 AI…」打开连接小弹窗（不是设置页）', async () => {
    useAiStore.setState({ caps: connectCapsOf([DS, CG]), connection: 'p_ds' })
    await mount(scope())
    await openMenu()
    const more = document.body.querySelector<HTMLElement>('[data-ai-connect-more]')!
    expect(more.textContent).toContain(ai('panel.connectMore'))
    await act(async () => more.click())
    expect(useConnectAiStore.getState().open).toBe(true)
    expect(useUiStore.getState().settingsOpen).toBe(false)
  })

  it('只有一个连接且只有一个型号（或没有型号清单）：选择器仍在，里面仍有「连接更多 AI…」', async () => {
    for (const only of [CG, { ...CG, models: [], default_model: null }]) {
      useAiStore.setState({ caps: connectCapsOf([only]), connection: 'sub:codex' })
      await mount(scope())
      expect(trigger()).toBeTruthy()
      await openMenu()
      expect(document.body.querySelector('[data-ai-connect-more]')).toBeTruthy()
      await act(async () => root.unmount())
      document.body.innerHTML = ''
    }
  })

  it('记着的连接不再 ready：显示第一个 ready 的，不静默改用户的首选值', async () => {
    useAiStore.setState({ caps: connectCapsOf([DS, { ...CG, state: 'needs_auth' }]), connection: 'sub:codex' })
    await mount(scope())
    expect(trigger()!.textContent).toContain('DeepSeek')
    expect(useAiStore.getState().connection).toBe('sub:codex')
  })

  it('降级：没有 connections 字段时仍是旧的执行器 → 型号选择器，不崩', async () => {
    useAiStore.setState({ caps: capsOf([agentCaps({ models: ['a', 'b'], default_model: 'a' }), claudeCaps()]), agent: 'codex' })
    await mount(scope())
    expect(trigger()).toBeTruthy()
    await openMenu()
    expect(document.body.querySelector('[data-ai-model-menu="families"]')).toBeTruthy()
    expect(document.body.querySelector('[data-ai-model-menu="connections"]')).toBeNull()
    expect(document.body.querySelector('[data-ai-connect-more]')).toBeNull()
  })
})

describe('没有任何可用 AI 的空态', () => {
  it('弹层里的恢复入口写「连接 AI」，点它打开小弹窗而不是设置页', async () => {
    useAiStore.setState({ caps: connectCapsOf([]) })
    await mount(scope())
    const btn = host.querySelector<HTMLButtonElement>('[data-ai-open-settings]')!
    expect(btn.textContent).toBe(ai('panel.connectAi'))
    expect(host.textContent).toContain(ai('panel.noAi'))
    await act(async () => btn.click())
    expect(useConnectAiStore.getState().open).toBe(true)
    expect(useUiStore.getState().settingsOpen).toBe(false)
  })

  it('助手面板顶上的提示条同样打开小弹窗', async () => {
    useAiStore.setState({ caps: connectCapsOf([]) })
    useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panelOf()] } }) as never)
    await mount(<AssistantPanel />)
    const notice = host.querySelector('[data-ai-no-agent]')!
    expect(notice).toBeTruthy()
    const btn = notice.querySelector('button')!
    expect(btn.textContent).toBe(ai('panel.connectAi'))
    await act(async () => btn.click())
    expect(useConnectAiStore.getState().open).toBe(true)
    expect(useUiStore.getState().settingsOpen).toBe(false)
  })

  it('降级：老后端没有连接视图 → 仍是「打开改图助手设置」去设置页', async () => {
    useAiStore.setState({ caps: capsOf([agentCaps({ usable: false, installed: false })]) })
    await mount(scope())
    const btn = host.querySelector<HTMLButtonElement>('[data-ai-open-settings]')!
    expect(btn.textContent).toBe(ai('panel.openAiSettings'))
    await act(async () => btn.click())
    expect(useConnectAiStore.getState().open).toBe(false)
    expect(useUiStore.getState()).toMatchObject({ settingsOpen: true, settingsSection: 'ai' })
  })
})
