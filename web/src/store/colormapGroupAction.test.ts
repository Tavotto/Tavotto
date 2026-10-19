/**
 * 色阶组整组换色的写入口（`setColormapOverride`，用户裁决 2026-10-18，ADR 0135 修订）。
 *
 * 引擎对同组多条 cmap override 取**文档序靠后的**；`upsertOverrides` 原地改值不挪位置——先改色条、
 * 再改曲面、回头又改色条，色条那条仍排在前面，新选的色图会被曲面那条盖掉。写入口因此在同一次
 * commit 里清掉同组其它成员的 cmap override，文档里始终只有一条。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { setColormapOverride, setColormapOverrides } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: () => new Promise(() => {}),
}))

const panel = (overrides: PanelObject['overrides']): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    nativeW: 40,
    nativeH: 30,
    script: 'fig.py',
    overrides,
  }) as PanelObject

const GROUP = ['axes_0.fill_0', 'axes_0.collections_1', 'axes_1.colorbar']
const ov = () => (useDocumentStore.getState().doc.objects[0] as PanelObject).overrides

async function seed(overrides: PanelObject['overrides']) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cmapgrp_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(panel(overrides))
  })
  useDocumentStore.setState({ past: [], future: [] })
}

beforeEach(() => useRenderStore.getState().clear())

describe('setColormapOverride：同组只留一条', () => {
  it('清掉同组其它成员的 cmap override，别的 prop 与组外元素不动', async () => {
    await seed([
      { gid: 'axes_1.colorbar', prop: 'cmap', value: 'plasma' },
      { gid: 'axes_0.fill_0', prop: 'alpha', value: 0.5 },
      { gid: 'axes_0.collections_1', prop: 'cmap', value: 'cividis' },
      { gid: 'axes_9.images_0', prop: 'cmap', value: 'magma' },
    ])
    setColormapOverride('p1', 'axes_0.fill_0', 'viridis', GROUP)
    expect(ov()).toEqual([
      { gid: 'axes_0.fill_0', prop: 'alpha', value: 0.5 },
      { gid: 'axes_9.images_0', prop: 'cmap', value: 'magma' },
      { gid: 'axes_0.fill_0', prop: 'cmap', value: 'viridis' },
    ])
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('回头再改先前改过的那个：新值是唯一一条', async () => {
    await seed([])
    setColormapOverride('p1', 'axes_1.colorbar', 'plasma', GROUP)
    setColormapOverride('p1', 'axes_0.fill_0', 'cividis', GROUP)
    setColormapOverride('p1', 'axes_1.colorbar', 'magma', GROUP)
    expect(ov().filter((p) => p.prop === 'cmap')).toEqual([
      { gid: 'axes_1.colorbar', prop: 'cmap', value: 'magma' },
    ])
  })

  it('同值且组里没有别的条目：什么都不写、不进历史', async () => {
    await seed([{ gid: 'axes_0.fill_0', prop: 'cmap', value: 'viridis' }])
    setColormapOverride('p1', 'axes_0.fill_0', 'viridis', GROUP)
    expect(useDocumentStore.getState().past).toHaveLength(0)
  })
})

describe('setColormapOverrides：多选批量改 cmap 同样整组收口', () => {
  it('被选中成员所在组里其它成员的 cmap override 清掉；组外元素与被写的 gid 各留自己的新值', async () => {
    // A（fill_0）在前、未选中的同组成员 B（collections_1）在后：只原地改 A 的话 B 重放时会把 A 盖回旧色图
    await seed([
      { gid: 'axes_0.fill_0', prop: 'cmap', value: 'plasma' },
      { gid: 'axes_0.collections_1', prop: 'cmap', value: 'cividis' },
      { gid: 'axes_9.images_0', prop: 'cmap', value: 'magma' },
    ])
    setColormapOverrides('p1', literal('批量'), [
      { gid: 'axes_0.fill_0', value: 'viridis', aliasGids: GROUP },
      { gid: 'axes_9.images_0', value: 'viridis', aliasGids: ['axes_9.images_0'] },
    ])
    expect(ov()).toEqual([
      { gid: 'axes_0.fill_0', prop: 'cmap', value: 'viridis' },
      { gid: 'axes_9.images_0', prop: 'cmap', value: 'viridis' },
    ])
    expect(useDocumentStore.getState().past).toHaveLength(1)
  })

  it('同组两个成员都被选中：两条新值都留下，不互相清掉', async () => {
    await seed([{ gid: 'axes_1.colorbar', prop: 'cmap', value: 'plasma' }])
    setColormapOverrides('p1', literal('批量'), [
      { gid: 'axes_0.fill_0', value: 'turbo', aliasGids: GROUP },
      { gid: 'axes_0.collections_1', value: 'turbo', aliasGids: GROUP },
    ])
    expect(ov()).toEqual([
      { gid: 'axes_0.fill_0', prop: 'cmap', value: 'turbo' },
      { gid: 'axes_0.collections_1', prop: 'cmap', value: 'turbo' },
    ])
  })
})
