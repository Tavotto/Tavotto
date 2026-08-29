/**
 * renderStore 的 **SVG payload 字节预算**（issue #181 Session 04 / ADR 0022）。
 *
 * 这套用例守的是一句话：
 *
 * > **条目数不是字节预算。** `RECENT_VARIANTS = 4` 管的是「留几档语义状态」，
 * > 它对「留了多少字节」一无所知——一份 hybrid 之后仍有 8～12 MiB 的 SVG
 * > 乘以 4 档乘以几个文件，就是几百 MB 常驻在 JS 堆里，而每一份都是合法的
 * > 撤销落点，`prune` 一个都不该清。
 *
 * 所以两条策略并存，而**超预算时丢掉的只有 `svg` 字符串**：manifest / rev /
 * lastPatches / wantPatches / timings / preview / status / stale 一个字都不动。
 * 「语义状态 ≠ SVG 源 payload」是本文件每一条用例的同一个主语。
 *
 * 记账口径：后端的 `preview.svg_bytes`（= `stat().st_size`，与硬闸量的是同一个
 * 东西）。下面多数用例用**小字符串 + 声明的 svg_bytes**来精确摆布字节数，
 * 最后一条 `真实巨型 payload` 用真的 12 MiB 字符串跑一遍——两种形状都要有：
 * 只有前者，测的就只是算术；只有后者，摆不出「谁先被丢」的精确局面。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineRenderOptions, Manifest } from '@/lib/api'
import {
  SVG_RECENT_BUDGET_GLOBAL,
  SVG_RECENT_BUDGET_PER_FILE,
  exactPanelManifest,
  panelDisplayView,
  renderKey,
  renderKeyOf,
  residentSvgBytes,
  useRenderStore,
} from './renderStore'
import type { PanelObject } from '@/types/document'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

const MiB = 1024 * 1024

const manifest = (stem: string): Manifest =>
  ({ stem, size_mm: [80, 60], elements: [{ gid: 'axes_0' }] }) as unknown as Manifest

const panel = (id: string, fileId: string, overrides: unknown[] = []): PanelObject =>
  ({
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
    overrides,
  }) as unknown as PanelObject

/** 第 i 版的 overrides（一个变体一条） */
const v = (i: number) => [{ gid: 'g', prop: 'fontsize', value: i }]

/**
 * 后端每次都回一份「声明了 `bytes` 字节」的 SVG。字符串本身很小——记账的
 * 权威是 `preview.svg_bytes`，而它在真后端里就是那份文件的 `stat().st_size`。
 */
function declarePayload(bytes: number) {
  let rev = 0
  engineRender.mockImplementation(async (id: string, patches: unknown[]) => ({
    rev: ++rev,
    manifest: manifest(`${id}#${JSON.stringify(patches)}`),
    warnings: [],
    timings: { canvas_draw_ms: 42 },
    svg: `<svg id="${JSON.stringify(patches)}"/>`,
    preview: {
      mode: 'vector',
      reason: 'normal',
      svg_bytes: bytes,
      rasterized_artist_count: 0,
    },
  }))
}

const store = () => useRenderStore.getState()
const resident = () => residentSvgBytes(useRenderStore.getState())

beforeEach(() => {
  engineRender.mockReset()
  useRenderStore.getState().clear()
})

