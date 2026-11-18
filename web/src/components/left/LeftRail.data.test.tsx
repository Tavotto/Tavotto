/** 左轨「数据」（从数据到图）：开关关着时左轨逐字不变也不发请求；开着且项目里有数据文件才出现；数据页开着时它高亮、别的项都不亮；点别的项 = 离开数据页。 */
import { createHash } from 'node:crypto'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { aiDataFilesAll } from '@/lib/api'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useTafFlagStore } from '@/store/tafFlagStore'
import { useUiStore } from '@/store/uiStore'
import { LeftRail } from './LeftRail'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
vi.mock('@/lib/api', async (orig) => ({ ...(await orig<typeof import('@/lib/api')>()), aiDataFilesAll: vi.fn() }))
const files = vi.mocked(aiDataFilesAll)

let root: Root
let host: HTMLDivElement
const mount = async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider><LeftRail /></TooltipProvider>))
}
const q = (s: string) => host.querySelector<HTMLElement>(s)
const rails = () => [...host.querySelectorAll('[data-rail]')].map((e) => e.getAttribute('data-rail'))
const one = [{ path: 'a.csv', size: 1 }]

beforeEach(() => {
  files.mockReset()
  files.mockResolvedValue({ files: one, truncated: false })
  useDataFigureStore.getState().clear()
  useTafFlagStore.setState({ enabled: false, settled: true })
  useUiStore.setState({ leftOpen: true, leftTab: 'assets' })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('开关关着', () => {
  it('左轨与合入前主干逐字相同（摘要），没有「数据」，也不去读数据清单', async () => {
    await mount()
    expect(rails()).toEqual(['workspace', 'assets', 'outline', 'elements', 'style', 'problems', 'settings'])
    expect(files).not.toHaveBeenCalled()
    // 摘要在合入前的主干上取（本 PR 动 LeftRail 之前）
    expect(createHash('sha256').update(q('nav')!.outerHTML).digest('hex').slice(0, 16)).toBe('82eed8710a2782cf')
  })
})

describe('开关开着', () => {
  beforeEach(() => useTafFlagStore.setState({ enabled: true, settled: true }))

  it('项目里有数据文件才出现，挨在「素材」后面；没有数据文件不出现', async () => {
    await mount()
    await act(async () => {})
    expect(rails()).toEqual(['workspace', 'assets', 'data', 'outline', 'elements', 'style', 'problems', 'settings'])
    act(() => root.unmount())
    host.remove()
    useDataFigureStore.getState().clear()
    files.mockResolvedValue({ files: [], truncated: false })
    await mount()
    await act(async () => {})
    expect(rails()).not.toContain('data')
  })

  it('「数据」是正式的轨道项：走 railClick 开抽屉（高亮、aria-expanded），再点一次收起；点「素材」换回素材', async () => {
    await mount()
    await act(async () => {})
    useUiStore.setState({ leftOpen: true, leftTab: 'assets' })
    await act(async () => q('[data-rail="data"]')!.click())
    expect(useUiStore.getState()).toMatchObject({ leftTab: 'data', leftOpen: true })
    expect(q('[data-rail="data"]')!.getAttribute('aria-expanded')).toBe('true')
    expect(q('[data-rail="assets"]')!.getAttribute('aria-expanded')).toBe('false')
    await act(async () => q('[data-rail="data"]')!.click())
    expect(useUiStore.getState().leftOpen).toBe(false)
    await act(async () => q('[data-rail="data"]')!.click())
    await act(async () => q('[data-rail="assets"]')!.click())
    expect(useUiStore.getState()).toMatchObject({ leftTab: 'assets', leftOpen: true })
  })

  it('画布不受影响：点「数据」只开抽屉，不开临时标签', async () => {
    await mount()
    await act(async () => {})
    await act(async () => q('[data-rail="data"]')!.click())
    expect(useDataFigureStore.getState().draftOpen).toBe(false)
  })

  it('抽屉当前就是「数据」时即使清单还没读到，「数据」项也在（开抽屉的入口不能消失）', async () => {
    files.mockResolvedValue({ files: [], truncated: false })
    useUiStore.setState({ leftOpen: true, leftTab: 'data' })
    await mount()
    expect(rails()).toContain('data')
  })
})
