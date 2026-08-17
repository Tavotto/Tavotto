import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushRender, renderTargets, requestRender, syncEngine } from './useEngineSync'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type CanvasObject, type PanelObject } from '@/types/document'
import type { Manifest } from '@/lib/api'

function panel(id: string, fileId: string, overrides: number): PanelObject {
  return {
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    fileId,
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    script: 'fig.py',
    overrides: Array.from({ length: overrides }, (_, i) => ({
      gid: `g${i}`,
      prop: 'color',
      value: '#000',
    })),
  } as PanelObject
}

/** 记下每一次真正发出的渲染（fileId / patches / preview dpi） */
let calls: { fileId: string; patches: unknown[]; dpi?: number }[] = []

beforeEach(() => {
  calls = []
  useRenderStore.getState().clear()
  useRenderStore.setState({
    render: async (fileId, patches, previewDpi) => {
      calls.push({ fileId, patches, dpi: previewDpi })
    },
  })
})

describe('renderTargets：按变体去重，不再裁一个赢家', () => {
  it('同文件不同 overrides 的两个副本各自入选', () => {
    // 旧实现在这里裁出唯一赢家，输家显示的就是赢家的图
    const objects: CanvasObject[] = [panel('a', 'Fig1.pdf', 2), panel('b', 'Fig1.pdf', 1)]
    const targets = renderTargets(objects, null, { 'Fig1.pdf': true })
    expect(targets.map((t) => t.id)).toEqual(['a', 'b'])
  })

  it('完全相同的两个副本只渲染一次（同一个变体键）', () => {
    const objects: CanvasObject[] = [panel('a', 'Fig1.pdf', 2), panel('b', 'Fig1.pdf', 2)]
    expect(renderTargets(objects, null, {}).map((t) => t.id)).toEqual(['a'])
    expect(renderKeyOf(objects[0] as PanelObject)).toBe(renderKeyOf(objects[1] as PanelObject))
  })

  it('不同文件各自入选', () => {
    const objects: CanvasObject[] = [panel('a', 'Fig1.pdf', 1), panel('b', 'Fig2.pdf', 1)]
    expect(renderTargets(objects, null, {})).toHaveLength(2)
  })

  it('无改动也未被跟踪的面板不进渲染队列', () => {
    // 磁盘文件本身就是那个样子，白跑一次引擎（heavy 脚本要几分钟）没意义
    expect(renderTargets([panel('a', 'Fig1.pdf', 0)], null, {})).toEqual([])
  })

  it('正在图内编辑 / 脚本已领先磁盘的面板照样进队列', () => {
    expect(renderTargets([panel('a', 'Fig1.pdf', 0)], 'a', {})).toHaveLength(1)
    expect(renderTargets([panel('a', 'Fig1.pdf', 0)], null, { 'Fig1.pdf': true })).toHaveLength(1)
  })

  it('没有脚本的面板永远不进队列', () => {
    const raster = { ...panel('a', 'photo.png', 3), script: undefined } as PanelObject
    expect(renderTargets([raster], 'a', {})).toEqual([])
  })
})

describe('两个同文件不同 overrides 的面板不再互顶（React #185 回归）', () => {
  it('各自排期、各自渲染，第二轮同步是不动点', () => {
    const a = panel('a', 'Fig1.pdf', 2)
    const b = panel('b', 'Fig1.pdf', 1)
    const objects: CanvasObject[] = [a, b]

    syncEngine(objects, null)

    // 两个都发出去了，各带自己的 patches
    expect(calls.map((c) => c.patches)).toEqual([a.overrides, b.overrides])

    // 关键：wantPatches 各写各的键，谁也没顶掉谁
    const { byKey } = useRenderStore.getState()
    expect(renderKeyOf(a)).not.toBe(renderKeyOf(b))
    expect(byKey[renderKeyOf(a)].wantPatches).toBe(JSON.stringify(a.overrides))
    expect(byKey[renderKeyOf(b)].wantPatches).toBe(JSON.stringify(b.overrides))

    // 再同步一轮：两边都已排期 → 一条都不再发。旧实现里这一轮会因为
    // wantPatches 被对方顶掉而重新发出，effect ↔ store 无限互相触发
    // （React #185「Maximum update depth exceeded」，整个界面白掉）
    syncEngine(objects, null)
    expect(calls).toHaveLength(2)
  })
})

describe('prune：没人引用的变体不留在内存里', () => {
  it('保留在用的与该文件最近成功的那份，其余清掉', () => {
    const store = useRenderStore.getState()
    const live = panel('a', 'Fig1.pdf', 1)
    const liveKey = renderKeyOf(live)
    const oldKey = renderKeyOf(panel('a', 'Fig1.pdf', 3))
    const goneKey = renderKeyOf(panel('a', 'Fig1.pdf', 5))
    for (const k of [liveKey, oldKey, goneKey]) {
      store.patch(k, { fileId: 'Fig1.pdf', status: 'ready' })
    }
    useRenderStore.setState({ latest: { 'Fig1.pdf': oldKey } })

    syncEngine([live], null)

    const keys = Object.keys(useRenderStore.getState().byKey)
    expect(keys).toContain(liveKey)   // 文档里还在用
    expect(keys).toContain(oldKey)    // 该文件最近画好的那份（新变体的退路）
    expect(keys).not.toContain(goneKey)
  })
})

