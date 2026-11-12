/**
 * 「更新 Codex」失败要说出来：型号菜单的「更新」项第二行、失败卡的详情，都显示最近一次更新 / 安装的失败原因
 * （与设置页同一份翻译 `installErrorText`）；主按钮仍是「更新 Codex」，可以再试。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { connectCapsOf, connection } from '@/components/settings/testCaps'
import { agentCaps } from '@/components/settings/testCaps'
import { resetAgentInstallStore, useAgentInstallStore } from '@/store/agentInstallStore'
import { useAiStore, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { ScopeAgentContent } from './AiPanel'
import { Turn } from './Transcript'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panelOf = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 75,
    fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 75,
    script: '/tmp/figs/fig1.py', overrides: [],
  }) as unknown as PanelObject

const CG = connection('sub:codex', {
  vendor: 'chatgpt', label: 'ChatGPT', logo: 'chatgpt', route: 'cli:codex',
  models: ['gpt-x'], default_model: 'gpt-x',
  cli_update: { current: '0.150.0', seen: '0.166.0', source: 'homebrew', action: 'install' },
})
const caps = () => ({ ...connectCapsOf([CG]), agents: [agentCaps()] })
const OFFLINE = t('engine.managedCliError.managed_cli_offline', { ns: 'errors', product: 'Codex' })

let root: Root
let host: HTMLDivElement
async function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider>{node}</TooltipProvider>))
}
const tick = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  resetAgentInstallStore()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cli_update_err')
  useAiStore.setState({ caps: caps() as never, agent: null, connection: 'sub:codex', models: {}, efforts: {} })
})
afterEach(async () => {
  await act(async () => root?.unmount())
  resetAgentInstallStore()
})

const fail = (over: Record<string, unknown>) =>
  act(async () => {
    useAgentInstallStore.setState({ byAgent: { codex: { status: 'error', ...over } } as never })
  })

describe('更新失败要说出来', () => {
  it('型号菜单的更新项：下载失败的原因顶替那句提示，项仍可点（再试一次）', async () => {
    await mount(<ScopeAgentContent panel={panelOf()} element={null} axes={null} scope="figure" scopes={['figure']} />)
    await fail({ code: 'managed_cli_offline' })
    await act(async () => {
      host.querySelector<HTMLElement>('[data-ai-model-picker="trigger"]')!.dispatchEvent(
        new PointerEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, pointerType: 'mouse' }),
      )
    })
    await tick()
    const fam = document.body.querySelector<HTMLElement>('[data-ai-connection]')!
    fam.focus()
    await act(async () => {
      fam.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }))
    })
    await tick()
    const item = document.body.querySelector<HTMLElement>('[data-ai-cli-update]')!
    expect(item.textContent).toContain(OFFLINE)
    expect(item.textContent).not.toContain(t('cliUpdate.menuHint', { ns: 'ai' }))
    expect(item.getAttribute('aria-disabled')).not.toBe('true')
  })

  it('失败卡：详情里有失败原因；主按钮仍是「更新 Codex」', async () => {
    const session = {
      id: 's1', project: null, agent: 'codex', agentLabel: 'Codex', prompt: 'x', script: '/tmp/figs/fig1.py',
      panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure', target: '整张图', entries: [],
      status: 'failed', error: 'agent_model_unavailable', changed: false, diff: '', startedAt: 1, finishedAt: 2,
    } as unknown as AiSession
    await mount(<Turn session={session} onResend={() => {}} onRetry={() => {}} canResend canRetry />)
    expect(host.textContent).not.toContain(OFFLINE)
    await fail({ message: 'network down' })
    await act(async () => host.querySelector<HTMLElement>('[data-notice-detail-toggle]')!.click())
    expect(host.querySelector('[data-notice-detail]')!.textContent).toContain('network down')
    await fail({ code: 'managed_cli_offline' })
    expect(host.querySelector('[data-notice-detail]')!.textContent).toContain(OFFLINE)
    const btn = host.querySelector<HTMLButtonElement>('[data-ai-cli-update-card]')!
    expect(btn.textContent).toContain(t('cliUpdate.action', { ns: 'ai', agent: 'Codex' }))
    expect(btn.disabled).toBe(false)
  })
})
