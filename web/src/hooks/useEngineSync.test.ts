import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { literal } from '@/i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderTargets, syncEngine, useEngineSync } from './useEngineSync'
import { flushRender, requestRender } from '@/store/renderScheduler'
import { seedExactRender } from '@/test/renderFixtures'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import {
  emptyProject,
  type CanvasObject,
  type FigureDocument,
  type PanelObject,
} from '@/types/document'
import type { Manifest, PanelInfo } from '@/lib/api'
import { panelScale } from '@/lib/preflight'
import { startCropDrag, startResizeDrag } from '@/canvas/interactions'
import { startLayoutAutoReflow } from '@/store/actions'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}

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

describe('baked 基线的有效性（isJustBakedBaseline 经由 renderTargets）', () => {
  // panel() 造出来的 overrides 形状与这里的 baked 逐字相同（同一段生成逻辑）
  const bakedOf = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ gid: `g${i}`, prop: 'color', value: '#000' }))
  const seedAsset = (extra: Partial<PanelInfo>) =>
    useAssetStore.setState({
      byId: { 'Fig1.pdf': { id: 'Fig1.pdf', ...extra } as PanelInfo },
    })

  afterEach(() => {
    useAssetStore.setState({ byId: {}, panels: [] })
  })

  it('overrides 恰好等于有效基线：不进队列（文件已是那个样子）', () => {
    seedAsset({ baked_overrides: bakedOf(2), baked_current: true })
    expect(renderTargets([panel('a', 'Fig1.pdf', 2)], null, {})).toEqual([])
  })

  it('老后端没有 baked_current 字段：维持旧行为，不进队列', () => {
    seedAsset({ baked_overrides: bakedOf(2) })
    expect(renderTargets([panel('a', 'Fig1.pdf', 2)], null, {})).toEqual([])
  })

  it('基线已失效（文件被外部改写，baked_current=false）：必须重新走引擎', () => {
    // 用户在 Tavotto 外重跑构建脚本把产物刷回脚本原值：预览若继续按
    // 「文件已是基线的样子」跳过渲染，画布挂的就是脚本原值，而编辑态
    // 显示 script+overrides——正是用户报的「预览没有使用 override」
    seedAsset({ baked_overrides: bakedOf(2), baked_current: false })
    expect(renderTargets([panel('a', 'Fig1.pdf', 2)], null, {})).toHaveLength(1)
  })

  it('overrides 与基线不同：无论基线是否有效都进队列', () => {
    seedAsset({ baked_overrides: bakedOf(2), baked_current: true })
    expect(renderTargets([panel('a', 'Fig1.pdf', 3)], null, {})).toHaveLength(1)
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

describe('渲染回来的图幅同步到面板：快速编辑舞台的框就是它', () => {
  /** 摆一个面板、挂上同步器、喂一次渲染回来的图幅，返回同步后的面板。 */
  async function syncOnce(p: PanelObject, size: [number, number]): Promise<PanelObject> {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true
    await useDocumentStore.getState().switchDocument(emptyProject(), `d_size_${p.id}`)
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.objects = [p]
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    const Probe = () => {
      useEngineSync()
      return null
    }
    await act(async () => {
      root.render(createElement(Probe))
    })
    await act(async () => {
      seedExactRender(p, { stem: 'Fig1', size_mm: size, elements: [] })
    })
    const o = useDocumentStore.getState().doc.objects[0] as PanelObject
    await act(async () => {
      root.unmount()
    })
    container.remove()
    return o
  }

  it('用户缩到 50% 的面板：换了原生图幅之后仍是 50%，宽高各自按比例', async () => {
    const p = {
      ...panel('p50', 'Fig1.pdf', 0),
      w: 37.63,
      h: 29.34,
      nativeW: 75.26,
      nativeH: 58.68,
      script: null,
    } as PanelObject
    const o = await syncOnce(p, [80, 57.6])
    expect(panelScale(o)).toBeCloseTo(0.5, 6)
    expect(o.w).toBeCloseTo(40, 6)
    expect(o.h).toBeCloseTo(28.8, 6)
  })

  it('转了 90° 的面板：页面包围盒的宽跟内容的高走，缩放比不变', async () => {
    const p = {
      ...panel('p90', 'Fig1.pdf', 0),
      // 包围盒是转过的：页面宽 = 内容高
      w: 58.68,
      h: 75.26,
      nativeW: 75.26,
      nativeH: 58.68,
      rotation: 90,
      script: null,
    } as PanelObject
    const o = await syncOnce(p, [80, 57.6])
    expect(o.w).toBeCloseTo(57.6, 6)
    expect(o.h).toBeCloseTo(80, 6)
    expect(panelScale(o)).toBeCloseTo(1, 6)
  })

  it('manifest size_mm 与文档不同：nativeW/H 跟着改，页面尺寸按同一比例走、缩放比不变', async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true
    // 文档里是上一次同步到的图幅（磁盘 PDF 的页面），渲染回来的是脚本 figsize
    const p = {
      ...panel('p1', 'Fig1.pdf', 0),
      w: 75.26,
      h: 58.68,
      nativeW: 75.26,
      nativeH: 58.68,
      script: null,
    } as PanelObject
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_size_sync')
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.objects = [p]
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    const Probe = () => {
      useEngineSync()
      return null
    }
    await act(async () => {
      root.render(createElement(Probe))
    })
    await act(async () => {
      seedExactRender(p, { stem: 'Fig1', size_mm: [80, 57.6], elements: [] })
    })
    const o = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect([o.nativeW, o.nativeH]).toEqual([80, 57.6])
    // 放进来是 100%，换成脚本 figsize 之后仍是 100%：宽不许停在磁盘 PDF 的
    // 75.26（那样缩放比静默变成 0.94，预检把合规的字号报成偏小）
    expect(o.w).toBeCloseTo(80, 6)
    expect(o.h).toBeCloseTo(57.6, 6)
    expect(panelScale(o)).toBeCloseTo(1, 6)
    await act(async () => {
      root.unmount()
    })
    container.remove()
  })

  describe('几何事务进行中收到改了图幅的渲染：同步并入这个事务', () => {
    /** 摆一个 100% 的面板、挂上同步器、开缩放事务并拖到 75%，返回卸载函数。 */
    async function resizeInFlight(docId: string) {
      globalThis.IS_REACT_ACT_ENVIRONMENT = true
      const p = { ...panel('pt', 'Fig1.pdf', 0), w: 40, h: 30, script: null } as PanelObject
      await useDocumentStore.getState().switchDocument(emptyProject(), docId)
      useDocumentStore.getState().commit(literal('准备'), (d) => {
        d.objects = [p]
      })
      const container = document.createElement('div')
      document.body.appendChild(container)
      const root = createRoot(container)
      const Probe = () => {
        useEngineSync()
        return null
      }
      await act(async () => {
        root.render(createElement(Probe))
      })
      await act(async () => {
        useDocumentStore.getState().beginTxn(literal('缩放'))
        useDocumentStore.getState().txnUpdate((d) => {
          const o = d.objects[0] as PanelObject
          o.w = 30
          o.h = 22.5
        })
      })
      // 手势还没松开，改了图幅的渲染先回来了（40×30 → 50×30）
      await act(async () => {
        seedExactRender(p, { stem: 'Fig1', size_mm: [50, 30], elements: [] })
      })
      return async () => {
        await act(async () => {
          root.unmount()
        })
        container.remove()
      }
    }
    const current = () => useDocumentStore.getState().doc.objects[0] as PanelObject
    const dims = (o: PanelObject) => [o.w, o.h, o.nativeW, o.nativeH]

    it('松手 → 撤销 → 重做：重做回到松手那一刻，缩放比不变', async () => {
      const unmount = await resizeInFlight('d_size_txn_redo')
      await act(async () => {
        useDocumentStore.getState().endTxn()
      })
      const done = current()
      expect(dims(done)).toEqual([37.5, 22.5, 50, 30])
      expect(panelScale(done)).toBeCloseTo(0.75, 6)
      await act(async () => {
        useDocumentStore.getState().undo()
      })
      // 撤到缩放之前：原生图幅与页面尺寸一起回去，缩放比仍自洽（100%）
      expect(panelScale(current())).toBeCloseTo(1, 6)
      await act(async () => {
        useDocumentStore.getState().redo()
      })
      // 以前同步走 silent、不进事务：重做只重放 w=30，nativeW 却停在 50，
      // 缩放比从 0.75 变成 0.6
      expect(dims(current())).toEqual(dims(done))
      expect(panelScale(current())).toBeCloseTo(0.75, 6)
      await unmount()
    })

    it('手势取消（丢弃事务）：回到事务之前，缩放比不变', async () => {
      const unmount = await resizeInFlight('d_size_txn_discard')
      await act(async () => {
        useDocumentStore.getState().endTxn({ discard: true })
      })
      // 回滚只还原事务记下的东西：以前 w 回到 40、nativeW 停在 50 → 0.8
      expect(panelScale(current())).toBeCloseTo(1, 6)
      expect([current().nativeW, current().nativeH]).toEqual([50, 30])
      await unmount()
    })
  })

  describe('渲染到达之后又有拖动帧：手势按按下时的几何写尺寸，同步留到收尾', () => {
    /** 屏幕像素指针桩（zoom=1、pan=0：1 mm = mmToWorld(1) px） */
    const down = () =>
      ({ clientX: 0, clientY: 0, button: 0, stopPropagation() {} }) as unknown as React.PointerEvent
    const fire = (type: 'pointermove' | 'pointerup', [dx, dy]: readonly [number, number]) =>
      window.dispatchEvent(
        new MouseEvent(type, { clientX: mmToWorld(dx), clientY: mmToWorld(dy), bubbles: true }),
      )
    const current = () => useDocumentStore.getState().doc.objects[0] as PanelObject
    const dims = (o: PanelObject) => [o.w, o.h, o.nativeW, o.nativeH]
    const box = (o: PanelObject) => [o.x, o.y, o.w, o.h]
    /** 横纵两个方向各自的缩放比（未旋转）：页面尺寸 ÷ 取景比例 ÷ 原生图幅 */
    const scales = (o: PanelObject) => {
      const c = o.crop ?? { x: 0, y: 0, w: 1, h: 1 }
      return [o.w / c.w / o.nativeW, o.h / c.h / o.nativeH]
    }

    /**
     * (0,0) 处 40×30 的 100% 面板：拖一帧到 `first` → 渲染回来改了图幅（默认 50×30）→
     * 再拖一帧到 `second` → 松手。默认是 e 手柄左移 10 再到左移 8。返回卸载函数。
     */
    async function dragAcrossRender(
      docId: string,
      start: () => void,
      first: readonly [number, number] = [-10, 0],
      second: readonly [number, number] = [-8, 0],
      size: [number, number] = [50, 30],
      /** 摆面板之后在同一次提交里再摆点别的（布局组、相邻成员） */
      setup?: (d: FigureDocument) => void,
    ) {
      globalThis.IS_REACT_ACT_ENVIRONMENT = true
      useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
      useUiStore.setState({ snapEnabled: false })
      const p = { ...panel('pg', 'Fig1.pdf', 0), w: 40, h: 30, script: null } as PanelObject
      await useDocumentStore.getState().switchDocument(emptyProject(), docId)
      useDocumentStore.getState().commit(literal('准备'), (d) => {
        d.objects = [p]
        setup?.(d)
      })
      const container = document.createElement('div')
      document.body.appendChild(container)
      const root = createRoot(container)
      const Probe = () => {
        useEngineSync()
        return null
      }
      await act(async () => {
        root.render(createElement(Probe))
      })
      await act(async () => {
        start()
        fire('pointermove', first)
      })
      await act(async () => {
        seedExactRender(p, { stem: 'Fig1', size_mm: size, elements: [] })
      })
      // 渲染回来之后用户还在拖：这一帧按按下时抓的 40×30 写绝对尺寸
      await act(async () => {
        fire('pointermove', second)
      })
      await act(async () => {
        fire('pointerup', second)
      })
      return async () => {
        await act(async () => {
          root.unmount()
        })
        container.remove()
      }
    }

    async function undoRedoKeepsEnd() {
      const done = current()
      await act(async () => {
        useDocumentStore.getState().undo()
      })
      // 撤到手势之前：旧图幅上的 100%，同步器随即补到新图幅，仍是 100%
      expect(scales(current())).toEqual([1, 1])
      await act(async () => {
        useDocumentStore.getState().redo()
      })
      expect(dims(current())).toEqual(dims(done))
      expect(box(current())).toEqual(box(done))
      expect(current().crop).toEqual(done.crop)
    }

    it('缩放：松手后横纵缩放比一致（拖到的 80% × 100%），撤销 / 重做回到松手那一刻', async () => {
      const unmount = await dragAcrossRender('d_size_drag_resize', () =>
        startResizeDrag(down(), 'pg', 'e'),
      )
      // 以前：渲染一到就并入事务（37.5 宽），下一帧按旧几何写回 32，nativeW 已是 50
      // → 横向 0.64、纵向 1，而且同步从此跳过
      const o = current()
      expect(o.nativeW).toBe(50)
      expect(scales(o)[0]).toBeCloseTo(0.8, 6)
      expect(scales(o)[1]).toBeCloseTo(1, 6)
      expect(useDocumentStore.getState().past.length).toBe(2)
      await undoRedoKeepsEnd()
      await unmount()
    })

    it('按下没拖就松手（空事务，文档引用不变）：推迟的同步照样补上', async () => {
      const unmount = await dragAcrossRender('d_size_drag_noop', () => {
        useDocumentStore.getState().beginTxn(literal('缩放'))
      })
      // 桩里没有指针监听，两次 pointermove 都落空；事务是空的，结束时不换 doc
      await act(async () => {
        useDocumentStore.getState().endTxn()
      })
      expect(dims(current())).toEqual([50, 30, 50, 30])
      await unmount()
    })

    it('缩放拖西边：对边（东边 x=40）在松手时不动，撤销 / 重做回到松手那一刻', async () => {
      const unmount = await dragAcrossRender(
        'd_size_drag_west',
        () => startResizeDrag(down(), 'pg', 'w'),
        [10, 0],
        [8, 0],
      )
      // 以前按左上角伸缩：x=8、w=40，东边跑到 48
      const o = current()
      expect(o.x + o.w).toBeCloseTo(40, 6)
      expect(o.y).toBeCloseTo(0, 6)
      expect(scales(o)[0]).toBeCloseTo(0.8, 6)
      expect(scales(o)[1]).toBeCloseTo(1, 6)
      await undoRedoKeepsEnd()
      expect(current().x + current().w).toBeCloseTo(40, 6)
      await unmount()
    })

    it('缩放拖北边：对边（南边 y=30）在松手时不动，撤销 / 重做回到松手那一刻', async () => {
      const unmount = await dragAcrossRender(
        'd_size_drag_north',
        () => startResizeDrag(down(), 'pg', 'n'),
        [0, 10],
        [0, 8],
        [40, 40],
      )
      const o = current()
      expect(o.y + o.h).toBeCloseTo(30, 6)
      expect(o.x).toBeCloseTo(0, 6)
      expect(scales(o)[0]).toBeCloseTo(1, 6)
      expect(scales(o)[1]).toBeCloseTo(22 / 30, 6)
      await undoRedoKeepsEnd()
      expect(current().y + current().h).toBeCloseTo(30, 6)
      await unmount()
    })

    it('缩放拖西边、两轴图幅同时变（40×30 → 50×40）：东手柄的中点 (40, 15) 不动', async () => {
      const unmount = await dragAcrossRender(
        'd_size_drag_west_2d',
        () => startResizeDrag(down(), 'pg', 'w'),
        [10, 0],
        [8, 0],
        [50, 40],
      )
      // 以前锚点落在东边的上角 (40, 0)：东手柄中心从 y=15 挪到 y=20
      const eastMid = (q: PanelObject) => [q.x + q.w, q.y + q.h / 2]
      expect(eastMid(current())[0]).toBeCloseTo(40, 6)
      expect(eastMid(current())[1]).toBeCloseTo(15, 6)
      expect(scales(current())[0]).toBeCloseTo(0.8, 6)
      expect(scales(current())[1]).toBeCloseTo(1, 6)
      await undoRedoKeepsEnd()
      expect(eastMid(current())[1]).toBeCloseTo(15, 6)
      await unmount()
    })

    it('缩放拖北边、两轴图幅同时变（40×30 → 50×40）：南手柄的中点 (20, 30) 不动', async () => {
      const unmount = await dragAcrossRender(
        'd_size_drag_north_2d',
        () => startResizeDrag(down(), 'pg', 'n'),
        [0, 10],
        [0, 8],
        [50, 40],
      )
      const southMid = (q: PanelObject) => [q.x + q.w / 2, q.y + q.h]
      expect(southMid(current())[0]).toBeCloseTo(20, 6)
      expect(southMid(current())[1]).toBeCloseTo(30, 6)
      expect(scales(current())[0]).toBeCloseTo(1, 6)
      expect(scales(current())[1]).toBeCloseTo(22 / 30, 6)
      await undoRedoKeepsEnd()
      expect(southMid(current())[0]).toBeCloseTo(20, 6)
      await unmount()
    })

    it('布局组（行）里的面板：手势中渲染回来变宽 → 松手 → 相邻成员按间距被推开，撤销 / 重做', async () => {
      let stop = () => {}
      /** 自动重排有 120 ms 防抖 */
      const settle = () =>
        act(async () => {
          await new Promise((r) => setTimeout(r, 200))
        })
      // 行布局：pg (0,0) 40×30，nb (45,0) 20×30，间距 5
      const unmount = await dragAcrossRender(
        'd_size_drag_layout',
        () => {
          // 摆好布局组之后才挂自动重排：摆场景那次提交本身不该排出一次重排
          stop = startLayoutAutoReflow()
          startResizeDrag(down(), 'pg', 's')
        },
        [0, -6],
        [0, -4],
        [50, 30],
        (d) => {
          d.objects[0].groupId = 'row1'
          d.objects.push({
            ...panel('nb', 'Fig2.pdf', 0),
            x: 45,
            w: 20,
            h: 30,
            nativeW: 20,
            nativeH: 30,
            script: null,
            groupId: 'row1',
          } as PanelObject)
          d.layoutGroups = [{ id: 'row1', kind: 'row', order: ['pg', 'nb'], gap: 5, align: 'start' }]
        },
      )
      await settle()
      const nb = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'nb') as PanelObject
      const gapAfter = () => nb().x - (current().x + current().w)
      // 松手时宽 40 → 50（锚点是南边中点，x 到 -5）；nb 被推到右边 5 mm 处
      expect(current().w).toBeCloseTo(50, 6)
      // 以前：收尾修正与「事务结束」分两次落地，自动重排两次都跳过 → nb 停在 45，压在面板上
      expect(gapAfter()).toBeCloseTo(5, 6)
      const done = [box(current()), box(nb())]
      await act(async () => {
        useDocumentStore.getState().undo()
      })
      await settle()
      // 撤掉自动重排：面板仍是松手后的样子
      expect(box(current())).toEqual(done[0])
      await act(async () => {
        useDocumentStore.getState().redo()
      })
      await settle()
      expect([box(current()), box(nb())]).toEqual(done)
      // 连续撤销两次：第二次回到手势前的旧图幅，图幅同步随即 silent 补回新图幅。
      // 这次不进历史的尺寸写入不许排出一条「自动重排」把 future 清空
      for (let i = 0; i < 2; i++) {
        await act(async () => {
          useDocumentStore.getState().undo()
        })
        await settle()
      }
      expect(useDocumentStore.getState().future).toHaveLength(2)
      for (let i = 0; i < 2; i++) {
        await act(async () => {
          useDocumentStore.getState().redo()
        })
        await settle()
      }
      expect([box(current()), box(nb())]).toEqual(done)
      expect(dims(current())).toEqual([50, 26, 50, 30])
      stop()
      await unmount()
    })

    it('反向：用户在布局组里正常缩放面板（没有图幅变化），引起的重排仍是一条可撤销的历史', async () => {
      globalThis.IS_REACT_ACT_ENVIRONMENT = true
      useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, viewW: 900, viewH: 700 })
      useUiStore.setState({ snapEnabled: false })
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_size_layout_user')
      useDocumentStore.getState().commit(literal('准备'), (d) => {
        d.objects = [
          { ...panel('pg', 'Fig1.pdf', 0), w: 40, h: 30, script: null, groupId: 'row1' } as PanelObject,
          {
            ...panel('nb', 'Fig2.pdf', 0),
            x: 45,
            w: 20,
            h: 30,
            nativeW: 20,
            nativeH: 30,
            script: null,
            groupId: 'row1',
          } as PanelObject,
        ]
        d.layoutGroups = [{ id: 'row1', kind: 'row', order: ['pg', 'nb'], gap: 5, align: 'start' }]
      })
      const stop = startLayoutAutoReflow()
      const nb = () => useDocumentStore.getState().doc.objects.find((o) => o.id === 'nb') as PanelObject
      await act(async () => {
        startResizeDrag(down(), 'pg', 'e')
        fire('pointermove', [10, 0])
        fire('pointerup', [10, 0])
        await new Promise((r) => setTimeout(r, 200))
      })
      const labels = () => useDocumentStore.getState().past.map((e) => e.label.key)
      expect(current().w).toBeCloseTo(50, 6)
      expect(nb().x).toBeCloseTo(55, 6)
      expect(labels().slice(-2)).toEqual(['history.resizeObjects', 'history.autoReflow'])
      // 撤销一次只撤掉重排：nb 回到 45，面板仍是 50 宽
      await act(async () => {
        useDocumentStore.getState().undo()
      })
      expect(nb().x).toBeCloseTo(45, 6)
      expect(current().w).toBeCloseTo(50, 6)
      stop()
    })

    it('裁剪：松手后仍是 100%，撤销 / 重做回到松手那一刻', async () => {
      const unmount = await dragAcrossRender('d_size_drag_crop', () =>
        startCropDrag(down(), 'pg', 'e'),
      )
      // 以前：整图宽 fullW 在按下时按 40 抓死，下一帧写 w = 40 × 0.8 = 32，
      // 而 nativeW 已是 50 → 横向 0.8、纵向 1
      const o = current()
      expect(o.crop?.w).toBeCloseTo(0.8, 6)
      expect(o.nativeW).toBe(50)
      expect(scales(o)[0]).toBeCloseTo(1, 6)
      expect(scales(o)[1]).toBeCloseTo(1, 6)
      // 整图锚点不动：未裁剪整图的中心仍在按下时的 (20, 15)——以前按左上角伸缩，
      // 整图中心跟着漂到 (25, 15)
      const fullCenter = (q: PanelObject) => {
        const c = q.crop ?? { x: 0, y: 0, w: 1, h: 1 }
        const [fw, fh] = [q.w / c.w, q.h / c.h]
        return [q.x - c.x * fw + fw / 2, q.y - c.y * fh + fh / 2]
      }
      expect(fullCenter(o)[0]).toBeCloseTo(20, 6)
      expect(fullCenter(o)[1]).toBeCloseTo(15, 6)
      await undoRedoKeepsEnd()
      expect(fullCenter(current())[0]).toBeCloseTo(20, 6)
      await unmount()
    })
  })
})

