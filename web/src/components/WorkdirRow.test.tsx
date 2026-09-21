/**
 * 「脚本的运行目录」三档（ADR 0047 / 0057）。盯四件事：
 * ① 切到真实目录（脚本目录 / 项目根）要先确认，取消则什么都不改；② 确认后只发 `mode`，
 *   成功把「没出图」/「先选目录」的面板重新排上；③ 错误块里的建议只在沙盒模式下出现；
 * ④ 老服务端只报两档时第三档不摆出来。
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

const envWith = (
  mode: 'sandbox' | 'project' | 'project_root',
  modes: string[] = ['sandbox', 'project', 'project_root'],
): EngineEnvironment =>
  ({
    ok: true,
    python: '/usr/bin/python3',
    source: 'system',
    matplotlib: '3.9',
    managed: false,
    bundled: false,
    runtime: {} as never,
    state: 'idle',
    project: { open: true, workdir: { mode, modes } },
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
const radios = () => [...document.querySelectorAll('[role="radio"]')] as HTMLButtonElement[]
const radio = (mode: string) =>
  document.querySelector(`[role="radio"][data-value="${mode}"]`) as HTMLButtonElement | null
const checked = () => radios().find((r) => r.getAttribute('aria-checked') === 'true')?.dataset.value
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

describe('脚本的运行目录', () => {
  it('三档都在，当前档选中，切到脚本目录要先确认；取消则什么都不改', async () => {
    await render(<WorkdirRow />)
    expect(text()).toContain(en('workdirHintSandbox'))
    expect(radios().map((r) => r.dataset.value)).toEqual(['sandbox', 'project', 'project_root'])
    expect(checked()).toBe('sandbox')
    await act(async () => radio('project')!.click())
    await answerConfirm(false)
    expect(setMock).not.toHaveBeenCalled()
    expect(checked()).toBe('sandbox')
  })

  it('切到项目根也要确认，文案是项目根那一套', async () => {
    await render(<WorkdirRow />)
    await act(async () => radio('project_root')!.click())
    const req = useUiStore.getState().confirm
    expect(req).toBeTruthy()
    expect(JSON.stringify(req)).toContain('workdirRootConfirmTitle')
    await answerConfirm(false)
    expect(setMock).not.toHaveBeenCalled()
  })

  it('老服务端只报两档时第三档不摆出来', async () => {
    useEnvStore.setState({ env: envWith('sandbox', ['sandbox', 'project']) })
    await render(<WorkdirRow />)
    expect(radios().map((r) => r.dataset.value)).toEqual(['sandbox', 'project'])
  })

  it('确认后只发 mode，成功后把「没出图」的面板重新排上', async () => {
    setMock.mockResolvedValue({
      ok: true,
      workdir: { mode: 'project', modes: [] },
      project: { open: true, workdir: { mode: 'project', modes: ['sandbox', 'project', 'project_root'] } },
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
    await act(async () => radio('project')!.click())
    await answerConfirm(true)
    expect(setMock).toHaveBeenCalledWith('project')
    expect(checked()).toBe('project')
    expect(text()).toContain(en('workdirHintProject'))
    expect(useRenderStore.getState().byKey.k.stale, '没重新排上').toBe(true)
  })

  it('切回沙盒不用确认', async () => {
    useEnvStore.setState({ env: envWith('project_root') })
    setMock.mockResolvedValue({
      ok: true,
      workdir: { mode: 'sandbox', modes: [] },
      project: { open: true, workdir: { mode: 'sandbox', modes: ['sandbox', 'project', 'project_root'] } },
    } as never)
    await render(<WorkdirRow />)
    expect(text()).toContain(en('workdirHintProjectRoot'))
    await act(async () => radio('sandbox')!.click())
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

  it('换项目时 env.project 立刻清掉并按新项目重取（Codex 评审 P1）', async () => {
    const { fetchEngineEnvironment } = await import('@/lib/api')
    const fetchMock = vi.mocked(fetchEngineEnvironment)
    fetchMock.mockReset()
    fetchMock.mockResolvedValue(envWith('sandbox'))
    useEnvStore.setState({ env: envWith('project') })
    await render(<WorkdirSuggestion />)
    expect(text()).not.toContain(en('workdirSuggest'))
    await act(async () => {
      useEnvStore.getState().resetProject()
    })
    // 清掉的那一刻就不再声称旧项目的模式；请求回来后是新项目的
    expect(fetchMock).toHaveBeenCalled()
    await act(async () => {})
    expect(useEnvStore.getState().env?.project?.workdir?.mode).toBe('sandbox')
    expect(text()).toContain(en('workdirSuggest'))
  })

  it('英文界面没有中文泄漏', async () => {
    await i18n.changeLanguage('en-US')
    await render(<WorkdirRow />)
    expect(text()).not.toMatch(/[一-鿿]/)
    expect(text()).toContain('Project root')
  })
})
