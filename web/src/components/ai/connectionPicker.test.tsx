/**
 * 「连接 AI」视图下的改图助手：模型选择器一级 = 连接（彩色 logo + 厂商名）、二级 = 型号，
 * 底部「连接更多 AI…」；没有连接时的空态按钮开的是连接小弹窗；老后端（没有 connections）退回旧选择器。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf, claudeCaps, connectCapsOf, connection, vendor } from '@/components/settings/testCaps'
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

describe('「AI 与模型」：已连接的 AI 一个列表，点一行在它下面展开型号（2026-11-25 改版，不再下拉里套菜单）', () => {
  const modelItems = () => [...host.querySelectorAll<HTMLElement>('[data-ai-model-item]')]
  const row = (id: string) => connItems().find((i) => i.dataset.aiConnection === id)!

  it('每个已连接的 AI 一行（彩色 logo + 名字，订阅类后面灰字「订阅」）；当前用的那个默认展开，选中的型号打勾', async () => {
    useAiStore.setState({ caps: connectCapsOf([DS, CG]), connection: 'p_ds' })
    await mount(scope())
    expect(trigger()).toBeNull() // 没有下拉触发器了
    expect(connItems().map((i) => i.dataset.aiConnection)).toEqual(['p_ds', 'sub:codex'])
    expect(row('p_ds').textContent).toBe('DeepSeek')
    expect(row('sub:codex').textContent).toBe(`ChatGPT${ai('panel.kindSubscription')}`)
    expect(connItems().every((i) => i.querySelector('[data-brand-logo]'))).toBe(true)
    expect(row('p_ds').getAttribute('aria-expanded')).toBe('true')
    expect(modelItems().map((i) => i.dataset.aiModelItem)).toEqual(['ds-chat', 'ds-reason'])
    expect(modelItems()[0].textContent).toBe(`DeepSeek Chat${ai('panel.effortDefault')}`) // 默认只是灰字，不是底色小标
  })

  it('点开另一个连接看到它的型号；选中写 connection 与 models[连接 id]，并持久化', async () => {
    useAiStore.setState({ caps: connectCapsOf([DS, CG]), connection: 'sub:codex' })
    await mount(scope())
    await act(async () => row('p_ds').click())
    const item = modelItems().find((o) => o.dataset.aiModelItem === 'ds-reason')!
    const radioOf = (el: HTMLElement) => el.querySelector<HTMLInputElement>('input[data-ai-model-radio]')!
    await act(async () => item.click())
    await tick()
    expect(useAiStore.getState().connection).toBe('p_ds')
    expect(useAiStore.getState().models['p_ds']).toBe('ds-reason')
    expect(radioOf(item).checked).toBe(true)
    expect(localStorage.getItem('tavotto.ai.connection')).toBe('p_ds')
    expect(JSON.parse(localStorage.getItem('tavotto.ai.prefs')!).models.p_ds).toBe('ds-reason')
  })

  it('同一连接的型号是同一 name 的原生 radio，只有选中的那一个 checked', async () => {
    useAiStore.setState({ caps: connectCapsOf([DS, CG]), connection: 'p_ds', models: { p_ds: 'ds-reason' } })
    await mount(scope())
    const radios = modelItems().map((i) => i.querySelector<HTMLInputElement>('input[type="radio"]')!)
    expect(radios).toHaveLength(2)
    expect(new Set(radios.map((r) => r.name))).toEqual(new Set(['model-p_ds']))
    expect(radios.map((r) => r.checked)).toEqual([false, true])
  })

  it('末行「连接更多 AI…」打开连接小弹窗（不是设置页）', async () => {
    useAiStore.setState({ caps: connectCapsOf([DS, CG]), connection: 'p_ds' })
    await mount(scope())
    const more = host.querySelector<HTMLElement>('[data-ai-connect-more]')!
    expect(more.textContent).toContain(ai('panel.connectMore'))
    await act(async () => more.click())
    expect(useConnectAiStore.getState().open).toBe(true)
    expect(useUiStore.getState().settingsOpen).toBe(false)
  })

  it('只有一个连接（有或没有型号清单）：列表仍在，仍有「连接更多 AI…」；没有型号清单的一行点了就是切换、不展开', async () => {
    for (const only of [CG, { ...CG, models: [], default_model: null }]) {
      useAiStore.setState({ caps: connectCapsOf([only]), connection: 'sub:codex' })
      await mount(scope())
      expect(row('sub:codex')).toBeTruthy()
      expect(host.querySelector('[data-ai-connect-more]')).toBeTruthy()
      if (only.models.length === 0) expect(row('sub:codex').getAttribute('aria-expanded')).toBeNull()
      await act(async () => root.unmount())
      document.body.innerHTML = ''
    }
  })

  it('记着的连接不再 ready：列表里只有 ready 的、展开的是第一个 ready 的，不静默改用户的首选值', async () => {
    useAiStore.setState({ caps: connectCapsOf([DS, { ...CG, state: 'needs_auth' }]), connection: 'sub:codex' })
    await mount(scope())
    expect(connItems().map((i) => i.dataset.aiConnection)).toEqual(['p_ds'])
    expect(row('p_ds').getAttribute('aria-expanded')).toBe('true')
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

  // 老用户升级（ADR 0209 §六，维护者 2026-11-26 选 a）：装着 Codex、ChatGPT 登录默认开、还没登录 → 同一句话，按钮直接是 Continue with ChatGPT
  const siwcOpenai = () =>
    vendor('openai', {
      label: 'ChatGPT',
      region: 'global',
      connect: 'siwc',
      methods: [{ id: 'openai:siwc', kind: 'siwc', label_key: 'method.chatgptPlan', connection_id: 'siwc:chatgpt', available: true }],
    })

  it('老用户升级：装着 Codex、还没用 ChatGPT 登录 → 提示条同一句话，按钮直接是 Continue with ChatGPT，点了发起 ChatGPT 登录', async () => {
    const posts: string[] = []
    const prev = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if ((init?.method ?? 'GET').toUpperCase() === 'POST') posts.push(String(url))
      return new Response('{}', { status: 200 })
    }) as typeof fetch
    try {
      useAiStore.setState({ caps: connectCapsOf([], { agents: [agentCaps({ installed: true })], vendors: [siwcOpenai()] }) })
      useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panelOf()] } }) as never)
      await mount(<AssistantPanel />)
      const notice = host.querySelector('[data-ai-siwc-upgrade]')!
      expect(notice).toBeTruthy()
      expect(notice.textContent).toContain(ai('panel.noAi'))
      const btn = notice.querySelector<HTMLButtonElement>('[data-connect-action="siwc"]')!
      expect(btn.textContent).toBe('Continue with ChatGPT')
      expect([...notice.querySelectorAll('button')].map((b) => b.textContent)).not.toContain(ai('panel.connectAi'))
      await act(async () => btn.click())
      expect(posts.some((u) => u.startsWith('/api/ai/siwc/login'))).toBe(true)
      expect(useConnectAiStore.getState().open).toBe(false)
    } finally {
      globalThis.fetch = prev
    }
  })

  it('没装 Codex（新用户）：提示条照旧是「连接 AI」', async () => {
    useAiStore.setState({ caps: connectCapsOf([], { agents: [agentCaps({ installed: false })], vendors: [siwcOpenai()] }) })
    useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panelOf()] } }) as never)
    await mount(<AssistantPanel />)
    expect(host.querySelector('[data-ai-siwc-upgrade]')).toBeNull()
    expect(host.querySelector('[data-ai-no-agent] button')!.textContent).toBe(ai('panel.connectAi'))
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
