/**
 * 起步那一屏（审计 T03）。
 *
 * 改造前四段提示各配一颗按钮——空画布、空素材、未选中对象、未选中可编辑图，
 * 每一段都在教用户一件事，而新用户此刻只需要知道**先做哪一件**。
 *
 * 这里守三件事：
 *   1. 画布中央只有**一个**主行动（「添加图」），次级入口画成文字链接而不是
 *      第二颗填色按钮（UI 视觉纪律：每个上下文最多一个填色主动作）；
 *   2. 「试用示例」走 `runTutorialEntry` 的统一入口——教程项目里自己不再出现；
 *   3. 项目里一张图都没有时，说明改成告诉用户**把什么文件放进项目目录**，
 *      因为「添加图」在那个状态下没有东西可添。素材清单还没回来时按「有」说。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CanvasStage } from '@/canvas/CanvasStage'
import { LayerTree } from '@/components/left/LayerTree'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { t } from '@/i18n'
import type { PanelInfo } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

/** jsdom 没有 ResizeObserver；CanvasStage 的视口上报靠它 */
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver

vi.mock('@/lib/onboarding/tutorial', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  runTutorialEntry: vi.fn(async () => ({ ok: true }) as const),
}))
const { runTutorialEntry } = await import('@/lib/onboarding/tutorial')

const sg = (key: string) => t(`stage.${key}`, { ns: 'workspace' })

const asset = (id: string): PanelInfo => ({
  id,
  name: id,
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
})

let root: Root

async function mount(node: React.ReactNode) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}

const byText = (text: string) =>
  [...document.querySelectorAll('button')].filter((b) => b.textContent?.trim() === text)

/** 画成按钮的那些（`Button` 一律带边框 / 底色；次级入口是裸文字链接） */
const chromedButtons = () =>
  [...document.querySelectorAll('button')].filter((b) => b.className.includes('border-border'))

beforeEach(async () => {
  document.body.innerHTML = ''
  vi.clearAllMocks()
  useAssetStore.setState({ panels: [], byId: {}, loaded: false })
  useProjectStore.setState({ project: { open: true, id: 'p1', figures_dir: '/figs' } })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_empty_start')
  useSelectionStore.getState().clear()
  useUiStore.setState({ leftTab: 'assets', tool: 'select', cropTargetId: null })
})

afterEach(async () => {
  await act(async () => root?.unmount())
})

describe('空画布的起步提示', () => {
  it('只有一个主行动「添加图」，「试用示例」是次级链接不是第二颗按钮', async () => {
    await mount(<CanvasStage />)
    expect(byText(sg('addFigure'))).toHaveLength(1)
    expect(byText(sg('tryTutorial'))).toHaveLength(1)
    // 一屏最多一个填色主动作：次级入口不许也是填色按钮
    // 一屏只有一个「行动」的样子：主行动是按钮，次级只是一条文字链接
    expect(chromedButtons().map((b) => b.textContent?.trim())).toEqual([sg('addFigure')])
  })

  it('「添加图」打开素材库；「试用示例」走教程的统一入口', async () => {
    useUiStore.setState({ leftTab: 'structure' })
    await mount(<CanvasStage />)
    await act(async () => byText(sg('addFigure'))[0].click())
    expect(useUiStore.getState().leftTab).toBe('assets')

    await act(async () => byText(sg('tryTutorial'))[0].click())
    expect(runTutorialEntry).toHaveBeenCalledWith('canvas')
  })

  it('教程项目里不再出现「试用示例」', async () => {
    useProjectStore.setState({
      project: { open: true, id: 'p1', figures_dir: '/figs', tutorial: true },
    })
    await mount(<CanvasStage />)
    expect(byText(sg('addFigure'))).toHaveLength(1)
    expect(byText(sg('tryTutorial'))).toHaveLength(0)
  })

  it('项目里一张图都没有：说明改成「把文件放进项目目录」', async () => {
    useAssetStore.setState({ panels: [], byId: {}, loaded: true })
    await mount(<CanvasStage />)
    expect(document.body.textContent).toContain(sg('emptyHintNoAssets'))
    expect(document.body.textContent).not.toContain(sg('emptyHint'))
  })

  it('素材清单还没回来时先按「有图」说，不许先吓唬用户', async () => {
    useAssetStore.setState({ panels: [], byId: {}, loaded: false })
    await mount(<CanvasStage />)
    expect(document.body.textContent).toContain(sg('emptyHint'))
    expect(document.body.textContent).not.toContain(sg('emptyHintNoAssets'))
  })

  it('有图时说明是「从素材库选一张」', async () => {
    useAssetStore.setState({
      panels: [asset('a.pdf')],
      byId: { 'a.pdf': asset('a.pdf') },
      loaded: true,
    })
    await mount(<CanvasStage />)
    expect(document.body.textContent).toContain(sg('emptyHint'))
  })
})

describe('侧栏的空态', () => {
  it('图层树只留轻量占位：起步的那颗按钮只在画布中央出现一次', async () => {
    await mount(<LayerTree />)
    expect(document.body.textContent).toContain(t('layerTree.emptyTitle', { ns: 'workspace' }))
    expect(document.querySelectorAll('button')).toHaveLength(0)
  })
})
