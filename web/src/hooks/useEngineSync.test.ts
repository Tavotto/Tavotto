import { literal } from '@/i18n'
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

describe('runtime 面板的 lazy rehydrate 门（ADR 0013）', () => {
  const runtimePanel = (id: string, overrides = 1): PanelObject =>
    ({
      ...panel(id, 'runtime:fig.py#fig', overrides),
      fileKind: 'runtime',
    }) as PanelObject

  it('重开文档：带 overrides 的 runtime 面板**不**自动执行脚本（负向反证 #4 的前端面）', () => {
    // 文件面板带未写回 overrides 会自动重建；runtime 面板绝不能——
    // 「打开文档」不是执行脚本的授权（总纲原则 5）
    expect(renderTargets([runtimePanel('a', 3)], null, {}, {})).toEqual([])
  })

  it('脚本变更（tracked）也不构成 runtime 自动重跑的理由', () => {
    expect(
      renderTargets([runtimePanel('a', 1)], null, { 'runtime:fig.py#fig': true }, {}),
    ).toEqual([])
  })

  it('进入图内编辑即入队（lazy build 的触发点）', () => {
    expect(renderTargets([runtimePanel('a', 0)], 'a', {}, {})).toHaveLength(1)
  })

  it('本会话已经跑过（latest 有它）之后与文件面板同一待遇', () => {
    const latest = { 'runtime:fig.py#fig': 'runtime:fig.py#fig []' }
    expect(renderTargets([runtimePanel('a', 2)], null, {}, latest)).toHaveLength(1)
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

describe('SVG payload 被内存预算清掉的那一版：重新排一次渲染', () => {
  // 重画走的是**防抖**那一路（用户可能按着撤销不放，一档一档往回退），
  // 所以要把计时器推过去才看得到那次请求
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('画出来过、几何权威也在，但没有矢量图 → 不许跳过（ADR 0022 §8）', () => {
    const p = panel('a', 'Fig1.pdf', 1)
    const want = JSON.stringify(p.overrides)
    // 这一版确实画成功过：lastPatches 对得上、manifest 在，只是 svg 被
    // `SVG_RECENT_BUDGET_*` 收走了（撤销回到它的那一刻正是这个状态）
    useRenderStore.getState().patch(renderKeyOf(p), {
      fileId: 'Fig1.pdf',
      manifest: { stem: 'Fig1', elements: [] } as unknown as Manifest,
      status: 'ready',
      lastPatches: want,
      wantPatches: want,
      svg: null,
      svgBytes: 0,
      svgEvicted: true,
    })

    syncEngine([p], 'a')
    vi.advanceTimersByTime(400)

    // 不重画的话 Codex 内嵌画布里连一条取像素的路都没有（previewPngUrl 只对
    // raster 档有缓存位图），画面会直接空掉
    expect(calls).toHaveLength(1)
    expect(calls[0].patches).toEqual(p.overrides)
  })

  it('payload 还在的那一版照旧跳过（不因为多了个字段就白跑引擎）', () => {
    const p = panel('a', 'Fig1.pdf', 1)
    const want = JSON.stringify(p.overrides)
    useRenderStore.getState().patch(renderKeyOf(p), {
      fileId: 'Fig1.pdf',
      manifest: { stem: 'Fig1', elements: [] } as unknown as Manifest,
      status: 'ready',
      lastPatches: want,
      wantPatches: want,
      svg: '<svg/>',
      svgBytes: 6,
      svgEvicted: false,
    })

    syncEngine([p], 'a')
    vi.advanceTimersByTime(400)

    expect(calls).toHaveLength(0)
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
    useDocumentStore.getState().commit(literal('加面板'), (d) => {
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
    useDocumentStore.getState().commit(literal('加面板'), (d) => {
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

/* ========================================================================== */
/*  渲染策略：假实时手势期间「一次都不发」                                     */
/* ========================================================================== */

describe("render:'none'：手势期间不麻烦 matplotlib，收尾时定稿一次", () => {
  beforeEach(async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_policy')
  })

  const putPanel = (p: PanelObject) => {
    useDocumentStore.getState().commit(literal('加面板'), (d) => {
      d.objects.push(p)
    })
  }

  it('none 不发请求，也不排防抖计时器', () => {
    vi.useFakeTimers()
    const p = panel('a', 'Fig1.pdf', 1)
    requestRender(p, 'none')
    vi.runAllTimers()
    expect(calls).toHaveLength(0)
    vi.useRealTimers()
  })

  it('none 仍然要占住 wantPatches——不占位的话同步器会立刻替它发一次', () => {
    const p = panel('a', 'Fig1.pdf', 1)
    putPanel(p)
    requestRender(p, 'none')
    expect(useRenderStore.getState().get(renderKeyOf(p)).wantPatches).toBe(
      JSON.stringify(p.overrides),
    )
    // 这才是真正的看护点：同步 effect 每次文档变化都会跑一遍
    syncEngine(useDocumentStore.getState().doc.objects, 'a')
    expect(calls).toHaveLength(0)
  })

  it('手势结束 flushRender：把这一版发出去（此前没有任何计时器挂着）', () => {
    const p = panel('a', 'Fig1.pdf', 1)
    putPanel(p)
    requestRender(p, 'none')
    expect(calls).toHaveLength(0)

    flushRender('a')
    expect(calls).toHaveLength(1)
    expect(calls[0].patches).toEqual(p.overrides)
    expect(calls[0].dpi).toBeUndefined() // 定稿永远默认 dpi
  })

  it('连着改十次只留最后一版，收尾发一次', () => {
    let p = panel('a', 'Fig1.pdf', 1)
    putPanel(p)
    for (let i = 1; i <= 10; i++) {
      p = panel('a', 'Fig1.pdf', i)
      useDocumentStore.getState().commit(literal('改一个值'), (d) => {
        const o = d.objects.find((x) => x.id === 'a')
        if (o?.type === 'panel') o.overrides = p.overrides
      })
      requestRender(p, 'none')
      syncEngine(useDocumentStore.getState().doc.objects, 'a')
    }
    expect(calls).toHaveLength(0)
    flushRender('a')
    expect(calls).toHaveLength(1)
    expect(calls[0].patches).toHaveLength(10)
  })

  it('布尔参数照旧：true=立刻、false=防抖（老调用方一个字不用改）', () => {
    vi.useFakeTimers()
    const p = panel('a', 'Fig1.pdf', 1)
    requestRender(p, true)
    expect(calls).toHaveLength(1)
    calls.length = 0
    requestRender(panel('b', 'Fig2.pdf', 1), false)
    expect(calls).toHaveLength(0)
    vi.runAllTimers()
    expect(calls).toHaveLength(1)
    vi.useRealTimers()
  })
})