describe('每文件预算：条目留着，payload 收进预算', () => {
  it('连改五版之后驻留字节回到预算之内，而五条语义状态一条不少', async () => {
    // 一份 12 MiB 的预览 SVG：hybrid 之后仍然可能是这个量级（#181 那张图
    // 收完 mesh 还有 1.8 MB，更大的数据集轻易到两位数 MiB）
    declarePayload(12 * MiB)
    for (let i = 1; i <= 5; i++) await store().render('Fig1.pdf', v(i))

    // 不设预算的话这里是 5 × 12 = 60 MiB
    expect(resident().byFile['Fig1.pdf']).toBeLessThanOrEqual(SVG_RECENT_BUDGET_PER_FILE)

    // **条目一条都没少**——只有 svg 被丢了
    const s = useRenderStore.getState()
    for (let i = 1; i <= 5; i++) {
      const e = s.byKey[renderKey('Fig1.pdf', v(i))]
      expect(e, `第 ${i} 版的条目`).toBeDefined()
      expect(e.manifest).not.toBeNull()
      expect(e.rev).toBeGreaterThan(0)
      expect(e.lastPatches).toBe(JSON.stringify(v(i)))
      expect(e.timings).toEqual({ canvas_draw_ms: 42 })
      expect(e.preview.svg_bytes).toBe(12 * MiB)
      expect(e.status).toBe('ready')
    }
  })

  it('被丢掉的那份：svg 与 svgBytes 成对归零，且打上 svgEvicted', async () => {
    declarePayload(12 * MiB)
    await store().render('Fig1.pdf', v(1))
    await store().render('Fig1.pdf', v(2))

    const first = useRenderStore.getState().byKey[renderKey('Fig1.pdf', v(1))]
    expect(first.svg).toBeNull()
    expect(first.svgBytes).toBe(0) // 拆开这一对，记账就会算出一个谁都对不上的数
    expect(first.svgEvicted).toBe(true)
    // 而新的那一版还在（它是 latest，画布的显示退路）
    const second = useRenderStore.getState().byKey[renderKey('Fig1.pdf', v(2))]
    expect(second.svg).not.toBeNull()
    expect(second.svgEvicted).toBe(false)
  })

  it('预算之内的普通图一份都不丢（普通科研图是几百 KB）', async () => {
    declarePayload(400 * 1024)
    for (let i = 1; i <= 4; i++) await store().render('Fig1.pdf', v(i))
    const s = useRenderStore.getState()
    for (let i = 1; i <= 4; i++) {
      expect(s.byKey[renderKey('Fig1.pdf', v(i))].svg).not.toBeNull()
    }
    expect(resident().total).toBe(4 * 400 * 1024)
  })
})

describe('pin：画布上正挂着的那一份绝不被内存预算清掉', () => {
  it('live 变体与 latest 都留着，被丢的是中间那几档老的', async () => {
    declarePayload(6 * MiB)
    await store().render('Fig1.pdf', v(1))
    // 画布上挂着的是第一版（用户改完又撤销回来了）
    store().prune(new Set([renderKey('Fig1.pdf', v(1))]))
    for (let i = 2; i <= 4; i++) await store().render('Fig1.pdf', v(i))

    const s = useRenderStore.getState()
    // 6×4 = 24 MiB > 16 MiB，必须丢；但丢的不能是这两份
    expect(s.byKey[renderKey('Fig1.pdf', v(1))].svg, 'live 那一份').not.toBeNull()
    expect(s.byKey[renderKey('Fig1.pdf', v(4))].svg, 'latest 那一份').not.toBeNull()
    expect(s.byKey[renderKey('Fig1.pdf', v(2))].svg).toBeNull()
    expect(s.byKey[renderKey('Fig1.pdf', v(3))].svg).toBeNull()
  })

  it('pin 名单每一轮 prune 都刷新——包括「这一轮没得清」的那种', async () => {
    declarePayload(6 * MiB)
    await store().render('Fig1.pdf', v(1))
    await store().render('Fig1.pdf', v(2))
    // 画布切到第二版；第一版仍在 recent 里，所以这一轮 prune **一条都清不掉**
    // ——早退的实现会把 pin 名单停在上一轮，于是下面 v1 被当成 live 保住，
    // 而真正 live 的 v2 反倒进了候选
    store().prune(new Set([renderKey('Fig1.pdf', v(2))]))
    for (let i = 3; i <= 4; i++) await store().render('Fig1.pdf', v(i))

    const s = useRenderStore.getState()
    expect(s.byKey[renderKey('Fig1.pdf', v(1))].svg).toBeNull()
    expect(s.byKey[renderKey('Fig1.pdf', v(4))].svg).not.toBeNull()
  })

  it('全都被 pin 住时宁可超预算，也不清掉正在显示的图', async () => {
    // 预算管的是**可驱逐的历史 payload**。画布上正挂着的那些是显示所必需的，
    // 清掉它们换来的是一块空白——那不是省内存，是坏功能（ADR 0022 §8）。
    declarePayload(12 * MiB)
    await store().render('Fig1.pdf', v(1))
    store().prune(new Set([renderKey('Fig1.pdf', v(1))]))
    await store().render('Fig1.pdf', v(2))

    const s = useRenderStore.getState()
    expect(s.byKey[renderKey('Fig1.pdf', v(1))].svg).not.toBeNull()
    expect(s.byKey[renderKey('Fig1.pdf', v(2))].svg).not.toBeNull()
    expect(resident().byFile['Fig1.pdf']).toBe(24 * MiB)
  })
})

