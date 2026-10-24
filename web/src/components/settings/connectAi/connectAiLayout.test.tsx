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
import { connectCapsOf, vendor } from '../testCaps'
import { BrandLines } from './BrandLines'
import { ConnectAiDialog } from './ConnectAiDialog'

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
    { id: 'sub', kind: 'login', label_key: 'method.subscription', agent: 'codex', available: true },
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
let host: HTMLDivElement
let root: Root
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null as never)
  calls = []
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    calls.push({ url: String(url), method: (init?.method ?? 'GET').toUpperCase(), body: init?.body ? JSON.parse(String(init.body)) : null })
    return json({})
  }) as typeof fetch
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
    expect(brand.className).toContain('hidden')
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
    expect(cards[0].textContent).toContain(cx('method.hintLogin', { name: 'OpenAI' }))
    expect(cards[1].textContent).toContain(cx('method.apiKey'))
    expect(cards[1].textContent).toContain(cx('method.hintKey', { name: 'OpenAI' }))
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
    { getImageData: () => ({ data: new Uint8ClampedArray(96 * 96 * 4).fill(255) }), clearRect: vi.fn() } as Record<string, unknown>,
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
