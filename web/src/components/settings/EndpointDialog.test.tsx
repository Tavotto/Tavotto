/**
 * 「为 X 添加模型服务」对话框（审计 T45）。
 *
 * 盯着四条：① 新建时先选预设，第二步只剩名称 + 密钥；② 模型是一串条目、
 * 第一个是默认；③ 必填只有名称，缺了指出**是哪一格**；④ 隐私说明不虚构安全
 * 保证——特别是权限那句，Windows 上后端根本不做，文案不能说成已经收好了。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n, t } from '@/i18n'
import { EndpointDialog } from './EndpointDialog'
import type { AiEndpoint, AiEndpointPreset } from '@/lib/api'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
Element.prototype.hasPointerCapture ??= () => false
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as never

const ag = (key: string, v?: Record<string, unknown>) =>
  t(`settings.agents.${key}`, { ns: 'dialogs', ...(v ?? {}) })

const PRESETS: AiEndpointPreset[] = [
  {
    id: 'glm',
    label: 'GLM',
    agent: 'codex',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    models: ['glm-4.6', 'glm-4.5-air'],
    wire_api: 'chat',
  },
  { id: 'kimi', label: 'Kimi', agent: 'codex', base_url: 'https://api.moonshot.cn/v1', models: [] },
]

const EXISTING: AiEndpoint = {
  id: 'e1',
  label: '我的网关',
  agent: 'codex',
  base_url: 'https://gw.example/v1',
  models: ['a-model', 'b-model'],
  default_model: 'a-model',
  wire_api: 'chat',
  has_key: true,
  key_hint: '…7f2a',
}

let host: HTMLDivElement
let root: Root
const onSave = vi.fn()
const onClose = vi.fn()

const text = () => document.body.textContent ?? ''
const buttons = () => [...document.body.querySelectorAll('button')] as HTMLButtonElement[]
const byText = (label: string) => buttons().find((b) => b.textContent?.trim() === label)
const byAria = (label: string) =>
  document.body.querySelector<HTMLElement>(`[aria-label="${label}"]`)
const input = (label: string) => byAria(label) as HTMLInputElement
const step = () => document.body.querySelector('[data-endpoint-step]')?.getAttribute('data-endpoint-step')

async function mount(over: Partial<Parameters<typeof EndpointDialog>[0]> = {}) {
  await act(async () => {
    root.render(
      <EndpointDialog
        agent="codex"
        agentLabel="Codex"
        wireApi
        existing={null}
        presets={PRESETS}
        onClose={onClose}
        onSave={onSave}
        {...over}
      />,
    )
  })
}

/** Radix 的下拉，选项在 portal 里，要先点开 */
const openSelect = async (ariaLabel: string) => {
  const trigger = document.body.querySelector<HTMLElement>(
    `[role="combobox"][aria-label="${ariaLabel}"]`,
  )!
  expect(trigger, `下拉不见了：${ariaLabel}`).toBeTruthy()
  await act(async () => trigger.click())
  return [...document.body.querySelectorAll('[role="option"]')] as HTMLElement[]
}

