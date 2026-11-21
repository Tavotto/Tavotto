/**
 * 「Tavotto Agent」（内置 AI，ADR 0200）在「连接 AI」里的一行：引擎报告它可用时才出现、没有 Key 表单、一句话说明、点「使用」就选中它；
 * 设置页里不给管理区（没有 Key 可换、不能断开）；不报告就一个字都没有。
 *
 * 判据的主语：**界面上看得到的行 / 按钮 / 被选中的连接**。后端用假的 fetch，不联网。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/Tooltip'
import { setLocale, t } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import { canCheckConnection } from '@/lib/connectionCheck'
import { HOSTED_AI_ID, hostedAiName } from '@/lib/hostedAi'
import { effectiveConnection, type AiVendor } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { connection, connectCapsOf, vendor } from '../testCaps'
import { BrandLogo } from '../BrandLogo'
import { ConnectAiDialog, CONNECT_AI_VISIBLE } from './ConnectAiDialog'
import { ConnectionsList } from './ConnectionsList'

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
HTMLCanvasElement.prototype.getContext = (() => null) as never
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const cx = (k: string, v?: Record<string, unknown>) => t(`settings.connectAi.${k}`, { ns: 'dialogs', ...(v ?? {}) })

const hostedVendor = (): AiVendor =>
  vendor(HOSTED_AI_ID, {
    label: hostedAiName(),
    region: 'global',
    connect: 'hosted',
    key_url: undefined,
    methods: [{ id: `${HOSTED_AI_ID}:hosted`, kind: 'hosted', label_key: 'method.hosted', connection_id: HOSTED_AI_ID, available: true }],
  })
const hostedConnection = () =>
  connection(HOSTED_AI_ID, {
    label: hostedAiName(),
    logo: HOSTED_AI_ID,
    models: ['tavotto-auto'],
    default_model: 'tavotto-auto',
    hosted: true,
  } as never)

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
/** 默认另有一条 ready 的 DeepSeek（所以有效连接是它、Tavotto Agent 此刻没被选中）；`only` = 只有 Tavotto Agent 一条 */
const withHosted = (only = false) => {
  const base = connectCapsOf(only ? [hostedConnection()] : [connection('deepseek'), hostedConnection()])
  return { ...base, vendors: [hostedVendor(), ...(base.vendors ?? [])] }
}

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  globalThis.fetch = (async () => json(withHosted())) as typeof fetch
  useAiStore.setState({ caps: withHosted(), agent: null, connection: null })
})
afterEach(async () => {
  await act(async () => root?.unmount())
  host?.remove()
  document.body.innerHTML = ''
  await setLocale('zh-CN')
  useAiStore.setState({ caps: null, connection: null })
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
const click = async (el: Element) => {
  await act(async () => {
    ;(el as HTMLElement).click()
  })
  await act(async () => {})
}

describe('Tavotto Agent 在连接弹窗里的一行', () => {
  it('排在最前，不占「默认露 5 家」的名额；名字由品牌常量拼出', async () => {
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(ids()[0]).toBe(HOSTED_AI_ID)
    expect(rows()).toHaveLength(CONNECT_AI_VISIBLE + 1)
    expect(row(HOSTED_AI_ID).textContent).toContain(`${PRODUCT_NAME} Agent`)
  })

  it('没有 Key 表单：只有一句话说明和一个「使用」', async () => {
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    useAiStore.setState({ connection: 'deepseek' })
    await act(async () => {})
    const r = row(HOSTED_AI_ID)
    expect(r.querySelector('input')).toBeNull()
    expect(r.querySelector('[data-connect-action="key"]')).toBeNull()
    expect(r.querySelector('[data-connect-hint="hosted"]')!.textContent).toBe(cx('method.hintHosted'))
    expect(r.querySelector('[data-connect-action="hosted"]')!.textContent).toBe(cx('hostedUse'))
  })

  it('点「使用」= 选中这条连接并关闭弹窗，像选任何一条连接', async () => {
    let connected = 0
    useAiStore.setState({ connection: 'deepseek' })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} onConnected={() => (connected += 1)} />)
    expect(effectiveConnection(useAiStore.getState().connection, useAiStore.getState().caps)?.id).toBe('deepseek')
    // 没有首选时 Tavotto Agent 排第一（维护者 2026-11-21）
    expect(effectiveConnection(null, useAiStore.getState().caps)?.id).toBe(HOSTED_AI_ID)
    await click(row(HOSTED_AI_ID).querySelector('[data-connect-action="hosted"]')!)
    expect(useAiStore.getState().connection).toBe(HOSTED_AI_ID)
    expect(connected).toBe(1)
  })

  it('已经是当前连接时显示「使用中」，不再给按钮', async () => {
    useAiStore.setState({ connection: HOSTED_AI_ID })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    const r = row(HOSTED_AI_ID)
    expect(r.querySelector('[data-connect-action="hosted"]')).toBeNull()
    expect(r.querySelector('[data-connect-state="ready"]')!.textContent).toBe(cx('hostedInUse'))
  })

  it('它是唯一一条连接时就是有效连接：显示「使用中」', async () => {
    useAiStore.setState({ caps: withHosted(true) })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(row(HOSTED_AI_ID).querySelector('[data-connect-state="ready"]')!.textContent).toBe(cx('hostedInUse'))
  })

  it('只有它一条 ready 连接时也不走「新手首屏」（已经有能用的 AI），直接是完整列表', async () => {
    useAiStore.setState({ caps: withHosted(true) })
    await render(<ConnectAiDialog open onClose={() => {}} />)
    expect(document.body.querySelector('[data-connect-starter]')).toBeNull()
    expect(ids()[0]).toBe(HOSTED_AI_ID)
  })

  it('引擎没报告它（没授权 / 没开）：弹窗里一个字都没有', async () => {
    const plain = connectCapsOf()
    useAiStore.setState({ caps: plain })
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(ids()).not.toContain(HOSTED_AI_ID)
    expect(document.body.textContent).not.toContain(`${PRODUCT_NAME} Agent`)
  })

  it('英文界面同样有一句话说明', async () => {
    await setLocale('en-US')
    await render(<ConnectAiDialog open list="all" onClose={() => {}} />)
    expect(row(HOSTED_AI_ID).querySelector('[data-connect-hint="hosted"]')!.textContent).toBe(cx('method.hintHosted'))
  })
})

