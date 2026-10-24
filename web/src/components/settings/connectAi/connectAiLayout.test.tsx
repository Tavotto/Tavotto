/**
 * 连接弹窗的版式（宽矩形 + 左侧品牌画）、二级卡片（多种连接方式）、品牌画的动画开关。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { t } from '@/i18n'
import type { AiVendor } from '@/lib/api'
import { PRODUCT_NAME } from '@/lib/brand'
import { useAiStore } from '@/store/aiStore'
import { connection, connectCapsOf, vendor } from '../testCaps'
import { BrandLines } from './BrandLines'
import { ConnectAiDialog } from './ConnectAiDialog'
import { methodHint } from './vendors'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

const cx = (k: string, v?: Record<string, unknown>) => t(`settings.connectAi.${k}`, { ns: 'dialogs', ...(v ?? {}) })
const json = (b: unknown) => new Response(JSON.stringify(b), { headers: { 'Content-Type': 'application/json' } })

const OPENAI: AiVendor = vendor('openai', {
  label: 'OpenAI',
  region: 'global',
  connect: 'login',
  methods: [
    { id: 'sub', kind: 'login', label_key: 'method.subscription', agent: 'codex', connection_id: 'sub:codex', available: true },
    { id: 'api', kind: 'key', label_key: 'method.apiKey', catalog_id: 'openai', key_url: 'https://keys.example/openai', available: true },
  ],
})
const CLAUDE: AiVendor = vendor('anthropic', {
  label: 'Claude',
  region: 'global',
  connect: 'login',
  methods: [
    { id: 'sub', kind: 'login', label_key: 'method.subscription', agent: 'claude', available: false },
    { id: 'api', kind: 'key', label_key: 'method.apiKey', catalog_id: 'anthropic', available: true },
  ],
})
const DS: AiVendor = vendor('deepseek', { label: 'DeepSeek' })

let calls: { url: string; method: string; body: Record<string, unknown> | null }[]
/** 「后端」当前的 capabilities（登录用例里按需换成连好之后的版本） */
let serverCaps: ReturnType<typeof connectCapsOf> | null
let host: HTMLDivElement
let root: Root
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null as never)
  calls = []
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    calls.push({ url: String(url), method: (init?.method ?? 'GET').toUpperCase(), body: init?.body ? JSON.parse(String(init.body)) : null })
    if (String(url).startsWith('/api/ai/capabilities') && serverCaps) return json(serverCaps)
    if (String(url).endsWith('/agents/codex/login') && init?.method === 'POST') return json({ started: true, mode: 'terminal' })
    return json({})
  }) as typeof fetch
  serverCaps = null
  useAiStore.setState({ caps: { ...connectCapsOf(), vendors: [DS, OPENAI, CLAUDE] } })
})
afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
  useAiStore.setState({ caps: null })
  vi.restoreAllMocks()
})
async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
  await act(async () => {})
}
const q = <T extends Element = HTMLElement>(sel: string) => document.body.querySelector<T>(sel)
const click = async (el: Element | null) => {
  await act(async () => (el as HTMLElement).click())
  await act(async () => {})
}

describe('版式：宽矩形 + 左侧品牌画', () => {
  it('固定尺寸的横向弹窗；左栏占约 40%、窄屏（<700px）隐藏，右栏常在；左栏有标志与产品名（来自品牌常量）', async () => {
    await render(<ConnectAiDialog open onClose={() => {}} />)
    const content = q('[data-dialog="connect-ai"]')!
    expect(content.style.width).toBe('800px')
    expect(content.style.height).toBe('480px')
    const brand = q('[data-connect-brand]')!
    expect(brand.className).toContain('w-[40%]')
    expect(brand.classList.contains('hidden')).toBe(true)
    expect(brand.className).toContain('min-[700px]:block')
    expect(brand.textContent).toContain(PRODUCT_NAME)
    expect(brand.querySelector('canvas[data-brand-lines]')).toBeTruthy()
    // 右栏：标题 + 副标题 + 滚动的列表区 + 「稍后」
    expect(q('[data-connect-scroll]')!.className).toContain('overflow-y-auto')
    expect(q('[data-connect-later]')).toBeTruthy()
    expect(q('[data-connect-ai-dialog]')!.textContent).toContain(cx('subtitle'))
  })

  it('左栏背景用品牌 token（不写字面色）', async () => {
    await render(<ConnectAiDialog open onClose={() => {}} />)
    expect(q('[data-connect-brand]')!.getAttribute('style')).toContain('var(--color-paper-ink)')
  })
})