describe('驱逐次序：先丢没人会撤销回去的，再丢近期档', () => {
  it('脚本变更后作废的那几档排在近期档前面，即使它更新', async () => {
    // 判据有两维：**在不在 recent 里**（第一维）与**多久没更新**（第二维）。
    // 只按第二维排（纯 LRU）的实现会先丢 Fig1 的老档，而 Fig3 那份早就
    // 因为脚本变更不作数了——留着它、丢掉一个真的撤销落点，正好丢反。
    declarePayload(8 * MiB)
    for (let f = 1; f <= 4; f++) {
      await store().render(`Fig${f}.pdf`, v(1))
      await store().render(`Fig${f}.pdf`, v(2))
    }
    // 每文件 16 MiB、全局 64 MiB——两条都**正好在线上**，一份都还没被丢
    expect(resident().total).toBe(SVG_RECENT_BUDGET_GLOBAL)

    // Fig3 的脚本变了：它那两档已经不是这个脚本的样子（markStale 清掉 recent）。
    // 它是**最新**被动过的文件，纯 LRU 会把它排到最后。
    store().markStale(['Fig3.pdf'])
    // 再开一张图，把全局推过线
    await store().render('Fig5.pdf', v(1))

    const s = useRenderStore.getState()
    // 第一维先生效：Fig3 那份（不在 recent 里）先被丢
    expect(s.byKey[renderKey('Fig3.pdf', v(1))].svg, 'Fig3 的作废档').toBeNull()
    // 而最老的那份近期档**还在**——它是真的撤销落点，排在作废档后面
    expect(s.byKey[renderKey('Fig1.pdf', v(1))].svg, '最老的近期档').not.toBeNull()
    // 收进预算就停手，不多丢
    expect(s.byKey[renderKey('Fig2.pdf', v(1))].svg).not.toBeNull()
    expect(s.byKey[renderKey('Fig5.pdf', v(1))].svg).not.toBeNull()
    expect(resident().total).toBeLessThanOrEqual(SVG_RECENT_BUDGET_GLOBAL)
  })

  it('全局预算是独立的第二把尺子：每文件都合规，合起来照样收', async () => {
    declarePayload(8 * MiB)
    for (let f = 1; f <= 6; f++) {
      await store().render(`Fig${f}.pdf`, v(1))
      await store().render(`Fig${f}.pdf`, v(2))
    }
    const after = resident()
    for (const [file, bytes] of Object.entries(after.byFile)) {
      expect(bytes, file).toBeLessThanOrEqual(SVG_RECENT_BUDGET_PER_FILE)
    }
    expect(after.total).toBeLessThanOrEqual(SVG_RECENT_BUDGET_GLOBAL)
    // 每个文件的 latest 都还在——被丢的只能是老档
    for (let f = 1; f <= 6; f++) {
      expect(
        useRenderStore.getState().byKey[renderKey(`Fig${f}.pdf`, v(2))].svg,
        `Fig${f} 的 latest`,
      ).not.toBeNull()
    }
  })
})

