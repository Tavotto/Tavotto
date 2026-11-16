/**
 * 「连接 AI」：连接弹窗、设置页的同级列表、彩色 logo。
 *
 * 判据的主语：**界面上看得到的顺序 / 个数 / 发出去的请求**（POST /api/ai/connections 的 body、登录接口有没有被调），
 * 后端用假的 fetch，不联网、不真登录。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SettingsDialog } from '@/components/SettingsDialog'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { setLocale, t } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { useUiStore } from '@/store/uiStore'
import { agentCaps, capsOf, connection, connectCapsOf } from '../testCaps'
import { BrandLogo } from '../BrandLogo'
import { BRAND_LOGOS } from '../brandLogoData'
import { CompatibleEndpoint } from './CompatibleEndpoint'
import { CONNECT_AI_VISIBLE, ConnectAiDialog } from './ConnectAiDialog'

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
// jsdom 没有 canvas：品牌画拿不到上下文就什么都不画（它的行为在 connectAiLayout.test 里测）
HTMLCanvasElement.prototype.getContext = (() => null) as never
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const cx = (k: string, v?: Record<string, unknown>) => t(`settings.connectAi.${k}`, { ns: 'dialogs', ...(v ?? {}) })

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

interface Call {
  url: string
  method: string
  body: Record<string, unknown> | null
}
let calls: Call[]
/** 当前「后端」的 capabilities；POST /connections 成功后可换成连好之后的版本 */
let serverCaps: ReturnType<typeof connectCapsOf>
let onPost: (call: Call) => Response | null

beforeEach(() => {
  calls = []
  serverCaps = connectCapsOf()
  onPost = () => null
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    const u = String(url)
    const call: Call = {
      url: u,
      method: (init?.method ?? 'GET').toUpperCase(),
      body: init?.body ? JSON.parse(String(init.body)) : null,
    }
    calls.push(call)
    const custom = onPost(call)
    if (custom) return custom
    if (u.startsWith('/api/ai/capabilities')) return json(serverCaps)
    return json({})
  }) as typeof fetch
  useAiStore.setState({ caps: serverCaps, agent: null, connection: null })
})

let host: HTMLDivElement
let root: Root
afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
  await setLocale('zh-CN')
  useAiStore.setState({ caps: null })
  useUiStore.setState({ settingsOpen: false, settingsSection: null })
})

async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
  await act(async () => {})
}

