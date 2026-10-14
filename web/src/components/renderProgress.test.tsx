/** #479：真实属性页与元素树共用文件级进度，等待超过 toast 时限仍能看见阶段与已用时长。 */
import { act, Profiler } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { handleServerEvent } from '@/hooks/useServerEvents'
import type { ServerEvent } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyDocument, type PanelObject } from '@/types/document'
import { TooltipProvider } from './ui/Tooltip'
import { ElementInspector } from './inspector/ElementInspector'
import { PanelSection } from './inspector/PanelSection'
import { ElementTree } from './left/ElementTree'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const panel = {
  id: 'p_progress', type: 'panel', fileId: 'slow.png', fileKind: 'raster',
  script: 'slow.py', x: 0, y: 0, w: 80, h: 60, nativeW: 80, nativeH: 60, overrides: [],
} as PanelObject
let root: Root
let host: HTMLDivElement
const progress = () => [...host.querySelectorAll<HTMLElement>('[data-render-progress]')]
const event = (kind: string, extra: Record<string, unknown> = {}) =>
  handleServerEvent({ kind, pj: 'p_progress', id: panel.fileId, ...extra } as ServerEvent)

beforeEach(() => {
  localStorage.clear()
  useRenderStore.getState().clear()
  useDocumentStore.setState({ doc: { ...emptyDocument(), objects: [panel] }, past: [], future: [], txn: null })
  useProjectStore.setState({ phase: 'open', project: { open: true, id: 'p_progress' } })
  useAssetStore.setState({ byId: {}, panels: [], loaded: true })
  useSelectionStore.getState().set([panel.id])
  useUiStore.setState({ selectedGids: [], elementPanelId: panel.id, leftTab: 'elements' })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  vi.useFakeTimers()
  vi.setSystemTime(1000)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.useRealTimers()
})

describe('跨过冷启动取会话与脚本执行的持久进度', () => {
  it.each(['elements', 'panel'] as const)('%s 页面与元素树在 15–24 秒的等待中同源更新，完成后撤下', async (surface) => {
    let commits = 0
    useUiStore.setState({ elementPanelId: surface === 'elements' ? panel.id : null })
    await act(async () => root.render(
      <Profiler id="progress" onRender={() => { commits++ }}><TooltipProvider>
        {surface === 'elements' ? <ElementInspector panel={panel} /> : <PanelSection objs={[panel]} />}
        <ElementTree />
      </TooltipProvider></Profiler>,
    ))
    await act(async () => event('render.preparing'))
    expect(progress()).toHaveLength(2)
    for (const node of progress()) expect(node.textContent).toContain('正在准备图表')
    await act(async () => vi.advanceTimersByTime(15000))
    expect(progress()).toHaveLength(2)
    for (const node of progress()) expect(node.textContent).toContain('已用时 15 秒')
    await act(async () => event('render.started', { cold: true, cost: 'medium' }))
    for (const node of progress()) {
      expect(node.textContent).toContain('可能需要几分钟')
      expect(node.textContent).toContain('已用时 15 秒')
    }
    await act(async () => vi.advanceTimersByTime(9000))
    for (const node of progress()) expect(node.textContent).toContain('已用时 24 秒')
    await act(async () => {
      useRenderStore.getState().patch(renderKeyOf(panel), { fileId: panel.fileId, status: 'ready' })
      event('render.done')
    })
    expect(progress()).toHaveLength(0)
    expect(host.textContent).not.toContain('已用时')
    const settledCommits = commits
    await act(async () => vi.advanceTimersByTime(10000))
    expect(commits).toBe(settledCommits)
  })

  it('旧后端只有 started 时仍显示冷启动进度，失败或项目清理后立即撤下', async () => {
    await act(async () => root.render(<TooltipProvider><ElementInspector panel={panel} /><ElementTree /></TooltipProvider>))
    await act(async () => event('render.started', { cold: true, cost: 'medium' }))
    expect(progress()).toHaveLength(2)
    await act(async () => vi.advanceTimersByTime(6000))
    for (const node of progress()) expect(node.textContent).toContain('已用时 6 秒')
    await act(async () => event('render.failed', { error: 'worker unavailable' }))
    expect(progress()).toHaveLength(0)
    await act(async () => event('render.preparing'))
    expect(progress()).toHaveLength(2)
    await act(async () => useRenderStore.getState().clear())
    expect(progress()).toHaveLength(0)
  })
})