describe('二级卡片的说明写产品名', () => {
  const hint = (v: AiVendor, i: number) => methodHint(v, v.methods![i])
  it('订阅看登录家族、Key 看 label_key、账号登录写厂商名；不按厂商 id', () => {
    expect(hint(OPENAI, 0)).toBe('用 ChatGPT 订阅登录')
    expect(hint(CLAUDE, 0)).toBe('用 Claude 订阅登录')
    expect(hint(OPENAI, 1)).toBe('填写 OpenAI API Key')
    const kimi = vendor('moonshot', { label: 'Kimi', methods: [
      { id: 'a', kind: 'key', label_key: 'method.apiKey', available: true },
      { id: 'c', kind: 'key', label_key: 'method.kimiCode', available: true },
    ] })
    expect(hint(kimi, 1)).toBe('用 Kimi Code 会员的 Key')
    const bailian = vendor('dashscope', { methods: [{ id: 'p', kind: 'key', label_key: 'method.codingPlan', available: true }] })
    expect(hint(bailian, 0)).toBe('用 Coding Plan 的 Key')
    const mm = vendor('minimax', { label: 'MiniMax', methods: [{ id: 'o', kind: 'oauth', label_key: 'method.account', available: true }] })
    expect(hint(mm, 0)).toBe('用 MiniMax 账号登录')
    // 同样是订阅登录、换一个厂商 id，只要家族是 codex 仍写 ChatGPT
    expect(hint(vendor('whatever', { methods: [{ id: 's', kind: 'login', agent: 'codex', available: true }] }), 0)).toBe('用 ChatGPT 订阅登录')
  })
})

describe('二级卡片：一家厂商有多种连接方式', () => {
  it('多种方式：点「连接」进二级页（大 logo + 厂商名 + 返回），每种方式一张卡；返回回到列表', async () => {
    await render(<ConnectAiDialog open onClose={() => {}} />)
    expect(q('[data-connect-vendor="openai"] [data-connect-action="methods"]')).toBeTruthy()
    await click(q('[data-connect-vendor="openai"] [data-connect-action="methods"]'))
    const step = q('[data-connect-step="openai"]')!
    expect(step.querySelector('[data-brand-logo]')).toBeTruthy()
    expect(step.textContent).toContain('OpenAI')
    const cards = [...document.body.querySelectorAll<HTMLButtonElement>('[data-connect-method]')]
    expect(cards.map((c) => c.dataset.connectMethod)).toEqual(['sub', 'api'])
    expect(cards[0].textContent).toContain(cx('method.subscription'))
    expect(cards[0].textContent).toContain('用 ChatGPT 订阅登录')
    expect(cards[1].textContent).toContain(cx('method.apiKey'))
    expect(cards[1].textContent).toContain('填写 OpenAI API Key')
    expect(q('[data-connect-vendor]')).toBeNull()
    await click(q('[data-connect-back]'))
    expect(q('[data-connect-step]')).toBeNull()
    expect(q('[data-connect-vendor="deepseek"]')).toBeTruthy()
  })

  it('选 API Key：展开 Key 表单，提交用该方式的目录 id；选订阅：直接发起登录（家族来自 method.agent）', async () => {
    await render(<ConnectAiDialog open onClose={() => {}} />)
    await click(q('[data-connect-vendor="openai"] [data-connect-action="methods"]'))
    await click(q('[data-connect-method="api"]'))
    const input = q<HTMLInputElement>('[data-connect-key-input]')!
    expect(q<HTMLAnchorElement>('[data-connect-key-link]')!.href).toBe('https://keys.example/openai')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'sk-x')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(q('[data-connect-key-submit]'))
    expect(calls.find((c) => c.url === '/api/ai/connections' && c.method === 'POST')?.body).toEqual({ vendor: 'openai', api_key: 'sk-x' })
    await click(q('[data-connect-method="api"]'))
    await click(q('[data-connect-method="sub"]'))
    expect(calls.some((c) => c.url.endsWith('/api/ai/agents/codex/login') && c.method === 'POST')).toBe(true)
  })

  it('不可用的方式置灰不可点，并给一句原因', async () => {
    await render(<ConnectAiDialog open onClose={() => {}} />)
    await click(q('[data-connect-vendor="anthropic"] [data-connect-action="methods"]'))
    const sub = q<HTMLButtonElement>('[data-connect-method="sub"]')!
    expect(sub.disabled).toBe(true)
    expect(sub.textContent).toContain(cx('needsInstall'))
    expect(q<HTMLButtonElement>('[data-connect-method="api"]')!.disabled).toBe(false)
  })

  it('只有一种方式的厂商不进二级页：点「连接」直接展开 Key 表单', async () => {
    await render(<ConnectAiDialog open onClose={() => {}} />)
    expect(q('[data-connect-vendor="deepseek"] [data-connect-action="methods"]')).toBeNull()
    await click(q('[data-connect-vendor="deepseek"] [data-connect-action="key"]'))
    expect(q('[data-connect-step]')).toBeNull()
    expect(q('[data-connect-key-form="deepseek"]')).toBeTruthy()
  })

  it('降级：没有 methods 字段时按旧的 connect 当唯一方式', async () => {
    useAiStore.setState({ caps: { ...connectCapsOf(), vendors: [{ ...OPENAI, methods: undefined }] } })
    await render(<ConnectAiDialog open onClose={() => {}} />)
    expect(q('[data-connect-vendor="openai"] [data-connect-action="methods"]')).toBeNull()
    expect(q('[data-connect-vendor="openai"] [data-connect-action="login"]')).toBeTruthy()
  })
})