describe('记账归零：换项目 / 换文件之后不留残账', () => {
  it('clear()：驻留字节归零', async () => {
    declarePayload(6 * MiB)
    await store().render('Fig1.pdf', v(1))
    expect(resident().total).toBe(6 * MiB)
    store().clear()
    expect(resident().total).toBe(0)
    expect(resident().byFile).toEqual({})
  })

  it('reset(fileId)：只归零那个文件，别的文件一个字节不动', async () => {
    declarePayload(6 * MiB)
    await store().render('Fig1.pdf', v(1))
    await store().render('Fig2.pdf', v(1))
    store().reset('Fig1.pdf')
    const after = resident()
    expect(after.byFile['Fig1.pdf']).toBeUndefined()
    expect(after.byFile['Fig2.pdf']).toBe(6 * MiB)
    expect(after.total).toBe(6 * MiB)
  })

  it('prune 清掉条目之后，那份字节也不再算数', async () => {
    declarePayload(6 * MiB)
    for (let i = 1; i <= 6; i++) await store().render('Fig1.pdf', v(i))
    store().prune(new Set([renderKey('Fig1.pdf', v(6))]))
    // 条目数被 RECENT_VARIANTS 压住，字节数跟着掉——两条策略在同一维上不冲突
    expect(Object.keys(useRenderStore.getState().byKey).length).toBeLessThanOrEqual(4)
    expect(resident().total).toBeLessThanOrEqual(4 * 6 * MiB)
  })
})

describe('被清掉 payload 的那一版：诚实的显示，不丢语义', () => {
  it('是 evicted，不是 fallback——画布挂的就是这一版，只是画法变了', async () => {
    declarePayload(12 * MiB)
    await store().render('Fig1.pdf', v(1))
    await store().render('Fig1.pdf', v(2))

    // 用户撤销回第一版
    const back = panel('p1', 'Fig1.pdf', v(1))
    const view = panelDisplayView(useRenderStore.getState(), back)
    expect(view.kind).toBe('evicted')
    // **来源键就是自己**：掉进 fallback 的话诊断会说画布挂着第二版的图，
    // 而几何权威是第一版——issue #131 那种错配
    expect(view.sourceKey).toBe(view.currentKey)
    expect(view.svg).toBeNull()
    expect(view.manifest).not.toBeNull()
  })

  it('几何权威一个字都不放松：exact manifest 仍是这一版自己的', async () => {
    declarePayload(12 * MiB)
    await store().render('Fig1.pdf', v(1))
    await store().render('Fig1.pdf', v(2))

    const back = panel('p1', 'Fig1.pdf', v(1))
    const m = exactPanelManifest(useRenderStore.getState(), back)
    expect(m).not.toBeNull()
    // 是**第一版**的 manifest，不是显示退路那一版的
    expect(m?.stem).toBe(`Fig1.pdf#${JSON.stringify(v(1))}`)
  })

  it('重画之后 svgEvicted 归位，矢量图回得来（这道闸不是单向门）', async () => {
    declarePayload(12 * MiB)
    await store().render('Fig1.pdf', v(1))
    await store().render('Fig1.pdf', v(2))
    expect(useRenderStore.getState().byKey[renderKey('Fig1.pdf', v(1))].svgEvicted).toBe(true)

    await store().render('Fig1.pdf', v(1))
    const again = useRenderStore.getState().byKey[renderKey('Fig1.pdf', v(1))]
    expect(again.svgEvicted).toBe(false)
    expect(again.svg).not.toBeNull()
    const p = panel('p1', 'Fig1.pdf', v(1))
    expect(panelDisplayView(useRenderStore.getState(), p).kind).toBe('exact')
  })
})

