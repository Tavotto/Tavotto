/**
 * T03：scriptRunStore 的试运行带上参数草稿——运行开始那一刻取拷贝；换项目丢掉草稿。
 * 判据的主语是 `probeScript` 实际收到的参数，不是 store 里的期望值。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { probeScript, type ProbeResult } from '@/lib/api'
import { useRuntimeAssetStore } from './runtimeAssetStore'
import { useScriptArgvStore } from './scriptArgvStore'
import { useScriptRunStore } from './scriptRunStore'

vi.mock('@/lib/api', () => ({
  probeScript: vi.fn(),
  cancelProbe: vi.fn().mockResolvedValue({ cancelling: true }),
  DEPENDENCY_PREPARATION_CODE: 'dependency_preparation_required',
  WORKDIR_CONFIRMATION_CODE: 'workdir_confirmation_required',
  ApiError: class ApiError extends Error {},
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
  fetchRuntimeStatus: vi.fn(),
  engineRender: vi.fn(),
  EngineError: class EngineError extends Error {},
  INPUT_REMAP_CHANGED_CODE: 'input_remap_changed',
}))

const mockProbe = vi.mocked(probeScript)
const ok = (): ProbeResult => ({
  script: 'fig.py',
  entry: '__main__',
  stems: [],
  descriptors: [],
  error: null,
  tried: ['__main__'],
  registered: true,
})

beforeEach(() => {
  useScriptRunStore.getState().clear()
  useRuntimeAssetStore.getState().clear()
  mockProbe.mockReset()
  mockProbe.mockResolvedValue(ok())
})

describe('试运行带参数', () => {
  it('没有参数：probeScript 只收到脚本名（与 T03 之前一致）', async () => {
    await useScriptRunStore.getState().run('fig.py')
    expect(mockProbe).toHaveBeenCalledWith('fig.py')
  })

  it('有参数：原样交给 probeScript；之后改草稿不影响这一次已发出的', async () => {
    const draft = useScriptArgvStore.getState()
    for (const t of ['--label', '', '中文 x']) draft.addToken('fig.py', t)
    let sent: readonly string[] | undefined
    mockProbe.mockImplementationOnce(async (_s, _c, args) => {
      sent = args?.argv
      useScriptArgvStore.getState().setToken('fig.py', 0, 'CHANGED')
      return ok()
    })
    await useScriptRunStore.getState().run('fig.py')
    expect(sent).toEqual(['--label', '', '中文 x'])
    expect(useScriptArgvStore.getState().drafts['fig.py'].tokens[0]).toBe('CHANGED')
  })

  it('换项目（clear）：草稿一并丢掉', () => {
    useScriptArgvStore.getState().addToken('fig.py', 'tok')
    useScriptRunStore.getState().clear()
    expect(useScriptArgvStore.getState().drafts).toEqual({})
  })
})

it.each(['rc_a', null])('answer rerun selects %s without consuming an unrelated argv draft', async (runConfig) => {
  useScriptArgvStore.getState().setTokens('fig.py', ['--wrong', 'SECRET'])
  await useScriptRunStore.getState().run('fig.py', runConfig)
  expect(mockProbe).toHaveBeenCalledWith('fig.py', undefined, { run_config: runConfig })
  expect(useScriptArgvStore.getState().drafts['fig.py'].tokens).toEqual(['--wrong', 'SECRET'])
})

it('a gated answer rerun retains its configuration after the draft changes', async () => {
  mockProbe.mockResolvedValueOnce({ ...ok(), error: {
    code: 'workdir_confirmation_required', message: 'choose',
    confirmation: { script: 'fig.py' } as never,
  } })
  await useScriptRunStore.getState().run('fig.py', 'rc_a')
  useScriptArgvStore.getState().setTokens('fig.py', ['--wrong'])
  useScriptRunStore.getState().rerunGated('needs_workdir', 'fig.py')
  await Promise.resolve()
  expect(mockProbe).toHaveBeenLastCalledWith('fig.py', undefined, { run_config: 'rc_a' })
})

it('an input-remap retry retains the selected answer configuration', async () => {
  mockProbe.mockResolvedValueOnce({ ...ok(), error: { code: 'input_remap_changed', message: 'retry' } })
  useScriptArgvStore.getState().setTokens('fig.py', ['--wrong'])
  await useScriptRunStore.getState().run('fig.py', 'rc_a')
  await Promise.resolve()
  expect(mockProbe).toHaveBeenCalledTimes(2)
  expect(mockProbe).toHaveBeenLastCalledWith('fig.py', undefined, { run_config: 'rc_a' })
})
