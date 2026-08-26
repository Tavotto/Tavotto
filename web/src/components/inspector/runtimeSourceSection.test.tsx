/**
 * runtime 面板的「源文件」区（负向反证 #5 的看护）：
 * 写回按钮**不出现**，但用户来找写回时能看到原因（没有原始图文件）与
 * 它真正支持的动作（重新运行）；文件面板的写回按钮不受影响。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  probeScript: vi.fn(),
  cancelProbe: vi.fn(),
  fetchPanels: vi.fn().mockResolvedValue({ figures_dir: '', panels: [] }),
  fetchRuntimeAssets: vi.fn().mockResolvedValue({ assets: [] }),
}))

import { probeScript } from '@/lib/api'
import { SourceSection } from '@/components/inspector/PanelSection'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { useScriptRunStore } from '@/store/scriptRunStore'
import type { PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const runtimePanel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'runtime:show.py#show',
  fileKind: 'runtime',
  nativeW: 120,
  nativeH: 90,
  script: 'show.py',
  source: {
    script: 'show.py',
    entry: '__main__',
    stem: 'show',
    captureSource: 'pyplot',
    fingerprint: 'sha256:x',
    sizeMm: [120, 90],
  },
  overrides: [{ gid: 'axes_0.title', prop: 'fontsize', value: 12 }],
  x: 0,
  y: 0,
  w: 120,
  h: 90,
} as PanelObject

const filePanel: PanelObject = {
  id: 'p2',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 120,
  nativeH: 90,
  script: 'fig1.py',
  overrides: [{ gid: 'axes_0.title', prop: 'fontsize', value: 12 }],
  x: 0,
  y: 0,
  w: 120,
  h: 90,
} as PanelObject

let host: HTMLElement
let root: Root

async function mount(panel: PanelObject) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <SourceSection panel={panel} objs={[panel]} />
      </TooltipProvider>,
    )
  })
}

beforeEach(() => {
  localStorage.clear()
  useScriptRunStore.getState().clear()
  // 「源文件与高级」默认折叠；本测试关心的内容都在里面
  useInspectorPrefs.getState().setAdvancedOpen('panel', true)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('runtime 面板的源文件区', () => {
  it('写回入口不出现，但显示原因与「重新运行」（反证 #5）', async () => {
    await mount(runtimePanel)
    const buttons = [...host.querySelectorAll('button')].map((b) => b.textContent ?? '')
    expect(buttons.some((t) => t.includes('写回原始文件'))).toBe(false)
    // 用户找写回时能看到原因——不是无声消失
    expect(host.textContent).toContain('没有对应的原始图文件')
    expect(host.textContent).toContain('导出会创建新文件')
    // 它真正支持的动作
    const rerun = [...host.querySelectorAll('button')].find((b) =>
      (b.textContent ?? '').includes('重新运行'),
    )!
    expect(rerun).toBeTruthy()
    vi.mocked(probeScript).mockImplementation(() => new Promise(() => {}))
    await act(async () => rerun.click())
    expect(probeScript).toHaveBeenCalledWith('show.py')
  })

  it('文件面板的写回按钮照旧（守住对照面）', async () => {
    await mount(filePanel)
    const buttons = [...host.querySelectorAll('button')].map((b) => b.textContent ?? '')
    expect(buttons.some((t) => t.includes('写回原始文件'))).toBe(true)
    expect(host.textContent).not.toContain('没有对应的原始图文件')
  })
})