describe('Tavotto Agent 在设置页的列表里', () => {
  it('单独一组放最前、不带分组标题，没有管理区（不能换 Key / 断开）', async () => {
    await render(<ConnectionsList caps={withHosted()} />)
    const groups = [...document.body.querySelectorAll<HTMLElement>('[data-connect-group]')].map((g) => g.dataset.connectGroup)
    expect(groups[0]).toBe('hosted')
    expect(document.body.querySelector('[data-connect-group="hosted"] h3')).toBeNull()
    expect(row(HOSTED_AI_ID).querySelector('[data-connect-expand]')).toBeNull() // 别家 ready 的行有「管理」，它没有
    expect(row(HOSTED_AI_ID)).toBeTruthy()
  })
})

describe('连接上的杂项判据', () => {
  it('不能对它「检查连接」（没有地址和 Key 可探）', () => {
    expect(canCheckConnection({ id: HOSTED_AI_ID })).toBe(false)
    expect(canCheckConnection({ id: 'deepseek' })).toBe(true)
  })

  it('图标是产品自己的图形标，不是通用图标', async () => {
    await render(<BrandLogo logo={HOSTED_AI_ID} size={24} />)
    expect(document.body.querySelector('[data-brand-logo="tavotto"]')).toBeTruthy()
    expect(document.body.querySelector('[data-brand-logo="generic"]')).toBeNull()
  })
})