/* ------------------------- 品牌画：动画开关 ------------------------- */

function stubCanvas() {
  const ctx = new Proxy(
    {
      getImageData: () => ({ data: new Uint8ClampedArray(96 * 96 * 4).fill(255) }),
      createLinearGradient: () => ({ addColorStop() {} }),
      measureText: () => ({ width: 10 }),
      clearRect: vi.fn(),
    } as Record<string, unknown>,
    { get: (t, k) => (k in t ? t[k as string] : () => undefined), set: () => true },
  )
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as never)
  vi.stubGlobal('Path2D', class {
    moveTo() {}
    lineTo() {}
  })
  return ctx as { clearRect: ReturnType<typeof vi.fn> }
}
const media = (reduced: boolean) =>
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: reduced && q.includes('reduce'), addEventListener() {}, removeEventListener() {} }))

describe('BrandLines：rAF 循环', () => {
  it('正常：起 rAF 循环，每帧重画；卸载时取消 rAF、移除监听', async () => {
    const ctx = stubCanvas()
    media(false)
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 7)
    const caf = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})
    const remove = vi.spyOn(HTMLCanvasElement.prototype, 'removeEventListener')
    await render(<BrandLines />)
    expect(raf).toHaveBeenCalled()
    await act(async () => root.unmount())
    expect(caf).toHaveBeenCalledWith(7)
    expect(remove.mock.calls.map((c) => c[0])).toEqual(expect.arrayContaining(['pointermove', 'pointerleave']))
    expect(ctx.clearRect).toBeDefined()
  })

  it('prefers-reduced-motion：只画静态一帧，不起 rAF、不挂鼠标监听', async () => {
    const ctx = stubCanvas()
    media(true)
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1)
    const add = vi.spyOn(HTMLCanvasElement.prototype, 'addEventListener')
    await render(<BrandLines />)
    expect(raf).not.toHaveBeenCalled()
    expect(add.mock.calls.map((c) => c[0])).not.toContain('pointermove')
    expect(ctx.clearRect.mock.calls.length).toBeGreaterThanOrEqual(1)
  })

  it('标签页不可见时暂停：取消 rAF；回来再起', async () => {
    stubCanvas()
    media(false)
    let id = 0
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => ++id)
    const caf = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})
    await render(<BrandLines />)
    const started = raf.mock.calls.length
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(caf).toHaveBeenCalled()
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(raf.mock.calls.length).toBeGreaterThan(started)
  })
})

/* ----------------- P2：连接状态落在每种方式上 ----------------- */

