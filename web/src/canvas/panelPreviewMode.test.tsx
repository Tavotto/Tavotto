/**
 * 编辑预览的三档表示法（ADR 0022 / issue #181）。
 *
 * 一句话判据：**画法可以换，能编辑的东西一个都不许少。**
 *
 * `raster` 档最容易被做错成「图太大所以不能编辑了」——而 #181 的用户要的
 * 恰恰是编辑这张图。所以这里每一条 raster 用例都同时断言两件事：画布上
 * 挂的是位图，**且命中层还在**。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PanelView } from './PanelView'
import { renderKeyOf, useRenderStore, type PanelRender } from '@/store/renderStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useUiStore } from '@/store/uiStore'
import { VECTOR_PREVIEW, type PreviewMetadata } from '@/lib/previewBudget'
import type { Manifest } from '@/lib/api'
import type { PanelObject } from '@/types/document'

const previewPng = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  enginePreviewPng: (id: string, patches: unknown[], bucket: number) => {
    previewPng(id, patches, bucket)
    return Promise.resolve(new Blob(['png']))
  },
}))

const PANEL: PanelObject = {
  id: 'p1',
  type: 'panel',
  x: 0,
  y: 0,
  w: 100,
  h: 80,
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 100,
  nativeH: 80,
  script: 'fig.py',
  overrides: [{ gid: 'title', prop: 'fontsize', value: 9 }],
} as unknown as PanelObject

/** 命中层要有东西可命中，否则「命中层还在」是句空话 */
const MANIFEST = {
  stem: 'Fig1',
  size_mm: [100, 80],
  elements: [
    { gid: 'figure', role: 'figure', bbox: [0, 0, 1, 1], editable: [] },
    {
      gid: 'axes_0.title',
      role: 'title',
      bbox: [0.3, 0.02, 0.4, 0.08],
      editable: [{ prop: 'fontsize', type: 'number', value: 9 }],
      draggable: true,
      anchor: [0.5, 0.06],
    },
  ],
} as unknown as Manifest

const RASTER: PreviewMetadata = {
  mode: 'raster',
  reason: 'svg_hard_limit',
  svg_bytes: 126_132_735,
  rasterized_artist_count: 0,
}

const HYBRID: PreviewMetadata = {
  mode: 'hybrid',
  reason: 'complexity_budget',
  svg_bytes: 900_000,
  rasterized_artist_count: 3,
}

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  previewPng.mockClear()
  URL.createObjectURL = vi.fn(() => 'blob:mock/1')
  URL.revokeObjectURL = vi.fn()
  useRenderStore.getState().clear()
  // 排序用例靠「只置要测的那一对」把相邻对隔离开，所以每条都得从空开始
  useNativeSessionStore.setState({ sessions: {} })
  useRuntimeAssetStore.setState({ byId: {} })
  useUiStore.setState({ elementPanelId: PANEL.id }) // 编辑态：三档的区别只在这里显现
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

function seed(extra: Partial<PanelRender>) {
  useRenderStore.getState().patch(renderKeyOf(PANEL), {
    fileId: PANEL.fileId,
    manifest: MANIFEST,
    rev: 3,
    status: 'ready',
    lastPatches: JSON.stringify(PANEL.overrides),
    preview: VECTOR_PREVIEW,
    ...extra,
  })
}

async function mount() {
  await act(async () => {
    root.render(<PanelView obj={PANEL} />)
  })
}

const inlineSvg = () => container.querySelector('[data-element-svg]')
const hitLayer = () => container.querySelector('[data-authority="ready"]')

