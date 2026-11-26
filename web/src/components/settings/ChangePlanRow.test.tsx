/**
 * 「改图前先给我看修改方案」开关（ADR 0206 PR 4，默认开）：
 * ① 后端没给 `change_plan_enabled`（老后端 / 还没探测到）→ 整行不存在；② 一行开关，状态取后端值（缺省开）；
 * ③ 切换调设置接口，失败退回原样；④ 这一行只有标签。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  setChangePlanEnabled: vi.fn(),
}))

import { setChangePlanEnabled } from '@/lib/api'
import { t } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { ChangePlanRow } from './ChangePlanRow'
import { agentCaps, capsOf } from './testCaps'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const putMock = vi.mocked(setChangePlanEnabled)
const label = t('settings.agents.changePlan', { ns: 'dialogs' })

let host: HTMLDivElement
let root: Root

async function mount(flag: boolean | undefined) {
  const caps = capsOf([agentCaps()])
  if (flag === undefined) delete caps.change_plan_enabled
  else caps.change_plan_enabled = flag
  useAiStore.setState({ caps, loadCaps: vi.fn(async () => {}) })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ChangePlanRow />)
  })
}

const sw = () => document.querySelector('[data-change-plan-switch] [role="switch"]') as HTMLButtonElement | null

beforeEach(() => {
  putMock.mockReset()
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('ChangePlanRow', () => {
  it('后端没给该键：不渲染任何东西', async () => {
    await mount(undefined)
    expect(document.querySelector('[data-change-plan-switch]')).toBeNull()
  })

  it('一行开关，标签就是那一句，没有说明 / 警告；后端值为开时是开', async () => {
    await mount(true)
    expect(document.querySelector('[data-change-plan-switch]')!.textContent).toBe(label)
    expect(sw()?.getAttribute('aria-checked')).toBe('true')
    expect(document.querySelector('[data-group-notice], [role="alert"], [data-setting-status]')).toBeNull()
  })

  it('关掉：调设置接口并显示为关', async () => {
    putMock.mockResolvedValue({ change_plan_enabled: false })
    await mount(true)
    await act(async () => sw()!.click())
    expect(putMock).toHaveBeenCalledWith(false)
    expect(sw()?.getAttribute('aria-checked')).toBe('false')
  })

  it('保存失败：退回原状态', async () => {
    putMock.mockRejectedValue(new Error('boom'))
    await mount(true)
    await act(async () => sw()!.click())
    await act(async () => {})
    expect(sw()?.getAttribute('aria-checked')).toBe('true')
  })
})