describe('svgBytes 的来路', () => {
  it('优先信后端的 preview.svg_bytes（与硬闸量的是同一个东西）', async () => {
    // 字符串很小、声明很大：记的必须是声明的那个数，否则前端就在用**另一把
    // 尺子**量同一件事，两侧一定漂
    declarePayload(9 * MiB)
    await store().render('Fig1.pdf', v(1))
    expect(useRenderStore.getState().byKey[renderKey('Fig1.pdf', v(1))].svgBytes).toBe(9 * MiB)
  })

  it('老后端不给 preview 时退回 svg.length，**不复制字符串**', async () => {
    const body = 'y'.repeat(5000)
    engineRender.mockResolvedValue({
      rev: 1,
      manifest: manifest('Fig1'),
      warnings: [],
      svg: `<svg>${body}</svg>`,
    })
    await store().render('Fig1.pdf', v(1))
    const e = useRenderStore.getState().byKey[renderKey('Fig1.pdf', v(1))]
    expect(e.svgBytes).toBe(`<svg>${body}</svg>`.length)
  })

  it('raster 档没有 payload：记 0，不占预算', async () => {
    engineRender.mockResolvedValue({
      rev: 1,
      manifest: manifest('Fig1'),
      warnings: [],
      preview: {
        mode: 'raster',
        reason: 'svg_hard_limit',
        svg_bytes: 126_132_735,
        rasterized_artist_count: 0,
      },
    })
    await store().render('Fig1.pdf', v(1))
    const e = useRenderStore.getState().byKey[renderKey('Fig1.pdf', v(1))]
    expect(e.svg).toBeNull()
    expect(e.svgBytes).toBe(0)
    expect(e.svgEvicted).toBe(false) // 它不是被驱逐的，是引擎压根没交出来
    expect(resident().total).toBe(0)
  })
})

describe('判据真的进了控制流：渲染成功那一刻就收账', () => {
  it('没有人显式调用 evictSvgBudget，超预算照样被收', async () => {
    // 「有一个 evictSvgBudget 函数」与「它在渲染路径上跑过」是两件事。
    // 这一条只走 render()，不碰那个 action。
    declarePayload(12 * MiB)
    await store().render('Fig1.pdf', v(1))
    await store().render('Fig1.pdf', v(2))
    expect(resident().byFile['Fig1.pdf']).toBe(12 * MiB)
  })

  it('没超预算时一个 set 都不发（同步 effect 不该被空转唤醒）', async () => {
    declarePayload(1 * MiB)
    await store().render('Fig1.pdf', v(1))
    const before = useRenderStore.getState().byKey
    store().evictSvgBudget()
    expect(useRenderStore.getState().byKey).toBe(before)
  })
})

describe('真实巨型 payload：不是声明出来的字节数', () => {
  it('四版各 12 MiB 的真字符串进出之后，驻留字节仍在预算之内', async () => {
    const filler = 'x'.repeat(12 * MiB)
    let rev = 0
    engineRender.mockImplementation(async (_id: string, patches: unknown[]) => ({
      rev: ++rev,
      manifest: manifest('Fig1'),
      warnings: [],
      // 不带 preview：走 svg.length 那条路，量的是**真的这么长**的字符串
      svg: `<svg id="${JSON.stringify(patches).length}">${filler}</svg>`,
    }))
    for (let i = 1; i <= 4; i++) await store().render('Fig1.pdf', v(i))

    const after = resident()
    // 没有预算的话是 4 × 12 MiB ≈ 48 MiB
    expect(after.byFile['Fig1.pdf']).toBeLessThanOrEqual(SVG_RECENT_BUDGET_PER_FILE)
    expect(after.byFile['Fig1.pdf']).toBeGreaterThan(11 * MiB) // 最后那份还在
    // 四条语义状态一条不少
    expect(Object.keys(useRenderStore.getState().byKey)).toHaveLength(4)
    for (let i = 1; i <= 4; i++) {
      expect(useRenderStore.getState().byKey[renderKey('Fig1.pdf', v(i))].manifest).not.toBeNull()
    }
    // 真的只剩一份字符串挂在堆上
    const alive = Object.values(useRenderStore.getState().byKey).filter((e) => e.svg != null)
    expect(alive).toHaveLength(1)
    expect(alive[0].lastPatches).toBe(JSON.stringify(v(4)))
  })
})

describe('renderKeyOf 与预算无关（回归护栏）', () => {
  it('驱逐不改变任何键', async () => {
    declarePayload(12 * MiB)
    await store().render('Fig1.pdf', v(1))
    await store().render('Fig1.pdf', v(2))
    const p = panel('p1', 'Fig1.pdf', v(1))
    expect(Object.keys(useRenderStore.getState().byKey)).toContain(renderKeyOf(p))
  })
})
