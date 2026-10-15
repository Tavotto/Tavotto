/**
 * 属性栏底部（2026-10-16，UI 2.0 §07）：每个折叠行都带当前值；「换一张图」是动作、住进标题行 ⋯；
 * 「改图里的内容」下面不留常驻死空白。
 *
 * 主语：被测的是**选中一张面板时右栏的真实 Inspector**（身份头 + 属性表），不是单个 section。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { literal } from '@/i18n'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject, type PanelObject } from '@/types/document'
import { Inspector } from './Inspector'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel = (id: string, script = '/proj/figs/fig1_kinetics.py'): PanelObject =>
  ({
    id, type: 'panel', x: 0, y: 0, w: 70, h: 50, fileId: `${id}.pdf`, fileKind: 'pdf',
    nativeW: 70, nativeH: 50, script, overrides: [],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

async function mount(objects: CanvasObject[], select: string[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_attr_rows')
  useDocumentStore.getState().commit(literal('放'), (d) => {
    d.objects.push(...objects)
  })
  useSelectionStore.getState().set(select)
  useUiStore.setState({ elementPanelId: null })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <Inspector />
      </TooltipProvider>,
    )
  })
  await act(async () => {})
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

const row = (name: string) =>
  [...host.querySelectorAll<HTMLElement>('[data-summary-row]')].find((r) =>
    r.querySelector('button')?.textContent?.trim().startsWith(name),
  )
const value = (name: string) => row(name)?.querySelector('[data-summary-value]')?.textContent ?? null

describe('折叠行都带当前值', () => {
  it('层级：「第 n 层 / 共 m」按绘制顺序（自底向上）数', async () => {
    await mount([panel('a'), panel('b'), panel('c')], ['b'])
    expect(value('层级')).toBe('第 2 层 / 共 3')
  })

  it('层级：置顶后值跟着变', async () => {
    await mount([panel('a'), panel('b'), panel('c')], ['a'])
    expect(value('层级')).toBe('第 1 层 / 共 3')
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[data-fold="zorder"] > button')!.click()
    })
    // 展开后右值收起；点「置于顶层」再收起，值变成第 3 层
    const top = [...host.querySelectorAll<HTMLButtonElement>('[role="toolbar"][aria-label="层级"] button')][0]
    await act(async () => top.click())
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[data-fold="zorder"] > button')!.click()
    })
    expect(value('层级')).toBe('第 3 层 / 共 3')
  })

  it('源文件：写脚本文件名（mono），完整路径在 title', async () => {
    await mount([panel('a')], ['a'])
    const v = row('源文件')!.querySelector<HTMLElement>('[data-source-script]')!
    expect(v.textContent).toBe('fig1_kinetics.py')
    expect(v.title).toBe('/proj/figs/fig1_kinetics.py')
    expect(v.className).toContain('font-mono')
  })

  it('源文件：Windows 路径也只取文件名', async () => {
    await mount([panel('a', 'C:\\proj\\figs\\fig2.py')], ['a'])
    expect(row('源文件')!.querySelector('[data-source-script]')!.textContent).toBe('fig2.py')
  })

  it('旋转、翻转、透明度：照旧带值', async () => {
    await mount([panel('a')], ['a'])
    expect(value('旋转、翻转、透明度')).toBe('0° · 不透明')
  })
})

describe('「换一张图」进标题行 ⋯', () => {
  const openMenu = async () => {
    const more = host.querySelector<HTMLButtonElement>('[data-identity-more]')!
    await act(async () => {
      more.focus()
      more.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    })
    await act(async () => {})
  }

  it('属性表里不再有这一行；⋯ 菜单里有，键盘（Enter）打开后可达', async () => {
    await mount([panel('a')], ['a'])
    expect(row('换一张图')).toBeUndefined()
    await openMenu()
    const item = document.querySelector<HTMLElement>('[data-replace-picture]')
    expect(item, '⋯ 里没有「换一张图」').toBeTruthy()
    expect(item!.getAttribute('role')).toBe('menuitem')
    expect(item!.textContent).toContain('换一张图')
  })

  it('选它会打开素材选择对话框', async () => {
    // 素材清单已载入：对话框打开时的「没载入就去取」效应不再触发（fetch 在这里是空桩，取不到会一直重试）
    useAssetStore.setState({ panels: [], loaded: true, loading: false, error: null })
    await mount([panel('a')], ['a'])
    await openMenu()
    const item = document.querySelector<HTMLElement>('[data-replace-picture]')!
    await act(async () => {
      item.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
      item.click()
    })
    await act(async () => {})
    expect(document.querySelector('[role="dialog"]')).toBeTruthy()
  })

  it('不是面板（文字）时菜单里没有这一项', async () => {
    const text = { id: 't', type: 'text', x: 0, y: 0, w: 20, h: 6, text: 'hi' } as unknown as CanvasObject
    await mount([text], ['t'])
    await openMenu()
    expect(document.querySelector('[data-replace-picture]')).toBeNull()
  })
})

describe('「改图里的内容」下面没有死空白', () => {
  it('引擎状态提示是 0 高度的浮层（节点仍在，只改可见性），不撑开节', async () => {
    await mount([panel('a')], ['a'])
    const status = host.querySelector<HTMLElement>('[data-panel-engine-status]')!
    expect(status.className).toMatch(/(^|\s)h-0(\s|$)/)
    expect(status.className).not.toMatch(/\bmt-/)
    expect(status.querySelectorAll('[data-panel-engine-message]')).toHaveLength(3)
  })
})