describe('PanelView：三档预览表示法', () => {
  it('vector：编辑态内联 SVG（今天的行为，一字不改）', async () => {
    seed({ svg: '<svg id="v"/>' })
    await mount()

    expect(inlineSvg()?.innerHTML).toContain('id="v"')
    expect(hitLayer()).not.toBeNull()
    expect(container.querySelector('img')).toBeNull()
  })

  it('hybrid：有 SVG 就照旧内联（混合产物仍然是一份 SVG）', async () => {
    seed({ svg: '<svg id="h"/>', preview: HYBRID })
    await mount()

    expect(inlineSvg()?.innerHTML).toContain('id="h"')
    expect(hitLayer()).not.toBeNull()
  })

  it('raster：画布走位图，**命中层照旧在**', async () => {
    seed({ svg: null, preview: RASTER })
    await mount()

    // 一个 dangerouslySetInnerHTML 都没有——这正是不变量 3 要的结果
    expect(inlineSvg()).toBeNull()
    const img = container.querySelector('img')
    expect(img?.getAttribute('src')).toBe('blob:mock/1')
    // 位图按**这个面板自己的 patches** 出（状态中立的既有链路）
    expect(previewPng).toHaveBeenCalledWith('Fig1.pdf', PANEL.overrides, expect.any(Number))

    // 「显示降级 ≠ 关闭语义编辑」：命中层在、且拿得到几何权威
    expect(hitLayer()).not.toBeNull()
    expect(container.querySelector('[data-authority="syncing"]')).toBeNull()
  })

  it('退回窗口里表示法跟着 SVG 走：raster 面板不闪一下矢量图', async () => {
    // 用户刚改完一个值：**自己这一版还没画出来**（新键，没有 manifest），
    // 画布退回该文件最近画好的那份——而那份是 raster。
    //
    // `mergeRender` 里表示法不跟着 SVG 走的话，这里拿到的是自己那份的默认值
    // （vector）：PanelView 于是既不内联 SVG（根本没有）、又不取引擎位图，
    // 退到磁盘原图——用户看到的是**没有任何编辑的那张图**闪一下。
    const other = { ...PANEL, overrides: [] } as unknown as PanelObject
    useRenderStore.getState().patch(renderKeyOf(other), {
      fileId: PANEL.fileId,
      manifest: MANIFEST,
      rev: 3,
      status: 'ready',
      lastPatches: '[]',
      svg: null,
      preview: RASTER,
    })
    useRenderStore.setState((s) => ({ latest: { ...s.latest, [PANEL.fileId]: renderKeyOf(other) } }))
    // 自己那一版只排了队，还没有结果
    useRenderStore.getState().patch(renderKeyOf(PANEL), {
      fileId: PANEL.fileId,
      status: 'rendering',
      wantPatches: JSON.stringify(PANEL.overrides),
    })
    await mount()

    expect(inlineSvg()).toBeNull()
    // 位图照旧在取（画布继续显示上一张 raster 预览），没有退到磁盘原图
    expect(previewPng).toHaveBeenCalled()
  })

  it('raster 面板不许拿同文件另一个变体的矢量 SVG 冒充自己', async () => {
    const other = { ...PANEL, overrides: [] } as unknown as PanelObject
    useRenderStore.getState().patch(renderKeyOf(other), {
      fileId: PANEL.fileId,
      manifest: MANIFEST,
      rev: 1,
      status: 'ready',
      lastPatches: '[]',
      svg: '<svg id="stale"/>',
      preview: VECTOR_PREVIEW,
    })
    useRenderStore.setState((s) => ({ latest: { ...s.latest, [PANEL.fileId]: renderKeyOf(other) } }))
    seed({ svg: null, preview: RASTER })
    await mount()

    expect(container.innerHTML).not.toContain('id="stale"')
    expect(inlineSvg()).toBeNull()
  })

  it('raster：画布说得出「挂的是这一版自己的图」', async () => {
    seed({ svg: null, preview: RASTER })
    await mount()

    // fallback = 挂着**别人**的图（几何交互停摆）；raster = 挂着自己的，只是画法不同。
    // 诊断与 e2e 都读这个属性，报错就是报错。
    expect(container.querySelector('[data-display]')?.getAttribute('data-display')).toBe('raster')
  })

  it('raster：角标解释一次，不弹对话框、不责怪用户', async () => {
    seed({ svg: null, preview: RASTER })
    await mount()

    const hint = container.querySelector('span[title]') as HTMLElement | null
    expect(hint?.title).toContain('导出质量')
    expect(hint?.title).not.toContain('太大') // 不责怪用户
    expect(hint?.parentElement?.textContent).toContain('低内存编辑预览')
  })

  it('raster 角标不许吃掉画布的指针事件', async () => {
    // 角标画在面板左上角，而 raster 这一档**整个编辑期间常驻**——图内标题
    // 常常就在那儿。既有角标全是 pointer-events-none；带 tooltip 的那一档
    // 只有 ⓘ 那一小块把指针事件收回来，角标本体不许收。
    // （真浏览器实测撞见过：整枚角标接指针事件时，标题点不中。）
    seed({ svg: null, preview: RASTER })
    await mount()

    const hint = container.querySelector('span[title]') as HTMLElement
    const badge = hint.parentElement as HTMLElement
    expect(badge.className).not.toContain('pointer-events-auto')
    expect(hint.className).toContain('pointer-events-auto')
    // 最外层那一格照旧完全透明
    const wrap = badge.parentElement as HTMLElement
    expect(wrap.className).toContain('pointer-events-none')
  })

  it('老后端不返回 preview：行为与从前逐字节相同', async () => {
    // `EMPTY.preview` 就是 VECTOR_PREVIEW——这条钉的是「加字段协议没有把
    // 旧路径改掉」，而不是某个新分支好用
    seed({ svg: '<svg id="legacy"/>' })
    await mount()

    expect(inlineSvg()?.innerHTML).toContain('id="legacy"')
  })
})

