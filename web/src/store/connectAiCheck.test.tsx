/**
 * 失败卡「检查连接」（Codex #1523 P2）：打开刚才派活那条连接的编辑表单（地址预填），不是只能新建的通用弹窗；
 * 那条不是 Key 连接时退回通用弹窗。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ConnectAiHost } from '@/components/ConnectAiHost'
import { connectCapsOf, connection } from '@/components/settings/testCaps'
import { TooltipProvider } from '@/components/ui/Tooltip'
import type { AiEndpoint } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { requestCheckConnection, useConnectAiStore } from '@/store/connectAiStore'

const relay: AiEndpoint = {
  id: 'claude-low',
  label: 'claude-low',
  agent: 'codex',
  base_url: 'https://relay.example',
  models: ['claude-sonnet-5-5'],
  default_model: 'claude-sonnet-5-5',
  wire_api: 'responses',
  has_key: true,
  key_hint: '…abcd',
  incompatible: null,
} as AiEndpoint

let root: Root | null = null
beforeEach(() => {
  useConnectAiStore.setState({ open: false, editing: null })
  document.body.innerHTML = ''
})
afterEach(() => {
  act(() => root?.unmount())
  root = null
})

describe('requestCheckConnection', () => {
  it('选中的是 Key 连接：编辑那一条（即使它此刻不算 ready），不开通用弹窗', () => {
    useAiStore.setState({
      caps: connectCapsOf([connection('deepseek'), connection('claude-low', { state: 'error' })], { endpoints: [relay] }),
      connection: 'claude-low',
    } as never)
    requestCheckConnection()
    expect(useConnectAiStore.getState().editing).toBe('claude-low')
    expect(useConnectAiStore.getState().open).toBe(false)
  })

  it('那条不是 Key 连接（订阅登录，没有接口记录）：退回通用的「连接 AI」弹窗', () => {
    useAiStore.setState({
      caps: connectCapsOf([connection('chatgpt', { route: 'cli:codex' })], { endpoints: [] }),
      connection: 'chatgpt',
    } as never)
    requestCheckConnection()
    expect(useConnectAiStore.getState().editing).toBeNull()
    expect(useConnectAiStore.getState().open).toBe(true)
  })

  it('宿主把编辑表单挂出来，地址就是出错的那个', async () => {
    useAiStore.setState({ caps: connectCapsOf([connection('claude-low')], { endpoints: [relay] }), connection: 'claude-low' } as never)
    const host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () =>
      root!.render(
        <TooltipProvider>
          <ConnectAiHost />
        </TooltipProvider>,
      ),
    )
    await act(async () => requestCheckConnection())
    const inputs = [...document.querySelectorAll<HTMLInputElement>('input')]
    expect(inputs.some((i) => i.value === 'https://relay.example')).toBe(true)
  })
})