const rows = () => [...document.body.querySelectorAll<HTMLElement>('[data-connect-vendor]')]
const ids = () => rows().map((r) => r.dataset.connectVendor)
const row = (id: string) => document.body.querySelector<HTMLElement>(`[data-connect-vendor="${id}"]`)!
const type = async (input: HTMLInputElement, value: string) => {
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    set.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const click = async (el: Element) => {
  await act(async () => {
    ;(el as HTMLElement).click()
  })
  await act(async () => {})
}

describe('ConnectAiDialog：排序与个数', () => {
  it('中文界面：国内在前，只露前 5 家', async () => {
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(ids()).toEqual(['deepseek', 'kimi', 'qwen', 'zhipu', 'chatgpt'])
    expect(rows()).toHaveLength(CONNECT_AI_VISIBLE)
  })

  it('英文界面：海外在前', async () => {
    await setLocale('en-US')
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(ids()).toEqual(['chatgpt', 'claude', 'openai', 'gemini', 'anthropic'])
  })

  it('「更多 AI…」展开全部（自定义不在列表里，另有「其他兼容接口…」）', async () => {
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(document.body.querySelector('[data-connect-other]')).toBeNull()
    await click(document.body.querySelector('[data-connect-more]')!)
    expect(ids()).toHaveLength(9)
    expect(ids()).not.toContain('custom')
    expect(document.body.querySelector('[data-connect-more]')).toBeNull()
    expect(document.body.querySelector('[data-connect-other]')).toBeTruthy()
  })

  it('从修复入口打开时标题多「来修复」，「稍后」直接关闭', async () => {
    const onClose = vi.fn()
    await render(<ConnectAiDialog open list="all" reason="fix" onClose={onClose} />)
    expect(document.body.textContent).toContain(cx('titleFix'))
    expect(document.body.textContent).toContain(cx('subtitle'))
    await click(document.body.querySelector('[data-connect-later]')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('每一行都是彩色 logo + 厂商名', async () => {
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    const r = row('deepseek')
    expect(r.querySelector('[data-brand-logo="deepseek"]')).toBeTruthy()
    expect(r.textContent).toContain('DeepSeek')
  })
})

describe('ConnectAiDialog：连接', () => {
  it('Key 类：点「连接」就地展开 Key 表单 + 获取链接；提交调 POST /api/ai/connections，连好回调并关闭', async () => {
    const onClose = vi.fn()
    const onConnected = vi.fn()
    const conn = connection('deepseek', { label: 'DeepSeek', models: ['deepseek-chat'], default_model: 'deepseek-chat' })
    onPost = (c) => {
      if (c.url === '/api/ai/connections' && c.method === 'POST') {
        serverCaps = connectCapsOf([conn])
        return json({ ok: true, connection: conn })
      }
      return null
    }
    await render(<ConnectAiDialog open list="all" onClose={onClose} onConnected={onConnected} />)
    expect(row('deepseek').querySelector('[data-connect-key-form]')).toBeNull()
    await click(row('deepseek').querySelector('[data-connect-action="key"]')!)
    const link = row('deepseek').querySelector<HTMLAnchorElement>('[data-connect-key-link]')!
    expect(link.href).toBe('https://keys.example/deepseek')
    expect(link.target).toBe('_blank')
    // 没填 Key 时「连接」不可点
    expect(row('deepseek').querySelector<HTMLButtonElement>('[data-connect-key-submit]')!.disabled).toBe(true)

    await type(row('deepseek').querySelector('[data-connect-key-input]')!, ' sk-test-123 ')
    await click(row('deepseek').querySelector('[data-connect-key-submit]')!)

    const post = calls.find((c) => c.url === '/api/ai/connections' && c.method === 'POST')!
    expect(post.body).toEqual({ vendor: 'deepseek', api_key: 'sk-test-123' })
    expect(onConnected).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
    // 连好之前 capabilities 已重读：弹窗关闭时 store 里已经有这条连接
    expect(useAiStore.getState().caps?.connections?.map((c) => c.id)).toEqual(['deepseek'])
  })

  it('保存了但后端试取型号失败（state=error）：不算连好，留在表单上说一句，不关', async () => {
    const onClose = vi.fn()
    onPost = (c) =>
      c.url === '/api/ai/connections' && c.method === 'POST'
        ? json({ ok: true, connection: connection('deepseek', { state: 'error' }) })
        : null
    await render(<ConnectAiDialog open list="all" onClose={onClose} />)
    await click(row('deepseek').querySelector('[data-connect-action="key"]')!)
    await type(row('deepseek').querySelector('[data-connect-key-input]')!, 'sk-bad')
    await click(row('deepseek').querySelector('[data-connect-key-submit]')!)
    expect(row('deepseek').querySelector('[data-connect-error]')?.textContent).toBe(cx('failed'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('订阅类：点「登录」直接发起登录（POST /api/ai/agents/codex/login），不展开表单', async () => {
    onPost = (c) => (c.url.endsWith('/api/ai/agents/codex/login') && c.method === 'POST' ? json({ started: true, mode: 'background' }) : null)
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    await click(row('chatgpt').querySelector('[data-connect-action="login"]')!)
    expect(calls.some((c) => c.url.endsWith('/api/ai/agents/codex/login') && c.method === 'POST')).toBe(true)
    expect(row('chatgpt').querySelector('[data-connect-key-form]')).toBeNull()
    expect(row('chatgpt').querySelector('[data-connect-login="waiting"]')).toBeTruthy()
  })

  it('订阅类（终端登录）：回来点「我已登录」→ 重读 → 订阅连接 ready → 回调并关闭', async () => {
    const onClose = vi.fn()
    const onConnected = vi.fn()
    onPost = (c) => {
      if (c.url.endsWith('/api/ai/agents/codex/login') && c.method === 'POST') {
        serverCaps = connectCapsOf([connection('sub:codex', { vendor: 'chatgpt', label: 'ChatGPT', route: 'cli:codex' })])
        return json({ started: true, mode: 'terminal' })
      }
      return null
    }
    await render(<ConnectAiDialog open list="all" onClose={onClose} onConnected={onConnected} />)
    await click(row('chatgpt').querySelector('[data-connect-action="login"]')!)
    const btn = row('chatgpt').querySelector<HTMLButtonElement>('[data-connect-login="terminal"]')!
    expect(btn.textContent).toBe(t('settings.agents.login.done', { ns: 'dialogs' }))
    await click(btn)
    expect(onConnected).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('已连接的行显示「已连接」，没有连接按钮', async () => {
    useAiStore.setState({ caps: connectCapsOf([connection('deepseek', { label: 'DeepSeek' })]) })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(row('deepseek').querySelector('[data-connect-state="ready"]')?.textContent).toBe(cx('connected'))
    expect(row('deepseek').querySelector('[data-connect-action]')).toBeNull()
  })

  it('登录类厂商对应的命令行没装（available=false）：不给登录按钮', async () => {
    const caps = connectCapsOf()
    caps.vendors = caps.vendors!.map((v) => (v.id === 'chatgpt' ? { ...v, available: false } : v))
    useAiStore.setState({ caps })
    await setLocale('en-US')
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(row('chatgpt').querySelector('[data-connect-action]')).toBeNull()
    expect(row('chatgpt').textContent).toContain(cx('needsInstall'))
  })
})

describe('后端补充约定', () => {
  it('厂商名走 label_key（已有目录文案），没有 label_key 的用 label', async () => {
    const caps = connectCapsOf()
    caps.vendors = caps.vendors!.map((v) => (v.id === 'deepseek' ? { ...v, label: 'DeepSeek-EN', label_key: 'vendor.deepseek' } : v))
    useAiStore.setState({ caps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(row('deepseek').textContent).toContain(t('settings.agents.vendor.deepseek', { ns: 'dialogs' }))
    expect(row('deepseek').textContent).not.toContain('DeepSeek-EN')
    expect(row('chatgpt').textContent).toContain('ChatGPT')
  })

  it('连接异常带原因码时给一句人话；Key 提示（key_hint_key）显示在表单里', async () => {
    const caps = connectCapsOf([connection('deepseek', { state: 'error', error: 'unauthorized' })])
    caps.vendors = caps.vendors!.map((v) => (v.id === 'deepseek' ? { ...v, key_hint_key: 'vendor.deepseek' } : v))
    useAiStore.setState({ caps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(row('deepseek').querySelector('[data-connect-state="error"]')?.textContent).toBe(cx('err.unauthorized'))
    await click(row('deepseek').querySelector('[data-connect-action="key"]')!)
    expect(row('deepseek').querySelector('[data-connect-key-hint]')).toBeTruthy()
  })

  it('已连接的换 Key：POST 带已有连接 id（不先删后建）', async () => {
    useAiStore.setState({ caps: connectCapsOf([connection('p_ds', { vendor: 'deepseek', models: ['a'], default_model: 'a' })]) })
    await openSettings(connectCapsOf([connection('p_ds', { vendor: 'deepseek', models: ['a'], default_model: 'a' })]))
    await click(row('deepseek').querySelector('[data-connect-expand="deepseek"]')!)
    await click(document.body.querySelector('[data-connect-change-key]')!)
    await type(document.body.querySelector('[data-connect-key-input]')!, 'sk-new')
    await click(document.body.querySelector('[data-connect-key-submit]')!)
    expect(calls.find((c) => c.url === '/api/ai/connections' && c.method === 'POST')?.body).toEqual({
      id: 'p_ds',
      vendor: 'deepseek',
      api_key: 'sk-new',
    })
  })
})

/* -------------------------------- 设置页 -------------------------------- */

async function openSettings(caps: ReturnType<typeof connectCapsOf> | ReturnType<typeof capsOf>) {
  serverCaps = caps as never
  useAiStore.setState({ caps: null })
  useUiStore.setState({ settingsOpen: true, settingsSection: 'ai' })
  await render(<SettingsDialog />)
}

describe('设置 → 改图助手：同级列表', () => {
  it('按 vendors 分「国内 / 海外」两组，中文界面国内在前，每行彩色 logo + 名字', async () => {
    await openSettings(connectCapsOf())
    const groups = [...document.body.querySelectorAll<HTMLElement>('[data-connect-group]')]
    expect(groups.map((g) => g.dataset.connectGroup)).toEqual(['cn', 'global'])
    expect([...groups[0].querySelectorAll<HTMLElement>('[data-connect-vendor]')].map((r) => r.dataset.connectVendor)).toEqual([
      'deepseek',
      'kimi',
      'qwen',
      'zhipu',
    ])
    expect(groups[1].querySelectorAll('[data-connect-vendor]')).toHaveLength(5)
    expect(document.body.querySelector('[data-connect-vendor="chatgpt"] [data-brand-logo]')).toBeTruthy()
    // 页底「其他兼容接口…」+「高级」
    expect(document.body.querySelector('[data-connect-other]')).toBeTruthy()
    expect(document.body.querySelector('[data-connect-advanced]')).toBeTruthy()
  })

  it('英文界面海外在前；Codex / Claude Code 不再是一级行（收进「高级」）', async () => {
    await setLocale('en-US')
    await openSettings(connectCapsOf([], { agents: [agentCaps()] }))
    const groups = [...document.body.querySelectorAll<HTMLElement>('[data-connect-group]')]
    expect(groups.map((g) => g.dataset.connectGroup)).toEqual(['global', 'cn'])
    const advanced = document.body.querySelector('[data-connect-advanced]')!
    const agentRow = document.body.querySelector('[data-agent-open="codex"]')!
    expect(advanced.contains(agentRow)).toBe(true)
  })

  it('设置页的「连接」复用同一张 Key 表单，提交走 POST /api/ai/connections', async () => {
    await openSettings(connectCapsOf())
    await click(row('kimi').querySelector('[data-connect-action="key"]')!)
    await type(row('kimi').querySelector('[data-connect-key-input]')!, 'sk-kimi')
    await click(row('kimi').querySelector('[data-connect-key-submit]')!)
    expect(calls.find((c) => c.url === '/api/ai/connections' && c.method === 'POST')?.body).toEqual({
      vendor: 'kimi',
      api_key: 'sk-kimi',
    })
  })

  it('已连接的 Key 类行可展开：默认型号、换 Key、断开', async () => {
    const conn = connection('deepseek', { label: 'DeepSeek', models: ['a', 'b'], default_model: 'a' })
    await openSettings(connectCapsOf([conn]))
    expect(document.body.querySelector('[data-connect-manage]')).toBeNull()
    await click(row('deepseek').querySelector('[data-connect-expand="deepseek"]')!)
    const manage = document.body.querySelector('[data-connect-manage="deepseek"]')!
    expect(manage).toBeTruthy()
    expect(manage.querySelector('[data-connect-change-key]')).toBeTruthy()
    expect(manage.querySelector('[data-connect-disconnect]')).toBeTruthy()
  })

  it('降级：后端没有 vendors / connections 时沿用旧页面，不崩', async () => {
    await openSettings(capsOf([agentCaps()]))
    expect(document.body.querySelector('[data-connect-list]')).toBeNull()
    expect(document.body.querySelector('[data-agent-open="codex"]')).toBeTruthy()
    expect(document.body.querySelector('[data-agent-section="in-app"]')).toBeTruthy()
  })
})

/* -------------------------------- 彩色 logo -------------------------------- */

describe('BrandLogo', () => {
  it('没有底、没有边框：直接画标志（不再是白色圆角矩形）', async () => {
    await render(<BrandLogo logo="deepseek" />)
    const el = host.querySelector<HTMLElement>('[data-brand-logo]')!
    expect(el.className).not.toMatch(/\bbg-|\bborder|rounded/)
  })

  it('深色主题：单色标志（OpenAI / Anthropic …）与 Kimi 的 K 用 currentColor 跟随 text-ink 反色，彩色部分原样', async () => {
    document.documentElement.setAttribute('data-theme', 'dark')
    await render(
      <>
        <BrandLogo logo="chatgpt" />
        <BrandLogo logo="anthropic" />
        <BrandLogo logo="kimi" />
        <BrandLogo logo="deepseek" />
      </>,
    )
    const [gpt, anth, kimi, ds] = [...host.querySelectorAll<HTMLElement>('[data-brand-logo]')]
    for (const el of [gpt, anth, kimi, ds]) expect(el.className).toContain('text-ink')
    for (const el of [gpt, anth]) expect(el.querySelector('svg')!.getAttribute('fill')).toBe('currentColor')
    expect([...kimi.querySelectorAll('path')].map((p) => p.getAttribute('fill'))).toEqual(['#1783FF', 'currentColor'])
    expect(ds.querySelector('path')!.getAttribute('fill')).toBe('#4D6BFE')
    document.documentElement.removeAttribute('data-theme')
  })

  it('有彩色版的厂商用多色路径（不是 currentColor 单色）', async () => {
    for (const key of ['deepseek', 'kimi', 'qwen', 'bailian', 'zhipu', 'minimax', 'claude', 'gemini']) {
      expect(BRAND_LOGOS[key], key).toBeTruthy()
      expect(BRAND_LOGOS[key].paths.some((p) => /^#|^url\(/.test(p.fill)), key).toBe(true)
    }
    await render(<BrandLogo logo="deepseek" />)
    expect(host.querySelector('path')!.getAttribute('fill')).toBe('#4D6BFE')
  })

  it('渐变（Gemini / MiniMax）一起内联，引用的 id 都在 defs 里', async () => {
    await render(<BrandLogo logo="gemini" />)
    const defined = new Set([...host.querySelectorAll('linearGradient')].map((g) => g.id))
    const used = [...host.querySelectorAll('path')].map((p) => /url\(#([^)]+)\)/.exec(p.getAttribute('fill') ?? '')?.[1]).filter(Boolean)
    expect(used.length).toBeGreaterThan(0)
    for (const u of used) expect(defined.has(u!)).toBe(true)
  })

  it('ChatGPT 用 OpenAI 的标志（单色画成纸墨色）；表外的键落到通用图标而不是空白；不加载远程图', async () => {
    await render(
      <>
        <BrandLogo logo="chatgpt" />
        <BrandLogo logo="synthetic" />
      </>,
    )
    const [a, b] = [...host.querySelectorAll<HTMLElement>('[data-brand-logo]')]
    expect(a.dataset.brandLogo).toBe('openai')
    expect(b.dataset.brandLogo).toBe('generic')
    expect(b.querySelector('svg')).toBeTruthy()
    expect(host.querySelector('img, image, use')).toBeNull()
  })
})

/* ---------------------- 其他兼容接口：保存失败 / 刷新失败 ---------------------- */

const passed = { checked: true, protocol: 'openai-chat', requested: null, switched: false, reason: null, reasons: {} }

describe('CompatibleEndpoint', () => {
  const ag = (k: string) => t(`settings.agents.${k}`, { ns: 'dialogs' })
  const posts = () => calls.filter((c) => c.url === '/api/ai/connections' && c.method === 'POST')
  const fill = async () => {
    await type(document.body.querySelector<HTMLInputElement>(`[aria-label="${ag('endpoint.name')}"]`)!, 'LAN model')
    await type(document.body.querySelector<HTMLInputElement>(`[aria-label="${ag('endpoint.baseUrl')}"]`)!, 'https://llm.example/v1')
    await type(document.body.querySelector<HTMLInputElement>(`[aria-label="${ag('endpoint.apiKey')}"]`)!, 'sk-test-12345678')
  }
  const saveBtn = () => [...document.body.querySelectorAll('button')].find((b) => b.textContent === t('common:actions.save'))!
  const okConn = connection('custom', { state: 'ready' })

  it('表单里没有协议选择；保存走 POST /api/ai/connections，不带 wire（自动探测），也不再 PUT /api/ai/endpoints', async () => {
    const onSaved = vi.fn()
    onPost = (c) => (c.url === '/api/ai/connections' && c.method === 'POST' ? json({ ok: true, connection: okConn, probe: passed }) : null)
    await render(<CompatibleEndpoint onClose={() => {}} onSaved={onSaved} />)
    expect(document.body.querySelector('input[type="radio"]')).toBeNull()
    expect(document.body.textContent).not.toMatch(/responses|chat completions|anthropic messages|协议/i)
    await fill()
    await click(saveBtn())
    expect(posts()).toHaveLength(1)
    const body = posts()[0].body!
    expect(body).toMatchObject({ vendor: 'custom', base_url: 'https://llm.example/v1', label: 'LAN model', api_key: 'sk-test-12345678' })
    expect('wire' in body && body.wire !== 'auto').toBe(false)
    expect(calls.some((c) => c.url === '/api/ai/endpoints')).toBe(false)
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('保存失败：表单里显示一句原因，不关、不调 onSaved；改完能再存', async () => {
    const onSaved = vi.fn()
    let fail = true
    onPost = (c) =>
      c.url === '/api/ai/connections' && c.method === 'POST'
        ? fail
          ? json({ code: 'endpoint_invalid', error: 'bad', params: { reason: 'api_key_invalid' } }, 400)
          : json({ ok: true, connection: okConn, probe: passed })
        : null
    await render(<CompatibleEndpoint onClose={() => {}} onSaved={onSaved} />)
    await fill()
    await click(saveBtn())
    expect(document.body.querySelector('[data-endpoint-save-error]')?.textContent).toBe(cx('keyMissing'))
    expect(onSaved).not.toHaveBeenCalled()
    expect(posts()).toHaveLength(1)
    fail = false
    await click(saveBtn())
    expect(posts()).toHaveLength(2)
    expect(onSaved).toHaveBeenCalledTimes(1)
    expect(document.body.querySelector('[data-endpoint-save-error]')).toBeNull()
  })

  it('存下来了但探测不通：表单不关、写一句原因；再点保存改的是同一条（带 id），不新建', async () => {
    const onSaved = vi.fn()
    let n = 0
    onPost = (c) => {
      if (c.url !== '/api/ai/connections' || c.method !== 'POST') return null
      n += 1
      return n === 1
        ? json({
            ok: true,
            connection: connection('custom', { state: 'error', error: 'endpoint_unusable', error_reason: 'web_page' }),
            probe: { ...passed, protocol: null, reason: 'web_page' },
          })
        : json({ ok: true, connection: okConn, probe: passed })
    }
    await render(<CompatibleEndpoint onClose={() => {}} onSaved={onSaved} />)
    await fill()
    await click(saveBtn())
    // 原因贴在「接口地址」下面（不进底栏），悬停补一句常见原因
    const note = document.body.querySelector<HTMLElement>('[data-endpoint-field-note="url"]')!
    expect(note.textContent).toBe(cx('probe.unreachable'))
    expect(note.title).toBe(cx('probeHint'))
    expect(document.body.querySelector('[data-endpoint-save-error]')).toBeNull()
    expect(onSaved).not.toHaveBeenCalled()
    await click(saveBtn())
    expect(posts()).toHaveLength(2)
    expect(posts()[1].body!.id).toBe('custom')
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('型号清单取不到但探测通了（中转站没有 /models）：算保存成功', async () => {
    const onSaved = vi.fn()
    onPost = (c) =>
      c.url === '/api/ai/connections' && c.method === 'POST'
        ? json({ ok: true, connection: connection('custom', { state: 'error', error: 'network' }), probe: passed })
        : null
    await render(<CompatibleEndpoint onClose={() => {}} onSaved={onSaved} />)
    await fill()
    await click(saveBtn())
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('保存成功但刷新失败：只提示并给「重试刷新」，重试只重读、绝不再发新建', async () => {
    const onSaved = vi.fn()
    let capsOk = false
    onPost = (c) => {
      if (c.url === '/api/ai/connections' && c.method === 'POST') return json({ ok: true, connection: okConn, probe: passed })
      if (c.url.startsWith('/api/ai/capabilities') && !capsOk) return json({ code: 'boom' }, 500)
      return null
    }
    await render(<CompatibleEndpoint onClose={() => {}} onSaved={onSaved} />)
    await fill()
    await click(saveBtn())
    expect(posts()).toHaveLength(1)
    expect(document.body.querySelector('[data-endpoint-saved-notice]')).toBeTruthy()
    expect(onSaved).not.toHaveBeenCalled()
    // 再失败一次也还是只重读
    await click(document.body.querySelector('[data-endpoint-retry-refresh]')!)
    expect(posts()).toHaveLength(1)
    capsOk = true
    await click(document.body.querySelector('[data-endpoint-retry-refresh]')!)
    expect(posts()).toHaveLength(1)
    expect(onSaved).toHaveBeenCalledTimes(1)
  })
})

/* ---------------------- 连接卡：地址跑不了对话 → 一句话 + 「检查连接」 ---------------------- */

describe('连接卡：按原因给一个按钮', () => {
  const bad = (reason: string) =>
    connection('deepseek', { state: 'error', error: 'endpoint_unusable', error_reason: reason as never })
  const act1 = () => document.body.querySelector<HTMLButtonElement>('[data-connect-vendor="deepseek"] button[data-connect-action]')
  const state = () => row('deepseek').querySelector('[data-connect-state="error"]')

  it('密钥被拒：小红点 + 「密钥不对」（不是胶囊），按钮「重填密钥」展开 Key 表单', async () => {
    serverCaps = connectCapsOf([bad('unauthorized')])
    useAiStore.setState({ caps: serverCaps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(state()?.textContent).toBe(cx('probe.unauthorized'))
    expect(state()?.className).not.toMatch(/bg-|rounded-full px/)
    expect(act1()?.textContent).toBe(cx('action.refillKey'))
    await click(act1()!)
    expect(row('deepseek').querySelector('[data-connect-key-form]')).not.toBeNull()
  })

  it('地址跑不了对话：「连不上这个地址」+「检查连接」；检查中隐藏文字、按钮转圈；通了变「已连接」', async () => {
    serverCaps = connectCapsOf([bad('web_page')])
    useAiStore.setState({ caps: serverCaps })
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    const base = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
      if (String(url) === '/api/ai/connections/deepseek/check') {
        await gate
        serverCaps = connectCapsOf([connection('deepseek')])
        return json({ ok: true, connection: connection('deepseek'), probe: passed })
      }
      return base(url as never, init)
    }) as typeof fetch
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(state()?.textContent).toBe(cx('probe.unreachable'))
    expect(act1()?.dataset.connectAction).toBe('check')
    await act(async () => void act1()!.click())
    expect(act1()!.disabled).toBe(true)
    expect(act1()!.getAttribute('aria-busy')).toBe('true')
    expect(state()).toBeNull() // 检查中不显示错误字
    await act(async () => release())
    await act(async () => {})
    expect(row('deepseek').querySelector('[data-connect-state="ready"]')).not.toBeNull()
  })

  it('检查后仍不通：换成对应原因的按钮（额度 → 弹窗里不叠层，只剩文字；其余 → 「改地址」）', async () => {
    serverCaps = connectCapsOf([bad('web_page')])
    useAiStore.setState({ caps: serverCaps })
    const base = globalThis.fetch
    globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
      String(url) === '/api/ai/connections/deepseek/check'
        ? json({ ok: true, connection: bad('wrong_protocol'), probe: { ...passed, protocol: null, reason: 'wrong_protocol' } })
        : base(url as never, init)) as typeof fetch
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    await click(act1()!)
    expect(state()?.textContent).toBe(cx('probe.unreachable'))
    expect(act1()?.textContent).toBe(cx('action.editAddress'))
  })

  it('别的错误（Key 被拒这类 unauthorized）：仍是原来的「连接」按钮', async () => {
    serverCaps = connectCapsOf([connection('deepseek', { state: 'error', error: 'unauthorized' })])
    useAiStore.setState({ caps: serverCaps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(row('deepseek').querySelector('[data-connect-action="check"]')).toBeNull()
    expect(row('deepseek').querySelector('[data-connect-action="key"]')).not.toBeNull()
  })
})

describe('自填地址的连接进列表', () => {
  const mine = (over = {}) =>
    connection('mine', { vendor: 'custom', label: '公司中转站', state: 'error', error: 'endpoint_unusable', error_reason: 'web_page', ...over } as never)
  const mineRow = () => document.body.querySelector<HTMLElement>('[data-connect-vendor="mine"]')

  it('有自填连接：厂商后面多一行，名字是连接名、图标是通用图标，状态与按钮同厂商行（连不上这个地址 + 检查连接）', async () => {
    serverCaps = connectCapsOf([mine()])
    useAiStore.setState({ caps: serverCaps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(ids()).toEqual(['deepseek', 'kimi', 'qwen', 'zhipu', 'chatgpt', 'mine'])
    const r = mineRow()!
    expect(r.textContent).toContain('公司中转站')
    expect(r.querySelector('[data-brand-logo="generic"]')).not.toBeNull()
    expect(r.querySelector('[data-connect-state="error"]')?.textContent).toBe(cx('probe.unreachable'))
    expect(r.querySelector('[data-connect-action="check"]')?.textContent).toContain(cx('checkConnection'))
  })

  it('按原因给按钮：额度用完的弹窗里不给；重填密钥打开它的编辑表单并聚焦密钥', async () => {
    serverCaps = connectCapsOf([mine({ error_reason: 'quota' })])
    useAiStore.setState({ caps: serverCaps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(mineRow()!.querySelector('button')).toBeNull()
    await act(async () => root.unmount())
    serverCaps = connectCapsOf([mine({ error_reason: 'unauthorized' })], {
      endpoints: [{ id: 'mine', label: '公司中转站', agent: 'codex', base_url: 'https://x.example/v1', models: [], has_key: true, key_hint: '…1234' } as never],
    })
    useAiStore.setState({ caps: serverCaps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    const b = mineRow()!.querySelector<HTMLButtonElement>('button[data-connect-action]')!
    expect(b.textContent).toBe(cx('action.refillKey'))
    await click(b)
    const { useConnectAiStore } = await import('@/store/connectAiStore')
    expect(useConnectAiStore.getState().editing).toBe('mine')
    expect(useConnectAiStore.getState().editFocus).toBe('key')
    useConnectAiStore.getState().closeEditor()
  })

  it('已连接的自填连接显示「已连接」；没有自填连接时列表里没有多余的行', async () => {
    serverCaps = connectCapsOf([mine({ state: 'ready', error: undefined, error_reason: undefined })])
    useAiStore.setState({ caps: serverCaps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(mineRow()!.querySelector('[data-connect-state="ready"]')).not.toBeNull()
    await act(async () => root.unmount())
    serverCaps = connectCapsOf([connection('deepseek')])
    useAiStore.setState({ caps: serverCaps })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(ids()).toEqual(['deepseek', 'kimi', 'qwen', 'zhipu', 'chatgpt'])
  })
})
