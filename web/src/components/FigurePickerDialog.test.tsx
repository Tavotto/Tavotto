/**
 * 多 Figure 交接的 Figure 选择器（Session 6）：
 * 全部 Figure 可见、选第二张加的就是第二张（stem/asset id 不串）、
 * 没描述符的条目不渲染假按钮（引导先运行）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/store/actions', () => ({
  addPanel: vi.fn(),
  addRuntimePanel: vi.fn(),
}))

import type { CapturedFigureDescriptor, RuntimeAssetInfo } from '@/lib/api'
import { FigurePickerDialog } from '@/components/FigurePickerDialog'
import { addPanel, addRuntimePanel } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockAddRuntime = vi.mocked(addRuntimePanel)
const mockAddPanel = vi.mocked(addPanel)

function desc(stem: string): CapturedFigureDescriptor {
  return {
    asset_id: `runtime:multi.py#${stem}`,
    script: 'multi.py',
    entry: '__main__',
    stem,
    capture_source: 'pyplot',
    execution_profile: 'safe',
    original_artifact: null,
    size_mm: [80, 60],
    source_fingerprint: 'sha256:x',
    can_writeback_artifact: false,
    can_writeback_source: false,
  }
}

function asset(stem: string, cached = true): RuntimeAssetInfo {
  return {
    id: `runtime:multi.py#${stem}`,
    script: 'multi.py',
    stem,
    entry: '__main__',
    status: cached ? 'fresh' : 'needs_rerun',
    cached,
    size_mm: cached ? [80, 60] : null,
    capture_source: cached ? 'pyplot' : null,
    descriptor: cached ? desc(stem) : null,
  }
}

let root: Root
let host: HTMLDivElement

function render() {
  act(() => {
    root.render(<FigurePickerDialog />)
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  useAssetStore.setState({ panels: [], byId: {}, loaded: true } as never)
  useRuntimeAssetStore.setState({ assets: [], previewNonce: {} } as never)
  useFigurePickerStore.setState({ script: null })
})

afterEach(() => {
  act(() => root.unmount())
  host.remove()
  useFigurePickerStore.setState({ script: null })
})

describe('FigurePickerDialog', () => {
  it('脚本的每张图都可见，选第二张加的就是第二张', () => {
    useRuntimeAssetStore.setState({
      assets: [asset('FigA'), asset('FigB')],
      previewNonce: {},
    } as never)
    useFigurePickerStore.setState({ script: 'multi.py' })
    render()

    const rows = Array.from(document.querySelectorAll('li'))
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('FigA'),
      expect.stringContaining('FigB'),
    ])

    const second = rows[1].querySelector('button')!
    act(() => second.click())

    expect(mockAddRuntime).toHaveBeenCalledTimes(1)
    expect(mockAddRuntime.mock.calls[0][0].asset_id).toBe('runtime:multi.py#FigB')
    expect(mockAddPanel).not.toHaveBeenCalled()
    // 选完关闭
    expect(useFigurePickerStore.getState().script).toBeNull()
  })

  it('磁盘原件条目走 addPanel；runtime 条目走描述符', () => {
    useAssetStore.setState({
      panels: [
        {
          id: 'FigA.pdf',
          name: 'FigA.pdf',
          folder: '.',
          kind: 'pdf',
          native_w_mm: 80,
          native_h_mm: 60,
          mtime: 1,
          script: 'multi.py',
        },
      ],
      byId: {},
      loaded: true,
    } as never)
    useRuntimeAssetStore.setState({ assets: [asset('FigB')], previewNonce: {} } as never)
    useFigurePickerStore.setState({ script: 'multi.py' })
    render()

    const rows = Array.from(document.querySelectorAll('li'))
    expect(rows).toHaveLength(2)
    act(() => rows[0].querySelector('button')!.click())
    expect(mockAddPanel).toHaveBeenCalledTimes(1)
    expect(mockAddRuntime).not.toHaveBeenCalled()
  })

  it('没有描述符的条目不渲染假按钮，引导先运行', () => {
    useRuntimeAssetStore.setState({
      assets: [asset('FigA'), asset('FigB', false)],
      previewNonce: {},
    } as never)
    useFigurePickerStore.setState({ script: 'multi.py' })
    render()

    const rows = Array.from(document.querySelectorAll('li'))
    expect(rows[0].querySelector('button')).not.toBeNull()
    expect(rows[1].querySelector('button')).toBeNull()
    expect(rows[1].textContent).toContain('先运行发现')
  })
})
