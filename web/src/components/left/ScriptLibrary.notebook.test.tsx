/**
 * 脚本库里的 Jupyter 笔记本（ADR 0179）：后端 `/api/registry` 的 `notebook_scripts`（还没转成脚本的）单独成一组；
 * 每行一颗「转成脚本并运行」钮，点了才让后端抽成同名 .py，然后走现有「打开并运行」。笔记本行不是脚本行：
 * 没有状态点、不进可运行脚本的清单；老后端没有这个字段时一行不多。判据的主语：发给后端的路径与发给准备会话的脚本。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRegistry: vi.fn(),
  extractNotebook: vi.fn(),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
}))

import { ApiError, extractNotebook, fetchRegistry, type RegistryView } from '@/lib/api'
import { formatMessage } from '@/i18n'
import { ScriptLibrary } from '@/components/left/ScriptLibrary'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { setCurrentProjectId } from '@/lib/session'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const view = (over: Partial<RegistryView> = {}): RegistryView => ({
  source: 'tavotto_registry.json',
  scripts: {},
  candidates: [],
  conflicts: {},
  all_scripts: [],
  ...over,
})

let host: HTMLElement
let root: Root
const prepOpen = vi.fn(async (..._a: unknown[]) => {})

async function mount(v: RegistryView, query = '') {
  vi.mocked(fetchRegistry).mockResolvedValue(v)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ScriptLibrary query={query} />
      </TooltipProvider>,
    )
  })
}

const row = (nb: string) => host.querySelector<HTMLElement>(`[data-notebook-row="${nb}"]`)
const runBtn = (nb: string) => host.querySelector<HTMLButtonElement>(`[data-notebook-run="${nb}"]`)!

beforeEach(() => {
  setCurrentProjectId('pj_a')
  useScriptLibraryStore.getState().clear()
  useScriptRunStore.getState().clear()
  useUiStore.setState({ status: null })
  useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
  prepOpen.mockClear()
  useProjectPreparationStore.setState({ open: prepOpen } as never)
  vi.mocked(extractNotebook).mockReset()
  vi.mocked(extractNotebook).mockImplementation(async (nb: string) => ({ notebook: nb, script: nb.replace(/\.ipynb$/, '.py'), status: 'created' }))
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('脚本库的 Jupyter 笔记本', () => {
  it('单独一组、一行一个；不进可运行脚本的行，也没有状态点', async () => {
    await mount(
      view({
        all_scripts: [{ script: 'plot.py', registered: false, static_stems: [], entry_candidates: ['main'], reason: 'static_candidate', can_probe: true }],
        notebook_scripts: [{ notebook: 'fig1.ipynb' }, { notebook: 'sub/fig2.ipynb' }],
      }),
    )
    expect(host.querySelector('[data-script-group="notebook"]')!.textContent).toContain('Jupyter 笔记本')
    expect(row('fig1.ipynb')!.textContent).toContain('fig1.ipynb')
    expect(row('sub/fig2.ipynb')).not.toBeNull()
    expect([...host.querySelectorAll('[data-script-row]')].map((e) => e.getAttribute('data-script-row'))).toEqual(['plot.py'])
    expect(runBtn('fig1.ipynb').getAttribute('aria-label')).toContain('转成脚本并运行')
  })

  it('点主动作：后端按项目相对路径抽取，接着「打开并运行」抽出来的 .py，说一句', async () => {
    await mount(view({ notebook_scripts: [{ notebook: 'sub/fig2.ipynb' }] }))
    await act(async () => runBtn('sub/fig2.ipynb').click())
    expect(vi.mocked(extractNotebook)).toHaveBeenCalledExactlyOnceWith('sub/fig2.ipynb', 'pj_a')
    expect(prepOpen).toHaveBeenCalledExactlyOnceWith({ script: 'sub/fig2.py', reuse_last_success: true }, { run: true, placeIn: undefined })
    const said = formatMessage(useUiStore.getState().status)
    expect(said).toContain('fig2.ipynb')
    expect(said).toContain('fig2.py')
    expect(useUiStore.getState().statusAction).toBeNull()
  })

  it('抽取失败：一句话说原因 + 发送问题反馈，不运行；按钮恢复可点', async () => {
    vi.mocked(extractNotebook).mockRejectedValue(new ApiError('x', 422, { code: 'notebook_unsupported_version', params: { version: 3 } }))
    await mount(view({ notebook_scripts: [{ notebook: 'old.ipynb' }] }))
    await act(async () => runBtn('old.ipynb').click())
    expect(prepOpen).not.toHaveBeenCalled()
    expect(formatMessage(useUiStore.getState().status)).toContain('很旧版本')
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(formatMessage(useUiStore.getState().statusAction!.label)).toBe('发送问题反馈')
    expect(runBtn('old.ipynb').disabled).toBe(false)
  })

  it('只有笔记本也不是空态；搜索按文件名过滤；老后端没有这个字段时一行不多', async () => {
    await mount(view({ notebook_scripts: [{ notebook: 'a.ipynb' }, { notebook: 'b.ipynb' }] }), 'b.')
    expect(row('a.ipynb')).toBeNull()
    expect(row('b.ipynb')).not.toBeNull()
    expect(host.querySelector('[data-script-no-match]')).toBeNull()
    await act(async () => root.unmount())
    host.remove()
    useScriptLibraryStore.getState().clear()
    await mount(view())
    expect(host.querySelector('[data-script-group="notebook"]')).toBeNull()
  })
})
