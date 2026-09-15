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
    expect(headings()).toEqual(['位置与尺寸', '图片', '排列', '更多', '源文件与高级'])
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

/* ---------------------- 2026-09-15 全面打磨（L1 / L7 / O1 / O3） ---------------------- */

const toggleByText = (text: string) =>
  all('button[aria-expanded]').find((b) => b.textContent?.trim().startsWith(text)) as
    | HTMLButtonElement
    | undefined

describe('对象页的版式（2026-09-15 全面打磨）', () => {
  it('一个表单一种行语法：这一页所有标签列同宽（L1）', () => {
    // 此前一页两种：缩放 / 取景那几行 44，对齐 / 层级 60——控件起点差 16px
    const widths = new Set(
      all('span[style*="width"]')
        .map((el) => (el as HTMLElement).style.width)
        .filter(Boolean),
    )
    expect(widths).toEqual(new Set(['88px']))
  })

  it('组内的「更多」是文字链接，不带 chevron（O3 / L4）', () => {
    const more = toggleByText('更多')!
    expect(more, '找不到「更多」').toBeTruthy()
    expect(more.querySelector('svg'), '组的尾巴不该用分区的字形').toBeNull()
    // 分区级的折叠（源文件与高级）仍然带 chevron：两种角色靠字形分开
    expect(toggleByText('源文件与高级')!.querySelector('svg')).toBeTruthy()
  })

  it('源文件与高级：写回的那句常驻说明删了，动作收成一行三颗（L7 / O2）', async () => {
    const fold = toggleByText('源文件与高级')!
    await act(async () => fold.click())
    // L7：确认框里已经把「会覆盖原件、留有备份」讲全，这里不再常驻一遍
    expect(host.textContent ?? '').not.toContain('写回会覆盖原始')
    const names = all('button').map((b) => b.textContent?.trim() ?? '')
    for (const want of ['写回原始文件', '历史', '同步修改到']) {
      expect(names.some((n) => n.startsWith(want)), `少了「${want}」`).toBe(true)
    }
    // 三颗在同一行（同一个父元素），不是三种宽度叠三行
    const row = all('button')
      .filter((b) => b.textContent?.trim().startsWith('写回原始文件'))
      .map((b) => b.parentElement)[0]!
    expect(row.querySelectorAll('button')).toHaveLength(3)
  })

  it('诊断是只读的一行，不是 surface-2 / danger 填充的小卡（O1）', async () => {
    const fold = toggleByText('源文件与高级')!
    await act(async () => fold.click())
    const value = all('span').find((el) => /pt$|dpi$/.test(el.textContent?.trim() ?? ''))!
    expect(value, '找不到诊断读数').toBeTruthy()
    expect(value.className).toContain('type-number')
    // 只读值不套容器：这一行上没有填充底
    let node: Element | null = value
    for (let i = 0; i < 3 && node; i++) {
      expect(node.className).not.toMatch(/bg-surface-2|bg-danger-subtle/)
      node = node.parentElement
    }
  })
})
