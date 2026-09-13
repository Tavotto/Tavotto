/**
 * 单选面板的属性页顺序（2026-09-13 审计 B09）：
 *   1. 头部之下是进图内编辑的**紧凑入口**——没有「图内元素」小标题再说一遍；
 *   2. 正文固定为 位置与尺寸 → 图片适配 → 排列 → 更多 → 源文件与高级；
 *   3. 「排列」一组里同时有对齐到画布（六颗）与层级（四颗），位置组里不再夹一行对齐。
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
    x: 6,
    y: 20,
    w: 73.3,
    h: 57.8,
    fileId: 'Fig1_kinetics.pdf',
    fileKind: 'pdf',
    nativeW: 73.3,
    nativeH: 57.8,
    script: 'Fig1_kinetics.py',
    overrides: [],
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

const all = (sel: string) => [...host.querySelectorAll(sel)]
/** 分组标题（h3）与折叠区标题（aria-expanded 的按钮）按出现顺序 */
const headings = () =>
  all('section > header h3, section > button[aria-expanded]').map((el) =>
    (el.querySelector('span') ?? el).textContent?.trim() ?? '',
  )
const section = (title: string) =>
  all('section').find((s) => s.querySelector('h3')?.textContent?.trim() === title)!

beforeEach(async () => {
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_panel_order')
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

describe('单选面板：变换 → 内容适配 → 排列 → 源文件', () => {
  it('分组顺序固定，「图内元素」不再是一个分组标题', () => {
    expect(headings()).toEqual(['位置与尺寸', '图片适配', '排列', '更多', '源文件与高级'])
  })

  it('进图内编辑的入口还在，是头部之下的一颗按钮，不撑满整栏', () => {
    const btn = all('button').find((b) => b.textContent?.trim() === '编辑图内元素') as
      | HTMLButtonElement
      | undefined
    expect(btn).toBeDefined()
    expect(btn!.className).not.toMatch(/\bflex-1\b|\bw-full\b/)
    // 它在第一个分组之前（头部延伸），不在任何带标题的分组里
    const firstTitled = section('位置与尺寸')
    expect(btn!.compareDocumentPosition(firstTitled) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('「排列」里同时有对齐到画布六颗与层级四颗；位置组里没有对齐', () => {
    const arrange = section('排列')
    expect(arrange.querySelector('[data-single-align]')!.querySelectorAll('button')).toHaveLength(6)
    expect(
      arrange.querySelector('[aria-label="层级"][role="toolbar"]')!.querySelectorAll('button'),
    ).toHaveLength(4)
    expect(section('位置与尺寸').querySelector('[role="toolbar"]')).toBeNull()
  })
})
