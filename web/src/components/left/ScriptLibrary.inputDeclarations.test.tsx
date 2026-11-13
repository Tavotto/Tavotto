import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/lib/preparationFlag', () => ({ preparationPanelEnabled: () => true }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchRegistry: vi.fn(),
}))

import { fetchRegistry } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useScriptLibraryStore } from '@/store/scriptLibraryStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { TooltipProvider } from '../ui/Tooltip'
import { ScriptLibrary } from './ScriptLibrary'

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  setCurrentProjectId('project-a')
  useProjectPreparationStore.getState().clear()
  useScriptLibraryStore.getState().clear()
  useScriptRunStore.getState().clear()
  vi.mocked(fetchRegistry).mockResolvedValue({
    source: 'tavotto_registry.json', scripts: {}, candidates: [], conflicts: {},
    all_scripts: [{ script: 'plot.py', registered: false, static_stems: [], entry_candidates: ['__main__'], reason: 'no_static_output', can_probe: true }],
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  useScriptLibraryStore.getState().clear()
  useProjectPreparationStore.getState().clear()
  setCurrentProjectId(null)
})

it('无准备条目的已确认项目也能先打开输入设置，零运行意图', async () => {
  const open = vi.spyOn(useProjectPreparationStore.getState(), 'open').mockResolvedValue()
  await act(async () => root.render(<TooltipProvider><ScriptLibrary query="" /></TooltipProvider>))
  const row = host.querySelector('[data-script-row="plot.py"]')!
  expect(row.querySelector('[data-script-prep-status]')).toBeNull()
  await act(async () => row.querySelector('[data-script-run]')!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 })))
  const settings = document.querySelector<HTMLElement>('[data-script-inputs="plot.py"]')
  expect(settings).not.toBeNull()
  await act(async () => settings!.click())
  expect(open).toHaveBeenCalledTimes(1)
  expect(open).toHaveBeenCalledWith({ script: 'plot.py', reuse_last_success: true }, { run: false })
})