describe('markStale：命中该文件的全部变体', () => {
  it('每个变体都置过期、清掉排期，文件级 tracked 也置位', () => {
    const store = useRenderStore.getState()
    const k1 = renderKeyOf(panel('a', 'Fig1.pdf', 1))
    const k2 = renderKeyOf(panel('b', 'Fig1.pdf', 2))
    const other = renderKeyOf(panel('c', 'Fig2.pdf', 1))
    store.patch(k1, { fileId: 'Fig1.pdf', lastPatches: 'x' })
    store.patch(k2, { fileId: 'Fig1.pdf', lastPatches: 'y' })
    store.patch(other, { fileId: 'Fig2.pdf', lastPatches: 'z' })

    store.markStale(['Fig1.pdf', 'Fig3.pdf'])

    const s = useRenderStore.getState()
    for (const k of [k1, k2]) {
      expect(s.byKey[k].stale).toBe(true)
      expect(s.byKey[k].lastPatches).toBeNull()
    }
    expect(s.byKey[other].stale).toBe(false)
    expect(s.byKey[other].lastPatches).toBe('z')
    // 一个变体都还没渲染过的文件也必须被跟踪，否则同步器根本看不到它
    expect(s.tracked['Fig3.pdf']).toBe(true)
  })
})

describe('交互期降质：只给含图像的面板', () => {
  const imageManifest = {
    stem: 'Fig1',
    size_mm: [80, 60],
    elements: [{ gid: 'im_0', role: 'image', label: '', bbox: [0, 0, 1, 1], editable: [], draggable: false }],
  } as unknown as Manifest
  const vectorManifest = {
    stem: 'Fig1',
    size_mm: [80, 60],
    elements: [{ gid: 'line_0', role: 'line', label: '', bbox: [0, 0, 1, 1], editable: [], draggable: false }],
  } as unknown as Manifest

  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const seed = (p: PanelObject, manifest: Manifest) => {
    const key = renderKeyOf(p)
    useRenderStore.getState().patch(key, { fileId: p.fileId, manifest, status: 'ready' })
    useRenderStore.setState({ latest: { [p.fileId]: key } })
  }

  it('含 imshow 的面板：防抖那一路带 preview_dpi，定稿不带', () => {
    const p = panel('a', 'Fig1.pdf', 1)
    seed(p, imageManifest)

    requestRender(p)               // 连续调整中
    vi.runAllTimers()
    expect(calls.at(-1)?.dpi).toBe(100)

    requestRender(p, true)         // 松手 / 颜色开关这类定稿
    expect(calls.at(-1)?.dpi).toBeUndefined()
  })

  it('纯矢量面板一律不降质（实测零收益，只会糊）', () => {
    const p = panel('a', 'Fig1.pdf', 1)
    seed(p, vectorManifest)

    requestRender(p)
    vi.runAllTimers()
    expect(calls.at(-1)?.dpi).toBeUndefined()
  })

  it('同一面板连着改只留最后一次（防抖按面板，不按变体）', () => {
    const p1 = panel('a', 'Fig1.pdf', 1)
    const p2 = panel('a', 'Fig1.pdf', 2)   // 同一个面板，值变了
    seed(p1, vectorManifest)

    requestRender(p1)
    requestRender(p2)
    vi.runAllTimers()
    expect(calls).toHaveLength(1)
    expect(calls[0].patches).toEqual(p2.overrides)
  })
})

describe('flushRender：交互结束后必须回到定稿质量', () => {
  beforeEach(async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_flush')
  })

  it('降质渲染过的变体，冲刷时按默认 dpi 重发一次', async () => {
    const p = panel('a', 'Fig1.pdf', 1)
    useDocumentStore.getState().commit('加面板', (d) => {
      d.objects.push(p)
    })
    useRenderStore.getState().patch(renderKeyOf(p), {
      fileId: p.fileId,
      previewDpi: 100,
      lastPatches: JSON.stringify(p.overrides),
    })

    flushRender('a')
    expect(calls).toHaveLength(1)
    expect(calls[0].dpi).toBeUndefined()
    expect(calls[0].patches).toEqual(p.overrides)
  })

  it('已经是定稿质量就不白跑一次', () => {
    const p = panel('a', 'Fig1.pdf', 1)
    useDocumentStore.getState().commit('加面板', (d) => {
      d.objects.push(p)
    })
    useRenderStore.getState().patch(renderKeyOf(p), {
      fileId: p.fileId,
      previewDpi: null,
      lastPatches: JSON.stringify(p.overrides),
    })

    flushRender('a')
    expect(calls).toHaveLength(0)
  })
})