/* -------------------------------------------------------------------------- */
/*  角标优先级：相邻两档同时成立时谁说了算                                       */
/* -------------------------------------------------------------------------- */

/**
 * 最终链条（#192 合入后）：
 *
 *     error → nativeState → stale → rasterEditing → runtimeBadge
 *
 * **排序缺陷唯一藏得住的地方是「相邻」。** 所以每条夹具只置要测的那一对，
 * 链上位于两者之间的每一档都刻意留空——置了中间那档，测的就变成「A vs 中间
 * 那档」，而对调 A/B 的顺序根本不改结果（#192 那边的第一版排序用例正是这么
 * 变异完还绿的）。
 *
 * 变异纪律：每条**单独**变异，只对调那一对。一次变异把三条全打红 = 夹具没
 * 隔离开，红的不是要测的那件事。
 */
describe('角标优先级：相邻两档同时成立时谁说了算', () => {
  const badgeText = () =>
    [...container.querySelectorAll('span')].map((el) => el.textContent).find((t) => t) ?? ''

  /** 一条活着的 native 会话，descriptors 指着这个面板 */
  const liveSession = (editable: boolean) => ({
    sessions: {
      s1: {
        session_id: 's1',
        state: 'running',
        editable,
        descriptors: [{ asset_id: PANEL.fileId }],
      } as never,
    },
  })

  it("'running' + raster → 说 native（两档都在，阻塞性的先说）", async () => {
    useNativeSessionStore.setState(liveSession(false))
    seed({ svg: null, preview: RASTER, stale: false }) // ← 刻意不置 stale
    await mount()

    expect(badgeText()).toBe('脚本正在运行，停下来才能编辑')
    expect(container.textContent).not.toContain('低内存编辑预览')
  })

  it("'offline' + raster → 仍说 native（这一格论证最薄，不许抽代表）", async () => {
    // `'offline'` 与 `'running'` **同为阻塞性**：`_NATIVE_STATUS` 把
    // NATIVE_SESSION_OFFLINE 与 NATIVE_SESSION_NOT_AT_BARRIER 都映射成 409，
    // `enginesession.resolve()` 在 profile=native、无活会话时也直接抛。
    // 解锁动作不同（等屏障 vs 重跑原命令），但点进图内编辑都失败。
    const runtimePanel = { ...PANEL, fileKind: 'runtime' } as unknown as PanelObject
    // **`checked: true` 不是装饰**：少了它，PanelView 挂载后的 `ensure()`
    // effect 会去查后端、落回默认的 `profile: 'safe'`，把夹具冲掉——用例照样
    // 红，但红的原因不是被测的那件事（实测撞见过，`byId` 里 profile 变成了
    // safe）。用的是产品代码自己的短路条件（`byId[id]?.checked` → 直接返回）。
    useRuntimeAssetStore.setState({
      byId: {
        [PANEL.fileId]: {
          profile: 'native',
          checked: true,
          registered: true,
          cached: true,
          status: 'fresh',
        } as never,
      },
    })
    useRenderStore.getState().patch(renderKeyOf(runtimePanel), {
      fileId: PANEL.fileId,
      manifest: MANIFEST,
      rev: 3,
      status: 'ready',
      lastPatches: JSON.stringify(runtimePanel.overrides),
      svg: null,
      preview: RASTER,
      stale: false, // ← 刻意不置 stale
    })
    await act(async () => {
      root.render(<PanelView obj={runtimePanel} />)
    })

    expect(badgeText()).toBe('会话已结束，重新运行原命令可继续编辑')
    expect(container.textContent).not.toContain('低内存编辑预览')
  })

  it('stale + raster → 说 stale（native 挪走之后 raster 的新上游邻居）', async () => {
    // #192 把 nativeState 从 stale 后面挪到了前面，raster 的上游邻居于是从
    // native 变成了 stale。**原本「两格各钉一条」的设计一条都碰不到这个新对。**
    seed({ svg: null, preview: RASTER, stale: true }) // ← 刻意不置 native
    await mount()

    expect(badgeText()).toBe('脚本已更新')
    expect(container.textContent).not.toContain('低内存编辑预览')
  })
})