const type = async (label: string, value: string) => {
  const el = input(label)
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(() => {
  onSave.mockReset()
  onClose.mockReset()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  if (i18n.language !== 'zh-CN') await i18n.changeLanguage('zh-CN')
})

describe('先选预设，再只填所需的（审计 T45）', () => {
  it('新建时第一步只有预设选择：没有地址 / 密钥 / 协议，也没有保存', async () => {
    await mount()
    expect(step()).toBe('preset')
    expect(byAria(ag('endpoint.apiKey'))).toBeNull()
    expect(byAria(ag('endpoint.baseUrl'))).toBeNull()
    expect(text()).not.toContain(ag('endpoint.wireChat'))
    expect(byText(t('actions.save', { ns: 'common' }))).toBeUndefined()
  })

  it('选一个预设：地址 / 模型 / 协议由它填好，第二步只剩名称与密钥', async () => {
    await mount()
    const options = await openSelect(ag('endpoint.presetAria'))
    expect(options.map((o) => o.textContent?.trim())).toEqual(['GLM', 'Kimi'])
    await act(async () => options[0].click())

    expect(step()).toBe('form')
    expect(input(ag('endpoint.name')).value).toBe('GLM')
    expect(byAria(ag('endpoint.apiKey'))).toBeTruthy()
    // 预设填好的三项折起来，但内容确实是那份预设的
    expect(text()).toContain(ag('endpoint.presetFilled'))
    expect(input(ag('endpoint.baseUrl')).value).toBe('https://open.bigmodel.cn/api/paas/v4')
    expect([...document.body.querySelectorAll('[data-model-list] > span')].map((s) =>
      s.textContent?.replace(ag('endpoint.modelDefault'), '').trim(),
    )).toEqual(['glm-4.6', 'glm-4.5-air'])
  })

  it('没有合适的预设时可以手动填写，那时字段一起摊开、不折叠', async () => {
    await mount()
    await act(async () => byText(ag('endpoint.manual'))!.click())
    expect(step()).toBe('form')
    expect(text()).not.toContain(ag('endpoint.presetFilled'))
    expect(byAria(ag('endpoint.baseUrl'))).toBeTruthy()
    expect(input(ag('endpoint.name')).value).toBe('')
  })

  it('一条预设都没有时不摆一个只有占位文案的下拉，直接进表单', async () => {
    await mount({ presets: [] })
    expect(step()).toBe('form')
    expect(document.body.querySelector(`[aria-label="${ag('endpoint.presetAria')}"]`)).toBeNull()
  })

  it('编辑已有的接口直接进第二步，模型是它自己那几条', async () => {
    await mount({ existing: EXISTING })
    expect(step()).toBe('form')
    expect(input(ag('endpoint.name')).value).toBe('我的网关')
    expect(text()).toContain('a-model')
    expect(text()).toContain('b-model')
    // 密钥只写不读：留空即保留，占位文案说清这件事
    expect(input(ag('endpoint.apiKey')).placeholder).toContain('…7f2a')
  })
})

describe('模型是一串条目，不是一行逗号分隔的字符串', () => {
  it('回车加一条，且不提交对话框；第一个标着「默认」', async () => {
    await mount({ presets: [] })
    // **名字先填上**：空着的话必填校验会先挡下 submit，「没有提交」这条断言
    // 就成了恒真——它证明不了回车没顺手提交（审计 T45 的用例自查）
    await type(ag('endpoint.name'), '我的网关')
    await type(ag('endpoint.modelDraftAria'), 'first-model')
    await act(async () => {
      input(ag('endpoint.modelDraftAria')).dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
      )
    })
    expect(onSave).not.toHaveBeenCalled()
    const chips = [...document.body.querySelectorAll('[data-model-list] > span')]
    expect(chips).toHaveLength(1)
    expect(chips[0].textContent).toContain(ag('endpoint.modelDefault'))
    expect(input(ag('endpoint.modelDraftAria')).value).toBe('')
  })

  it('可以逐条移除；移除之后保存出去的就是剩下的那几条', async () => {
    await mount({ existing: EXISTING })
    await act(async () => byAria(ag('endpoint.modelRemove', { name: 'a-model' }))!.click())
    await act(async () => byText(t('actions.save', { ns: 'common' }))!.click())
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ models: ['b-model'] }))
  })

  it('重复的模型名不会加进去两遍', async () => {
    await mount({ existing: EXISTING })
    await type(ag('endpoint.modelDraftAria'), 'a-model')
    await act(async () => byText(ag('endpoint.modelAdd'))!.click())
    expect(document.body.querySelectorAll('[data-model-list] > span')).toHaveLength(2)
  })
})

describe('必填与错误定位', () => {
  it('必填只有名称一项，并且标了出来', async () => {
    await mount({ presets: [] })
    const nameRow = input(ag('endpoint.name')).closest('label')!
    expect(nameRow.textContent).toContain(ag('endpoint.requiredAria'))
    // 地址不是必填：留空的语义是「用这个 CLI 自己的登录态」
    const urlRow = input(ag('endpoint.baseUrl')).closest('label')!
    expect(urlRow.textContent).not.toContain(ag('endpoint.requiredAria'))
  })

  it('名字空着点保存：指出是哪一格，不发保存', async () => {
    await mount({ presets: [] })
    await act(async () => byText(t('actions.save', { ns: 'common' }))!.click())
    expect(onSave).not.toHaveBeenCalled()
    const alert = document.body.querySelector('[role="alert"]')!
    expect(alert.textContent).toBe(ag('endpoint.nameRequired'))
    expect(input(ag('endpoint.name')).getAttribute('aria-invalid')).toBe('true')
  })

  it('填上名字之后错误消失，保存真的发出去', async () => {
    await mount({ presets: [] })
    await act(async () => byText(t('actions.save', { ns: 'common' }))!.click())
    await type(ag('endpoint.name'), '  我的网关  ')
    expect(document.body.querySelector('[role="alert"]')).toBeNull()
    await act(async () => byText(t('actions.save', { ns: 'common' }))!.click())
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ label: '我的网关', agent: 'codex' }))
  })
})

describe('隐私说明说的是核过的事实（审计 T45）', () => {
  it('首屏一句短话，细节展开才有', async () => {
    await mount({ presets: [] })
    expect(text()).toContain(ag('endpoint.keyNote'))
    expect(text()).toContain(ag('endpoint.keyNoteMore'))
  })

  it('不承诺 Windows 上有权限保护——后端在那儿根本不做这一步', async () => {
    await mount({ presets: [] })
    const perms = ag('endpoint.keyNotePerms')
    expect(perms).toContain('Windows')
    // 「已经收好了」这种既成事实的说法不许出现：`_harden()` 是保存时才跑的
    expect(ag('endpoint.keyNote')).not.toContain('权限')
    await i18n.changeLanguage('en-US')
    expect(ag('endpoint.keyNotePerms')).toContain('Windows')
  })
})
