/**
 * T03：脚本要命令行参数（`script_needs_arguments`）时，左栏那一行的出口是"填参数、再试一次"。
 * 判据的主语是 `probeScript` 实际收到的第三个参数（精确 token），不是编辑器里显示的值。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRegistry: vi.fn(),
  probeScript: vi.fn(),
  cancelProbe: vi.fn().mockResolvedValue({ cancelling: true }),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
}))

import {
  fetchRegistry,
  probeScript,
  type ProbeResult,
  type RegistryView,
  type ScriptInventoryEntry,
} from '@/lib/api'
import { ScriptLibrary } from '@/components/left/ScriptLibrary'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { setCurrentProjectId } from '@/lib/session'
import { useEnvStore } from '@/store/envStore'
import { useScriptArgvStore } from '@/store/scriptArgvStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const SCRIPT = 'fft.py'
const entry: ScriptInventoryEntry = {
  script: SCRIPT,
  registered: false,
  static_stems: [],
  entry_candidates: ['__main__'],
  reason: 'no_static_output',
  can_probe: true,
}
const view: RegistryView = {
  source: 'tavotto_registry.json',
  scripts: {},
  candidates: [],
  conflicts: {},
  all_scripts: [entry],
}
const needsArgs: ProbeResult = {
  script: SCRIPT,
  entry: null,
  stems: [],
  descriptors: [],
  error: {
    code: 'script_needs_arguments',
    message: '脚本要求命令行参数',
    params: { error: 'SystemExit: 2', parse_kind: 'missing_required' },
  },
  tried: ['__main__'],
  registered: false,
}

let host: HTMLElement
let root: Root
const flush = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}
const setValue = (el: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(async () => {
  localStorage.clear()
  useScriptLibraryStore.getState().clear()
  useScriptRunStore.getState().clear()
  useScriptArgvStore.getState().clear()
  useEnvStore.getState().resetProject()
  setCurrentProjectId('pA')
  vi.mocked(fetchRegistry).mockResolvedValue(view)
  vi.mocked(probeScript).mockReset()
  vi.mocked(probeScript).mockResolvedValueOnce(needsArgs)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ScriptLibrary query="" />
      </TooltipProvider>,
    )
  })
  await flush()
  await act(async () => host.querySelector<HTMLButtonElement>('button[data-script-run]')!.click())
  await flush()
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useEnvStore.getState().resetProject()
  setCurrentProjectId(null)
})

describe('脚本要参数', () => {
  it('第一次不带参数跑：调用形状不变，只有脚本名', () => {
    expect(vi.mocked(probeScript)).toHaveBeenNthCalledWith(1, SCRIPT)
  })

  it('失败的"详情"里有参数编辑器和"再试一次"；填的 token 原样交给下一次试运行', async () => {
    const recovery = host.querySelector<HTMLElement>('[data-script-argv-recovery]')
    expect(recovery).not.toBeNull()
    const add = [...recovery!.querySelectorAll('button')].find((b) => b.textContent?.includes('添加参数'))!
    await act(async () => add.click())
    await act(async () => add.click())
    await act(async () => add.click())
    const [a, b, c] = [...recovery!.querySelectorAll<HTMLInputElement>('li input')]
    setValue(a, '--freq')
    setValue(b, '')
    setValue(c, '中文 x')
    vi.mocked(probeScript).mockResolvedValueOnce({ ...needsArgs, error: null })
    const retry = [...recovery!.querySelectorAll('button')].find((x) =>
      x.textContent?.includes('再试一次'),
    )!
    await act(async () => retry.click())
    await flush()
    expect(vi.mocked(probeScript)).toHaveBeenNthCalledWith(2, SCRIPT, undefined, {
      argv: ['--freq', '', '中文 x'],
      sensitive: false,
    })
  })

  it('别的失败形状不出现参数编辑器', async () => {
    useScriptRunStore.getState().clear()
    vi.mocked(probeScript).mockResolvedValueOnce({
      ...needsArgs,
      error: { code: 'execution_timeout', message: 'x', params: {} },
    })
    await act(async () => host.querySelector<HTMLButtonElement>('button[data-script-run]')!.click())
    await flush()
    expect(host.querySelector('[data-script-argv-recovery]')).toBeNull()
  })
})
