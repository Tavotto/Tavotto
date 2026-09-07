/**
 * 图片面板的分组（审计 T26 验收：不混淆裁剪、原图尺寸和画布缩放）：
 *   1. 「原始比例 / 原始尺寸」改的是 W/H，所以它们跟着位置与尺寸走；
 *   2. 「图片适配」只剩取景那三件：裁剪 / 完整放入 / 填满框；
 *   3. 每颗适配按钮都是小图标 + 短名称（不是纯图标）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { PanelSection } from './PanelSection'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panelOf = (): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 3.5,
    y: 12,
    w: 73,
    h: 52.6,
    fileId: 'Fig2.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 57.6,
    overrides: [],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

const all = (sel: string) => [...host.querySelectorAll(sel)]
/** 分组标题 → 那一整个 <section> */
const section = (title: string) =>
  all('section').find((s) => s.querySelector('h3')?.textContent?.trim() === title)!

beforeEach(async () => {
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_panel_ops')
  useDocumentStore.getState().commit(literal('放面板'), (d) => {
    d.objects.push(panelOf())
  })
  useUiStore.setState({ cropTargetId: null, cropBaseline: null, elementPanelId: null })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  const panel = useDocumentStore.getState().doc.objects[0] as PanelObject
  await act(async () => {
    root.render(
      <TooltipProvider>
        <PanelSection objs={[panel]} />
      </TooltipProvider>,
    )
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('「原始比例 / 原始尺寸」跟着 W/H 走', () => {
  it('它们在「位置与尺寸」里，不在「图片适配」里', () => {
    const geo = section('位置与尺寸')
    expect(geo.textContent).toContain('原始比例')
    expect(geo.textContent).toContain('原始尺寸')
    const image = section('图片适配')
    expect(image.textContent).not.toContain('原始比例')
    expect(image.textContent).not.toContain('原始尺寸')
  })

  it('缩放与「原始 80 × 57.6」也在同一组里——三个数说的是同一件事', () => {
    const geo = section('位置与尺寸')
    expect(geo.textContent).toContain('缩放')
    expect(geo.textContent).toContain('原始')
  })

  it('「原始尺寸」按下去改的是 W/H', async () => {
    const btn = all('button').find((b) => b.textContent?.includes('原始尺寸'))! as HTMLButtonElement
    await act(async () => btn.click())
    const live = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(live.w).toBeCloseTo(80)
    expect(live.h).toBeCloseTo(57.6)
  })
})

describe('「图片适配」只剩取景三件', () => {
  it('裁剪 / 完整放入 / 填满框，没有别的', () => {
    const image = section('图片适配')
    const names = [...image.querySelectorAll('button')].map((b) => b.textContent?.trim())
    expect(names).toEqual(['裁剪', '完整放入', '填满框'])
  })

  it('每颗都是小图标配短名称，不是纯图标', () => {
    const image = section('图片适配')
    for (const b of image.querySelectorAll('button')) {
      expect(b.querySelector('svg')).not.toBeNull()
      expect(b.textContent?.trim().length).toBeGreaterThan(0)
    }
  })

  it('「裁剪」进裁剪态并记下基线，取消才有得还', async () => {
    const btn = host.querySelector<HTMLButtonElement>('[data-crop-toggle]')!
    await act(async () => btn.click())
    expect(useUiStore.getState().cropTargetId).toBe('p1')
    expect(useUiStore.getState().cropBaseline?.id).toBe('p1')
  })
})