describe('事务之外改了图幅：撤销 / 重做回到用户设定的缩放比', () => {
  /*
   * 撤销栈按 **immer 补丁**记账，不是整份快照：一次缩放 commit 记下的是
   * `replace objects[i].w/h`（绝对值），里面没有 nativeW/H——缩放没改它们。
   * 事务之外的图幅同步走 `silent`：nativeW/H 与 w/h 一起改了，**历史里什么都没留**
   * （不加条目，也不改已有条目）。于是撤销那次缩放时 w/h 回到按**旧图幅**量的值，
   * nativeW 却停在新图幅上；同一个 manifest、同一个变体键，同步器看 nativeW 已经
   * 等于 size_mm，不会再补——缩放比就一直错着。
   */
  let unmount: (() => Promise<void>) | null = null
  afterEach(async () => {
    await unmount?.()
    unmount = null
  })

  /** 摆一个 100% 的面板（40×30 mm、原生 40×30）、挂上同步器。 */
  async function mount(docId: string, extra: Partial<PanelObject> = {}): Promise<PanelObject> {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true
    const p = { ...panel('pu', 'Fig1.pdf', 0), script: null, ...extra } as PanelObject
    await useDocumentStore.getState().switchDocument(emptyProject(), docId)
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.objects = [p]
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    const Probe = () => {
      useEngineSync()
      return null
    }
    await act(async () => {
      root.render(createElement(Probe))
    })
    unmount = async () => {
      await act(async () => {
        root.unmount()
      })
      container.remove()
    }
    return p
  }
  const current = () => useDocumentStore.getState().doc.objects[0] as PanelObject
  const nativeOf = (o: PanelObject) => [o.nativeW, o.nativeH]
  /** 用户明确地把面板缩到原生图幅的 `s` 倍（一次 commit，与松手后的缩放同形） */
  const resizeTo = async (s: number) => {
    await act(async () => {
      useDocumentStore.getState().commit(literal('缩放'), (d) => {
        const o = d.objects[0] as PanelObject
        o.w = o.nativeW * s
        o.h = o.nativeH * s
      })
    })
  }
  const undo = async () => {
    await act(async () => {
      useDocumentStore.getState().undo()
    })
  }
  const redo = async () => {
    await act(async () => {
      useDocumentStore.getState().redo()
    })
  }
  /** 事务之外，一次渲染（脚本被改过、同一个变体键）把图幅改成 50×30 */
  const renderNewSize = async (p: PanelObject) => {
    await act(async () => {
      seedExactRender(p, { stem: 'Fig1', size_mm: [50, 30], elements: [] })
    })
  }

  it('缩放 commit → 图幅变化 → 撤销 → 重做：每一步都是用户设定的那个比例', async () => {
    const p = await mount('d_size_undo_basic')
    await resizeTo(0.75)
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    const depth = useDocumentStore.getState().past.length

    await renderNewSize(p)
    // 静默同步：比例不变、原生图幅跟 manifest 走，历史一条都没多
    expect(nativeOf(current())).toEqual([50, 30])
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    expect(useDocumentStore.getState().past.length).toBe(depth)
    expect(useDocumentStore.getState().future.length).toBe(0)

    await undo()
    // 撤销的是「缩到 75%」这件事：回到缩放之前的 100%，原生图幅仍是这个变体的 manifest
    expect(panelScale(current())).toBeCloseTo(1, 6)
    expect(nativeOf(current())).toEqual([50, 30])

    await redo()
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    expect(nativeOf(current())).toEqual([50, 30])
  })

  it('拖手柄缩放（事务，松手落历史）→ 图幅变化 → 撤销 → 重做', async () => {
    const p = await mount('d_size_undo_txn')
    await act(async () => {
      const s = useDocumentStore.getState()
      s.beginTxn(literal('缩放'))
      for (const k of [0.9, 0.8, 0.75]) {
        s.txnUpdate((d) => {
          const o = d.objects[0] as PanelObject
          o.w = 40 * k
          o.h = 30 * k
        })
      }
      useDocumentStore.getState().endTxn()
    })
    await renderNewSize(p)
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    await undo()
    expect(panelScale(current())).toBeCloseTo(1, 6)
    expect(nativeOf(current())).toEqual([50, 30])
    await redo()
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    expect(nativeOf(current())).toEqual([50, 30])
  })

  it('收尾修正把图幅并进了缩放这条历史 → 之后事务外图幅再变 → 撤销 → 重做', async () => {
    // 这条历史两侧的原生图幅不同（40 → 50，收尾修正并进来的），而此刻又是第三个图幅
    const p = await mount('d_size_undo_finalizer')
    await act(async () => {
      useDocumentStore.getState().beginTxn(literal('缩放'))
      useDocumentStore.getState().txnUpdate((d) => {
        const o = d.objects[0] as PanelObject
        o.w = 30
        o.h = 22.5
      })
    })
    // 手势中途渲染回来（40×30 → 50×30）：事务开着不写，留给收尾
    await renderNewSize(p)
    expect(nativeOf(current())).toEqual([40, 30])
    await act(async () => {
      useDocumentStore.getState().endTxn()
    })
    expect(nativeOf(current())).toEqual([50, 30])
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    // 松手之后，事务之外又一次渲染把图幅改成 60×30
    await act(async () => {
      seedExactRender(p, { stem: 'Fig1', size_mm: [60, 30], elements: [] })
    })
    expect(nativeOf(current())).toEqual([60, 30])
    expect(panelScale(current())).toBeCloseTo(0.75, 6)

    await undo()
    expect(panelScale(current())).toBeCloseTo(1, 6)
    expect(nativeOf(current())).toEqual([60, 30])
    await redo()
    // 重做打回的是松手那一刻（按 50 量的 w/h），要换到此刻的 60，而不是按收尾之前的 40
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    expect(nativeOf(current())).toEqual([60, 30])
  })

  it('只改了宽（取消宽高比锁定）→ 两轴图幅都变 → 撤销 / 重做只换算条目打回的那一维', async () => {
    // 条目只记了 w；h 从没离开过当前单位（silent 同步已经把它换到新图幅），
    // 撤销时再乘一遍的话 h 会翻倍两次（Codex #551 P1）
    const p = await mount('d_size_undo_one_dim')
    await act(async () => {
      useDocumentStore.getState().commit(literal('改宽'), (d) => {
        ;(d.objects[0] as PanelObject).w = 20
      })
    })
    await act(async () => {
      seedExactRender(p, { stem: 'Fig1', size_mm: [80, 60], elements: [] })
    })
    expect([current().w, current().h]).toEqual([40, 60])
    await undo()
    expect([current().w, current().h]).toEqual([80, 60])
    expect(nativeOf(current())).toEqual([80, 60])
    await redo()
    expect([current().w, current().h]).toEqual([40, 60])
  })

  it('撤销 / 重做换基的锚点是左上角：打回来的 x/y 原样，重做与撤销前逐位相同', async () => {
    // 与事务外的 silent 同步同一个默认锚点：重做那一侧正是 silent 同步换算过的
    // 状态，锚点不同的话重做会把面板挪走
    const p = await mount('d_size_undo_anchor', { x: 10, y: 5 })
    await act(async () => {
      useDocumentStore.getState().commit(literal('拖西边'), (d) => {
        const o = d.objects[0] as PanelObject
        o.x = 30
        o.w = 20
      })
    })
    await renderNewSize(p)
    const box = (o: PanelObject) => [o.x, o.y, o.w, o.h, o.nativeW, o.nativeH]
    const synced = box(current())
    await undo()
    expect(box(current())).toEqual([10, 5, 50, 30, 50, 30])
    await redo()
    expect(box(current())).toEqual(synced)
  })

  it('正方形面板转 90°（w/h 数值不变）→ 图幅变化 → 撤销回到未转的方向', async () => {
    // 条目里没有 w/h 的补丁，但宽高对应的原生轴互换了（Codex #551 P2）
    const p = await mount('d_size_undo_square_rot', { w: 40, h: 40, nativeW: 40, nativeH: 40 })
    await act(async () => {
      useDocumentStore.getState().commit(literal('旋转'), (d) => {
        ;(d.objects[0] as PanelObject).rotation = 90
      })
    })
    await act(async () => {
      seedExactRender(p, { stem: 'Fig1', size_mm: [80, 40], elements: [] })
    })
    // 转过的包围盒：页面宽 = 内容高
    expect([current().w, current().h]).toEqual([40, 80])
    await undo()
    expect([current().w, current().h]).toEqual([80, 40])
    expect(panelScale(current())).toBeCloseTo(1, 6)
    await redo()
    expect([current().w, current().h]).toEqual([40, 80])
    expect(panelScale(current())).toBeCloseTo(1, 6)
  })

  it('只改了宽的拖动，收尾修正把两轴图幅并进来 → 事务外再变 → 重做连 h 一起换', async () => {
    // 手势本身只动了 w，h 是收尾修正改的：条目打回哪几维要按收尾之后的文档算，
    // 按手势那一刻算会漏掉 h，重做后 h 停在松手时的单位上
    const p = await mount('d_size_undo_finalizer_dims')
    await act(async () => {
      useDocumentStore.getState().beginTxn(literal('改宽'))
      useDocumentStore.getState().txnUpdate((d) => {
        ;(d.objects[0] as PanelObject).w = 20
      })
    })
    await act(async () => {
      seedExactRender(p, { stem: 'Fig1', size_mm: [80, 60], elements: [] })
    })
    await act(async () => {
      useDocumentStore.getState().endTxn()
    })
    expect([current().w, current().h]).toEqual([40, 60])
    await act(async () => {
      seedExactRender(p, { stem: 'Fig1', size_mm: [160, 120], elements: [] })
    })
    expect([current().w, current().h]).toEqual([80, 120])
    await undo()
    expect([current().w, current().h]).toEqual([160, 120])
    await redo()
    expect([current().w, current().h]).toEqual([80, 120])
    expect(nativeOf(current())).toEqual([160, 120])
  })

  it('两次缩放 → 图幅变化 → 连撤两步再连重做两步', async () => {
    const p = await mount('d_size_undo_deep')
    await resizeTo(0.75)
    await resizeTo(0.5)
    await renderNewSize(p)
    expect(panelScale(current())).toBeCloseTo(0.5, 6)

    await undo()
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    expect(nativeOf(current())).toEqual([50, 30])
    await undo()
    expect(panelScale(current())).toBeCloseTo(1, 6)
    expect(nativeOf(current())).toEqual([50, 30])
    await redo()
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    await redo()
    expect(panelScale(current())).toBeCloseTo(0.5, 6)
    expect(nativeOf(current())).toEqual([50, 30])
  })

  it('转了 90° 的面板：撤销后页面包围盒的宽仍跟内容的高走', async () => {
    // 包围盒是转过的：页面宽 = 内容高
    const p = await mount('d_size_undo_rot', { w: 30, h: 40, rotation: 90 })
    await act(async () => {
      useDocumentStore.getState().commit(literal('缩放'), (d) => {
        const o = d.objects[0] as PanelObject
        o.w = 22.5
        o.h = 30
      })
    })
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    await renderNewSize(p)
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    await undo()
    expect(panelScale(current())).toBeCloseTo(1, 6)
    // 内容 50×30 转 90° → 页面 30 宽 × 50 高
    expect([current().w, current().h]).toEqual([30, 50])
    await redo()
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
  })

  it('图幅变化在缩放之前：撤销到更早不受影响（条目记下的就是新图幅）', async () => {
    const p = await mount('d_size_undo_before')
    await renderNewSize(p)
    expect(nativeOf(current())).toEqual([50, 30])
    await resizeTo(0.5)
    await undo()
    expect(panelScale(current())).toBeCloseTo(1, 6)
    expect(nativeOf(current())).toEqual([50, 30])
    await redo()
    expect(panelScale(current())).toBeCloseTo(0.5, 6)
  })

  it('图幅是 override 改的：撤掉那条 override 回到旧变体，再撤到缩放之前', async () => {
    // 这条是对照组：变体键会变回去，旧变体的 manifest 还在，同步器自己就能对齐
    const p = await mount('d_size_undo_override')
    await act(async () => {
      seedExactRender(p, { stem: 'Fig1', size_mm: [40, 30], elements: [] })
    })
    await resizeTo(0.75)
    await act(async () => {
      useDocumentStore.getState().commit(literal('改图幅'), (d) => {
        const o = d.objects[0] as PanelObject
        o.overrides = [{ gid: 'fig', prop: 'size_mm', value: [50, 30] }]
      })
    })
    await act(async () => {
      seedExactRender(current(), { stem: 'Fig1', size_mm: [50, 30], elements: [] })
    })
    expect(nativeOf(current())).toEqual([50, 30])
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    await undo()
    expect(nativeOf(current())).toEqual([40, 30])
    expect(panelScale(current())).toBeCloseTo(0.75, 6)
    await undo()
    expect(panelScale(current())).toBeCloseTo(1, 6)
    expect(nativeOf(current())).toEqual([40, 30])
  })
})