describe('多方式厂商：一种连好了不堵另一种的入口', () => {
  const SUB = connection('sub:codex', { vendor: 'openai', label: 'ChatGPT', route: 'cli:codex' })
  const KEY = connection('p_openai', { vendor: 'openai', label: 'OpenAI API', method: 'api', models: ['m1'], default_model: 'm1' })
  const withConns = (...c: ReturnType<typeof connection>[]) =>
    useAiStore.setState({ caps: { ...connectCapsOf(c), vendors: [DS, OPENAI, CLAUDE] } })

  it('已用订阅连接：行上显示「已连接」，入口仍在；二级页里订阅卡已连接、API Key 卡仍可连', async () => {
    withConns(SUB)
    await render(<ConnectAiDialog open onClose={() => {}} />)
    const row = q('[data-connect-vendor="openai"]')!
    expect(row.querySelector('[data-connect-state="ready"]')?.textContent).toBe(cx('connected'))
    await click(row.querySelector('[data-connect-action="methods"]'))
    expect(q('[data-connect-method="sub"]')!.dataset.connectMethodState).toBe('ready')
    expect(q('[data-connect-method="sub"] [data-connect-state="ready"]')).toBeTruthy()
    const api = q<HTMLButtonElement>('[data-connect-method="api"]')!
    expect(api.dataset.connectMethodState).toBe('none')
    expect(api.disabled).toBe(false)
    await click(api)
    expect(q('[data-connect-key-form]')).toBeTruthy()
  })

  it('反过来：已连 API Key，订阅卡照常可登录；已连的 Key 卡展开是管理（换 Key / 断开）', async () => {
    withConns(KEY)
    await render(<ConnectAiDialog open onClose={() => {}} />)
    await click(q('[data-connect-vendor="openai"] [data-connect-action="methods"]'))
    expect(q('[data-connect-method="api"]')!.dataset.connectMethodState).toBe('ready')
    expect(q('[data-connect-method="sub"]')!.dataset.connectMethodState).toBe('none')
    await click(q('[data-connect-method="api"]'))
    expect(q('[data-connect-manage="p_openai"]')).toBeTruthy()
    expect(q('[data-connect-change-key]')).toBeTruthy()
    expect(q('[data-connect-disconnect]')).toBeTruthy()
    await click(q('[data-connect-method="sub"]'))
    expect(calls.some((c) => c.url.endsWith('/api/ai/agents/codex/login') && c.method === 'POST')).toBe(true)
  })

  it('已连的订阅卡只显示状态，不展开', async () => {
    withConns(SUB)
    await render(<ConnectAiDialog open onClose={() => {}} />)
    await click(q('[data-connect-vendor="openai"] [data-connect-action="methods"]'))
    expect(q<HTMLButtonElement>('[data-connect-method="sub"]')!.disabled).toBe(true)
  })

  it('同一厂商两种 Key 方式：连接只算到它自己的方式上（method 字段 / catalog_id），不串', async () => {
    const kimi = vendor('moonshot', {
      label: 'Kimi',
      region: 'cn',
      methods: [
        { id: 'moonshot:key', kind: 'key', label_key: 'method.apiKey', catalog_id: 'moonshot', available: true },
        { id: 'kimi-code:key', kind: 'key', label_key: 'method.kimiCode', catalog_id: 'kimi-code', available: true },
      ],
    })
    useAiStore.setState({
      caps: { ...connectCapsOf([connection('p_k', { vendor: 'moonshot', method: 'kimi-code:key' })]), vendors: [kimi] },
    })
    await render(<ConnectAiDialog open onClose={() => {}} />)
    await click(q('[data-connect-vendor="moonshot"] [data-connect-action="methods"]'))
    expect(q('[data-connect-method="moonshot:key"]')!.dataset.connectMethodState).toBe('none')
    expect(q('[data-connect-method="kimi-code:key"]')!.dataset.connectMethodState).toBe('ready')
    // 老后端没给 method / catalog_id、又分不出是哪种 Key：都不认
    useAiStore.setState({ caps: { ...connectCapsOf([connection('p_k', { vendor: 'moonshot' })]), vendors: [kimi] } })
    await click(q('[data-connect-back]'))
    await click(q('[data-connect-vendor="moonshot"] [data-connect-action="methods"]'))
    expect(q('[data-connect-method="moonshot:key"]')!.dataset.connectMethodState).toBe('none')
    expect(q('[data-connect-method="kimi-code:key"]')!.dataset.connectMethodState).toBe('none')
  })

  it('账号登录类方式按 connection_id 认连接', async () => {
    const mm = vendor('minimax', {
      label: 'MiniMax',
      methods: [{ id: 'o', kind: 'oauth', label_key: 'method.account', connection_id: 'minimax-oauth', available: true }, { id: 'k', kind: 'key', label_key: 'method.apiKey', catalog_id: 'minimax', available: true }],
    })
    useAiStore.setState({ caps: { ...connectCapsOf([connection('minimax-oauth', { vendor: 'minimax' })]), vendors: [mm] } })
    await render(<ConnectAiDialog open onClose={() => {}} />)
    await click(q('[data-connect-vendor="minimax"] [data-connect-action="methods"]'))
    expect(q('[data-connect-method="o"]')!.dataset.connectMethodState).toBe('ready')
    expect(q('[data-connect-method="k"]')!.dataset.connectMethodState).toBe('none')
  })
})

