/**
 * 「转换时允许 AI 不受沙盒限制」开关（TVD-BCZ0 方案 B）：
 * ① 后端没给 `convert_unsandboxed_windows`（非 Windows / 老后端）→ 整行不存在；
 * ② Windows（键存在）→ 一行开关，状态取后端值，默认关；
 * ③ 切换调设置接口（PUT），失败则退回原样；
 * ④ 这一行只有标签：没有说明、副标题、警告横条。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  setConvertUnsandboxedWindows: vi.fn(),
}))

import { setConvertUnsandboxedWindows } from '@/lib/api'
import { t } from '@/i18n'
import { useAiStore } from '@/store/aiStore'
import { ConvertSandboxRow } from './ConvertSandboxRow'
import { agentCaps, capsOf } from './testCaps'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const putMock = vi.mocked(setConvertUnsandboxedWindows)
const label = t('settings.agents.convertUnsandboxed', { ns: 'dialogs' })

let host: HTMLDivElement
let root: Root

async function mount(flag: boolean | undefined) {
  const caps = capsOf([agentCaps()], flag === undefined ? {} : { convert_unsandboxed_windows: flag })
  useAiStore.setState({
    caps,
    // 保存后会重新拉能力；这里只关心写接口，拉取原样返回当前 caps
    loadCaps: vi.fn(async () => {}),
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<ConvertSandboxRow />)
  })
}

const sw = () => document.querySelector('[data-convert-unsandboxed] [role="switch"]') as HTMLButtonElement | null

beforeEach(() => {
  putMock.mockReset() // 不要 return 它：beforeEach 返回函数会被当成清理钩子调用
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

describe('ConvertSandboxRow', () => {
  it('后端没给该键（非 Windows / 老后端）：不渲染任何东西', async () => {
    await mount(undefined)
    expect(document.querySelector('[data-convert-unsandboxed]')).toBeNull()
    expect(document.body.textContent).not.toContain(label)
  })

  it('Windows：一行开关，默认关，标签就是那一句，且没有说明 / 警告', async () => {
    await mount(false)
    const row = document.querySelector('[data-convert-unsandboxed]') as HTMLElement
    expect(row.textContent).toBe(label)
    expect(sw()?.getAttribute('aria-checked')).toBe('false')
    expect(document.querySelector('[data-group-notice], [role="alert"], [data-setting-status]')).toBeNull()
  })

  it('后端值为开时显示为开', async () => {
    await mount(true)
    expect(sw()?.getAttribute('aria-checked')).toBe('true')
  })

  it('切换后调设置接口并显示为开', async () => {
    putMock.mockResolvedValue({ convert_unsandboxed_windows: true })
    await mount(false)
    await act(async () => sw()!.click())
    expect(putMock).toHaveBeenCalledWith(true)
    expect(sw()?.getAttribute('aria-checked')).toBe('true')
  })

  it('保存失败：退回原状态', async () => {
    putMock.mockImplementation(async () => {
      throw new Error('boom')
    })
    await mount(false)
    await act(async () => sw()!.click())
    await act(async () => {})
    expect(putMock).toHaveBeenCalledWith(true)
    expect(sw()?.getAttribute('aria-checked')).toBe('false')
  })

  it('保存成功但能力刷新失败：保留已保存的值，不退回', async () => {
    putMock.mockResolvedValue({ convert_unsandboxed_windows: true })
    await mount(false)
    useAiStore.setState({
      loadCaps: vi.fn(async () => {
        throw new Error('refresh boom')
      }),
    })
    await act(async () => sw()!.click())
    await act(async () => {})
    expect(putMock).toHaveBeenCalledWith(true)
    expect(sw()?.getAttribute('aria-checked')).toBe('true')
    expect(sw()?.disabled).toBe(false)
  })
})
