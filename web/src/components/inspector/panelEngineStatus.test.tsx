/**
 * 引擎提示不能在渲染回包时插拔布局节点，把正在按的属性控件挪走。
 * 这里量状态 / 可访问性 / 节点身份；真正的按钮几何与原生点击由 layout-timeline e2e 量。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { i18n, literal } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { useInspectorPrefs } from '@/store/inspectorPrefs'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { PanelSection } from './PanelSection'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
let panel: PanelObject

beforeEach(async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_engine_status')
  panel = {
    id: 'p_engine_status', type: 'panel', fileId: 'Fig1.pdf', fileKind: 'pdf',
    script: 'fig1.py', figureFrame: 1, nativeW: 80, nativeH: 60,
    x: 10, y: 20, w: 80, h: 60, overrides: [],
  }
  useDocumentStore.getState().commit(literal('panel'), (d) => { d.objects.push(panel) })
  useUiStore.setState({ elementPanelId: null, cropTargetId: null, cropBaseline: null })
  useRenderStore.getState().clear()
  useInspectorPrefs.setState({ moreOpen: { panel: true } })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(async () => { root.render(<TooltipProvider><PanelSection objs={[panel]} /></TooltipProvider>) })
})

afterEach(async () => {
  await act(async () => { root.unmount() })
  host.remove()
  vi.unstubAllGlobals()
})

async function state(building: 'cold' | 'warm' | false, stale = false) {
  await act(async () => {
    useRenderStore.getState().patch(renderKeyOf(panel), { status: building ? 'rendering' : 'ready', stale })
    useRenderStore.getState().noteBuilding(panel.fileId, building ? { cold: building === 'cold', cost: 'light' } : null)
  })
}

const messages = () => [...host.querySelectorAll<HTMLElement>('[data-panel-engine-message]')]
const visible = () => messages().filter((el) => el.getAttribute('aria-hidden') !== 'true')

describe('引擎状态提示保留布局足迹', () => {
  for (const locale of ['zh-CN', 'en-US']) {
    it(`${locale}：冷启动 / 构建 / stale / ready 只换可见内容，不插拔提示与下方按钮`, async () => {
      await act(async () => { await i18n.changeLanguage(locale) })
      const original = messages()
      expect(original).toHaveLength(3)
      const controls = [...host.querySelectorAll('button, input')]
      for (const [building, stale, expected] of [
        ['cold', false, ['cold']], ['warm', false, ['building']], [false, false, []],
        [false, true, ['stale']],
        // 一行只说一件事：构建中优先（它正在把「脚本已变」刷新掉）
        ['cold', true, ['cold']], [false, false, []],
      ] as const) {
        await state(building, stale)
        expect(messages()).toHaveLength(original.length)
        messages().forEach((el, i) => { expect(el).toBe(original[i]); expect(el.isConnected).toBe(true) })
        const currentControls = [...host.querySelectorAll('button, input')]
        expect(currentControls).toHaveLength(controls.length)
        currentControls.forEach((el, i) => { expect(el).toBe(controls[i]); expect(el.isConnected).toBe(true) })
        expect(visible().map((el) => el.dataset.panelEngineMessage)).toEqual(expected)
        for (const el of messages()) {
          expect(el.classList.contains('invisible')).toBe(el.getAttribute('aria-hidden') === 'true')
          expect(el.textContent?.trim()).not.toBe('') // 隐藏也保留真实译文的换行足迹
        }
      }
    })
  }

  it('状态更新后仍可翻转；ready 不会被误报为 busy / stale', async () => {
    await state('cold')
    const flip = host.querySelector<HTMLButtonElement>('[data-panel-flip="h"]')!
    await state(false)
    expect(flip.isConnected).toBe(true)
    expect(flip.disabled).toBe(false)
    expect(visible()).toHaveLength(0)
    await act(async () => { flip.click() })
    expect((useDocumentStore.getState().doc.objects[0] as PanelObject).flipH).toBe(true)
  })
})