describe('登录只认所选方式的连接', () => {
  const KEY = connection('p_openai', { vendor: 'openai', label: 'OpenAI API', method: 'api' })
  const SUB = connection('sub:codex', { vendor: 'openai', label: 'ChatGPT', route: 'cli:codex' })
  const caps = (...c: ReturnType<typeof connection>[]) => ({ ...connectCapsOf(c), vendors: [DS, OPENAI, CLAUDE] })

  it('已有 Key 连接时做订阅登录：登录没成功就点「我已登录」不算成功（不回调、不关窗、留在卡上给失败那句）；订阅真的 ready 了才算', async () => {
    const onClose = vi.fn()
    const onConnected = vi.fn()
    useAiStore.setState({ caps: caps(KEY) })
    serverCaps = caps(KEY) // 登录没成：后端仍只有那条 Key 连接
    await render(<ConnectAiDialog open onClose={onClose} onConnected={onConnected} />)
    await click(q('[data-connect-vendor="openai"] [data-connect-action="methods"]'))
    await click(q('[data-connect-method="sub"]'))
    await click(q('[data-connect-login="terminal"]'))
    expect(onConnected).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    // 读到了状态但订阅没 ready：不是刷新问题——回到「登录」按钮并给失败那句（不进「重试刷新」）
    expect(q('[data-connect-login="retry"]')).toBeNull()
    expect(q('[data-connect-login="idle"]')).toBeTruthy()
    expect(q('[data-connect-login-hint]')!.textContent).toBe(t('settings.agents.oauth.state.failed', { ns: 'dialogs' }))
    // 再点 = 重新发起登录（又一次 POST login）
    const loginPosts = () => calls.filter((c) => c.url.endsWith('/agents/codex/login') && c.method === 'POST').length
    const before = loginPosts()
    await click(q('[data-connect-login="idle"]'))
    expect(loginPosts()).toBe(before + 1)
    expect(q('[data-connect-login="terminal"]')).toBeTruthy()
    // 这回登录真的成了
    serverCaps = caps(KEY, SUB)
    await click(q('[data-connect-login="terminal"]'))
    expect(onConnected).toHaveBeenCalledTimes(1)
    expect(onConnected.mock.calls[0][0].id).toBe('sub:codex')
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('error 状态的 Key 连接：改 Key 是更新不是新建', () => {
  it('二级卡片里的 Key 表单带原连接 id', async () => {
    useAiStore.setState({
      caps: { ...connectCapsOf([connection('p_openai', { vendor: 'openai', method: 'api', state: 'error', error: 'unauthorized' })]), vendors: [DS, OPENAI, CLAUDE] },
    })
    await render(<ConnectAiDialog open onClose={() => {}} />)
    await click(q('[data-connect-vendor="openai"] [data-connect-action="methods"]'))
    await click(q('[data-connect-method="api"]'))
    const input = q<HTMLInputElement>('[data-connect-key-input]')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'sk-fix')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await click(q('[data-connect-key-submit]'))
    expect(calls.find((c) => c.url === '/api/ai/connections' && c.method === 'POST')?.body).toEqual({
      id: 'p_openai',
      vendor: 'openai',
      api_key: 'sk-fix',
    })
  })
})

describe('登录后读不到状态：重试的是刷新，不是重新登录', () => {
  it('capabilities 读取失败进「重试」；点重试只再读 capabilities，不再发起登录', async () => {
    const SUB = connection('sub:codex', { vendor: 'openai', label: 'ChatGPT', route: 'cli:codex' })
    useAiStore.setState({ caps: { ...connectCapsOf([]), vendors: [DS, OPENAI, CLAUDE] } })
    const base = globalThis.fetch
    let fail = true
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url).startsWith('/api/ai/capabilities') && fail) {
        calls.push({ url: String(url), method: 'GET', body: null })
        return new Response('{}', { status: 500, headers: { 'Content-Type': 'application/json' } })
      }
      return base(url as string, init)
    }) as typeof fetch
    const onConnected = vi.fn()
    await render(<ConnectAiDialog open onClose={() => {}} onConnected={onConnected} />)
    await click(q('[data-connect-vendor="openai"] [data-connect-action="methods"]'))
    await click(q('[data-connect-method="sub"]'))
    await click(q('[data-connect-login="terminal"]'))
    expect(q('[data-connect-login="retry"]')).toBeTruthy()
    expect(q('[data-connect-login-hint]')!.textContent).toBe(t('settings.agents.login.refreshFailed', { ns: 'dialogs' }))
    const loginPosts = () => calls.filter((c) => c.url.endsWith('/agents/codex/login') && c.method === 'POST').length
    const before = loginPosts()
    fail = false
    serverCaps = { ...connectCapsOf([SUB]), vendors: [DS, OPENAI, CLAUDE] }
    await click(q('[data-connect-login="retry"]'))
    expect(loginPosts()).toBe(before)
    expect(onConnected).toHaveBeenCalledTimes(1)
  })
})
