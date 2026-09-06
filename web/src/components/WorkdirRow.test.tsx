/**
 * 「在脚本目录里运行」开关（ADR 0045）。盯三件事：
 * ① 开启要先确认，取消则什么都不改；② 确认后只发 `mode`，成功把「没出图」的面板
 *   重新排上；③ 错误块里的建议只在沙盒模式下出现。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  setProjectWorkdir: vi.fn(),
  fetchEngineEnvironment: vi.fn(),
}))

import { setProjectWorkdir, type EngineEnvironment } from '@/lib/api'
import { WorkdirRow, WorkdirSuggestion } from '@/components/WorkdirRow'
import { i18n, t } from '@/i18n'
import { useEnvStore } from '@/store/envStore'
import { useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const setMock = vi.mocked(setProjectWorkdir)
const en = (key: string) => t(`engine.${key}`, { ns: 'errors' })

const envWith = (mode: 'sandbox' | 'project'): EngineEnvironment =>
  ({
    ok: true,
    python: '/usr/bin/python3',
    source: 'system',
    matplotlib: '3.9',
    managed: false,
    bundled: false,
    runtime: {} as never,
    state: 'idle',
    project: { open: true, workdir: { mode, modes: ['sandbox', 'project'] } },
  }) as never

let host: HTMLDivElement
let root: Root
async function render(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(node)
  })
  await act(async () => {})
}
const text = () => document.body.textContent ?? ''
const switchEl = () => document.querySelector('[role="switch"]') as HTMLButtonElement | null
const answerConfirm = async (ok: boolean) => {
  const req = useUiStore.getState().confirm
  expect(req, '没有弹出确认框').toBeTruthy()
  await act(async () => {
    useUiStore.getState().setConfirm(null)
    req!.resolve(ok)
  })
  await act(async () => {})
}

beforeEach(() => {
  setMock.mockReset()
  useUiStore.getState().setConfirm(null)
  useEnvStore.setState({ env: envWith('sandbox') })
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  await i18n.changeLanguage('zh-CN')
})

describe('在脚本目录里运行', () => {
  it('开启要先确认；取消则什么都不改', async () => {
    await render(<WorkdirRow />)
    expect(text()).toContain(en('workdirHintSandbox'))
    await act(async () => switchEl()!.click())
    await answerConfirm(false)
    expect(setMock).not.toHaveBeenCalled()
    expect(switchEl()!.getAttribute('aria-checked')).toBe('false')
  })

  it('确认后只发 mode，成功后把「没出图」的面板重新排上', async () => {
    setMock.mockResolvedValue({
      ok: true,
      workdir: { mode: 'project', modes: [] },
      project: { open: true, workdir: { mode: 'project', modes: [] } },
    } as never)
    useRenderStore.setState({
      byKey: {
        k: {
          ...(useRenderStore.getState().byKey.k ?? ({} as never)),
          fileId: 'impact.png', status: 'error', code: 'no_figures_captured',
          lastPatches: '[]', wantPatches: '[]', stale: false,
        } as never,
      },
      tracked: {},
    })
    await render(<WorkdirRow />)
    await act(async () => switchEl()!.click())
    await answerConfirm(true)
    expect(setMock).toHaveBeenCalledWith('project')
    expect(switchEl()!.getAttribute('aria-checked')).toBe('true')
    expect(text()).toContain(en('workdirHintProject'))
    expect(useRenderStore.getState().byKey.k.stale, '没重新排上').toBe(true)
  })

  it('关闭不用确认', async () => {
    useEnvStore.setState({ env: envWith('project') })
    setMock.mockResolvedValue({
      ok: true,
      workdir: { mode: 'sandbox', modes: [] },
      project: { open: true, workdir: { mode: 'sandbox', modes: [] } },
    } as never)
    await render(<WorkdirRow />)
    await act(async () => switchEl()!.click())
    await act(async () => {})
    expect(useUiStore.getState().confirm).toBeNull()
    expect(setMock).toHaveBeenCalledWith('sandbox')
  })

  it('错误块里的建议只在沙盒模式下出现', async () => {
    await render(<WorkdirSuggestion />)
    expect(text()).toContain(en('workdirSuggest'))
    await act(async () => root.unmount())
    host.remove()
    useEnvStore.setState({ env: envWith('project') })
    await render(<WorkdirSuggestion />)
    expect(text()).not.toContain(en('workdirSuggest'))
  })

  it('英文界面没有中文泄漏', async () => {
    await i18n.changeLanguage('en-US')
    await render(<WorkdirRow />)
    expect(text()).not.toMatch(/[一-鿿]/)
    expect(text()).toContain("Run in the script's directory")
  })
})
