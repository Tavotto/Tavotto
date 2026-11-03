/**
 * 脚本库里的 R / MATLAB 脚本（ADR 0143 修订「项目内入口」）：后端 `/api/registry` 的 `foreign_scripts` 单独成一组，
 * 标 R / MATLAB；点开出主页那张“生成可编辑版本”卡（同一个入口），主按钮发的是 source_language 正确的转换请求。
 * 它们不能“打开并运行”：行里没有 ▶ / 状态点，点开也不建准备会话、不碰运行状态机。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRegistry: vi.fn(),
  startConvert: vi.fn(),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
}))

import { fetchRegistry, startConvert, type RegistryView } from '@/lib/api'
import { ForeignScriptCard } from '@/components/PreparationCard'
import { ScriptLibrary } from '@/components/left/ScriptLibrary'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useConvertStore } from '@/store/convertStore'
import { useForeignScriptStore } from '@/store/foreignScriptStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'

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

async function mount(v: RegistryView, query = '') {
  vi.mocked(fetchRegistry).mockResolvedValue(v)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <ScriptLibrary query={query} />
        <ForeignScriptCard />
      </TooltipProvider>,
    )
  })
}

const row = (script: string) => host.querySelector<HTMLElement>(`[data-foreign-script-row="${script}"]`)

beforeEach(() => {
  useScriptLibraryStore.getState().clear()
  useScriptRunStore.getState().clear()
  useConvertStore.setState({ entries: {} })
  useForeignScriptStore.setState({ offer: null })
  vi.mocked(startConvert).mockReset()
  vi.mocked(startConvert).mockReturnValue(new Promise(() => {}))
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('脚本库的 R / MATLAB 脚本', () => {
  it('单独一组，每行标 R / MATLAB；没有“打开并运行”，也不进可运行脚本的行', async () => {
    await mount(
      view({
        all_scripts: [
          { script: 'plot.py', registered: false, static_stems: [], entry_candidates: ['main'], reason: 'static_candidate', can_probe: true },
        ],
        foreign_scripts: [
          { script: 'fig3_yield.R', language: 'r' },
          { script: 'code/trend.m', language: 'matlab' },
        ],
      }),
    )
    expect(host.querySelector('[data-script-group="foreign"]')!.textContent).toContain('R / MATLAB 脚本')
    expect(row('fig3_yield.R')!.textContent).toContain('R')
    expect(row('code/trend.m')!.textContent).toContain('MATLAB')
    // 可运行脚本的行只有 .py
    const runnable = [...host.querySelectorAll('[data-script-row]')].map((e) => e.getAttribute('data-script-row'))
    expect(runnable).toEqual(['plot.py'])
    // R / MATLAB 行里没有“运行 / 打开并运行”的按钮
    expect(row('fig3_yield.R')!.querySelector('[data-script-run], [data-prep-primary]')).toBeNull()
    expect(row('fig3_yield.R')!.textContent).not.toContain('运行')
  })

  it('点 R 行：出主页那张卡（主按钮「生成可编辑版本」），点主按钮发 source_language=r 的转换请求；不建准备会话', async () => {
    await mount(view({ foreign_scripts: [{ script: 'fig3_yield.R', language: 'r' }] }))
    expect(host.querySelector('[data-foreign-card]')).toBeNull()
    await act(async () => row('fig3_yield.R')!.querySelector('button')!.click())
    expect(useForeignScriptStore.getState().offer).toEqual({ script: 'fig3_yield.R', language: 'r' })
    expect(host.querySelector('[data-foreign-card="r"]')).not.toBeNull()
    const start = host.querySelector<HTMLButtonElement>('[data-foreign-start]')!
    expect(start.textContent).toBe('生成可编辑版本')
    await act(async () => start.click())
    expect(vi.mocked(startConvert).mock.calls[0]![0]).toMatchObject({ script: 'fig3_yield.R', source_language: 'r' })
    expect(useProjectPreparationStore.getState().focus).toBeNull()
    expect(useScriptRunStore.getState().byScript['fig3_yield.R']).toBeUndefined()
  })

  it('MATLAB 行同一条路，语言是 matlab', async () => {
    await mount(view({ foreign_scripts: [{ script: 'trend.m', language: 'matlab' }] }))
    await act(async () => row('trend.m')!.querySelector('button')!.click())
    await act(async () => host.querySelector<HTMLButtonElement>('[data-foreign-start]')!.click())
    expect(vi.mocked(startConvert).mock.calls[0]![0]).toMatchObject({ script: 'trend.m', source_language: 'matlab' })
  })

  it('只有 R / MATLAB 脚本也不是空态；搜索按文件名过滤；老后端没有这个字段时一行不多', async () => {
    await mount(view({ foreign_scripts: [{ script: 'a.R', language: 'r' }, { script: 'b.m', language: 'matlab' }] }), 'b.')
    expect(row('a.R')).toBeNull()
    expect(row('b.m')).not.toBeNull()
    await act(async () => root.unmount())
    host.remove()
    useScriptLibraryStore.getState().clear()
    await mount(view())
    expect(host.querySelector('[data-script-group="foreign"]')).toBeNull()
  })
})
