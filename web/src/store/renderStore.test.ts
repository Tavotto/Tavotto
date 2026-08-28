/**
 * 渲染态按「文件 + 变体」分键（Phase F）。
 *
 * 关键约定三条：同一文件的不同 overrides 各存各的；SVG 与 manifest 来自
 * **同一次响应**（inline_svg，不再第二跳 GET）；自己那份还没画出来时
 * 退回该文件最近画好的那张，而不是让画布闪回磁盘原图。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineRenderOptions, Manifest } from '@/lib/api'
import { panelRender, renderKey, renderKeyOf, useRenderStore } from './renderStore'
import { EDITOR_SVG_HARD_LIMIT_BYTES } from '@/lib/previewBudget'
import type { PanelObject } from '@/types/document'

const engineRender = vi.fn()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

const manifest = (stem: string): Manifest =>
  ({ stem, size_mm: [80, 60], elements: [] }) as unknown as Manifest

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

beforeEach(() => {
  engineRender.mockReset()
  useRenderStore.getState().clear()
})

describe('二道闸：超大 SVG 不进 store（ADR 0021）', () => {
  it('后端说 raster 时不存 svg，但 manifest / status 照常', async () => {
    const p = panel('a', 'Fig1.pdf')
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
    await useRenderStore.getState().render('Fig1.pdf', [])

    const entry = useRenderStore.getState().get(renderKeyOf(p))
    // **不是渲染失败**：manifest 在，状态是 ready，只是没有矢量图
    expect(entry.status).toBe('ready')
    expect(entry.manifest).not.toBeNull()
    expect(entry.svg).toBeNull()
    expect(entry.preview.mode).toBe('raster')
  })

  it('后端异常地给了一份超大 svg：当场丢掉，绝不 prepareSvg 后存进来', async () => {
    engineRender.mockResolvedValue({
      rev: 1,
      manifest: manifest('Fig1'),
      warnings: [],
      // 老后端 / 绕过了闸的后端：没有 preview 字段，svg 却大得离谱
      svg: `<svg>${'x'.repeat(EDITOR_SVG_HARD_LIMIT_BYTES)}</svg>`,
    })
    await useRenderStore.getState().render('Fig1.pdf', [])

    const entry = useRenderStore.getState().get(renderKey('Fig1.pdf', []))
    expect(entry.svg).toBeNull()
    expect(entry.preview.mode).toBe('raster')
    expect(entry.preview.reason).toBe('fallback')
  })

  it('raster 之后再画成矢量：svg 回得来（这道闸不是单向门）', async () => {
    engineRender.mockResolvedValue({
      rev: 1,
      manifest: manifest('Fig1'),
      warnings: [],
      preview: {
        mode: 'raster',
        reason: 'svg_hard_limit',
        svg_bytes: 1e8,
        rasterized_artist_count: 0,
      },
    })
    await useRenderStore.getState().render('Fig1.pdf', [])
    engineRender.mockResolvedValue({
      rev: 2,
      manifest: manifest('Fig1'),
      warnings: [],
      svg: '<svg id="back"/>',
    })
    await useRenderStore.getState().render('Fig1.pdf', [])

    const entry = useRenderStore.getState().get(renderKey('Fig1.pdf', []))
    expect(entry.svg).toContain('id="back"')
    expect(entry.preview.mode).toBe('vector')
  })
})

describe('变体键', () => {
  it('同文件不同 overrides 是不同的键，相同 overrides 是同一个键', () => {
    const a = panel('a', 'Fig1.pdf', [{ gid: 'g', prop: 'color', value: '#f00' }])
    const b = panel('b', 'Fig1.pdf', [{ gid: 'g', prop: 'color', value: '#00f' }])
    const c = panel('c', 'Fig1.pdf', [{ gid: 'g', prop: 'color', value: '#f00' }])
    expect(renderKeyOf(a)).not.toBe(renderKeyOf(b))
    expect(renderKeyOf(a)).toBe(renderKeyOf(c))
  })

  it('文件名里带空格也不会撞键（变体串必然以 [ 开头）', () => {
    expect(renderKey('my figs/a [1].pdf', [])).not.toBe(renderKey('my figs/a', [1, []]))
  })
})

describe('render：SVG 与 manifest 同一次响应', () => {
  it('inline 回来的 SVG 直接入库（去掉 width/height 铺满容器）', async () => {
    engineRender.mockResolvedValue({
      rev: 3,
      manifest: manifest('Fig1'),
      svg: '<svg width="216pt" height="144pt" viewBox="0 0 216 144"><g/></svg>',
    })

    await useRenderStore.getState().render('Fig1.pdf', [])

    const st = useRenderStore.getState().byKey[renderKey('Fig1.pdf', [])]
    expect(st.rev).toBe(3)
    expect(st.svg).toContain('preserveAspectRatio="none"')
    expect(st.svg).not.toContain('width="216pt"')
    // 一次请求拿齐，不再有第二跳
    expect(engineRender).toHaveBeenCalledTimes(1)
  })

  it('两个变体各存各的，互不覆盖', async () => {
    const v1 = [{ gid: 'g', prop: 'text', value: 'A' }]
    const v2 = [{ gid: 'g', prop: 'text', value: 'B' }]
    engineRender.mockImplementation(async (_id: string, patches: { value: string }[]) => ({
      rev: 1,
      manifest: manifest(`Fig1-${patches[0].value}`),
      svg: `<svg>${patches[0].value}</svg>`,
    }))

    await useRenderStore.getState().render('Fig1.pdf', v1)
    await useRenderStore.getState().render('Fig1.pdf', v2)

    const { byKey } = useRenderStore.getState()
    expect(byKey[renderKey('Fig1.pdf', v1)].manifest?.stem).toBe('Fig1-A')
    expect(byKey[renderKey('Fig1.pdf', v2)].manifest?.stem).toBe('Fig1-B')
  })

  it('降质渲染把 preview_dpi 带下去并记在条目上', async () => {
    engineRender.mockResolvedValue({ rev: 1, manifest: manifest('Fig1'), svg: '<svg/>' })
    await useRenderStore.getState().render('Fig1.pdf', [], 100)
    expect(engineRender.mock.calls[0][2]).toMatchObject({ previewDpi: 100 })
    expect(useRenderStore.getState().byKey[renderKey('Fig1.pdf', [])].previewDpi).toBe(100)

    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(useRenderStore.getState().byKey[renderKey('Fig1.pdf', [])].previewDpi).toBeNull()
  })
})

describe('panelRender：新变体还没画出来时接着显示上一张', () => {
  it('自己那份没有 manifest 就退回该文件最近画好的那份', async () => {
    engineRender.mockResolvedValue({ rev: 2, manifest: manifest('Fig1'), svg: '<svg>old</svg>' })
    await useRenderStore.getState().render('Fig1.pdf', [])

    // 用户刚改了一个值：新变体只有排期，还没有图
    const next = panel('a', 'Fig1.pdf', [{ gid: 'g', prop: 'text', value: 'new' }])
    useRenderStore.getState().patch(renderKeyOf(next), {
      fileId: 'Fig1.pdf',
      wantPatches: JSON.stringify(next.overrides),
      status: 'rendering',
    })

    const view = panelRender(useRenderStore.getState(), next)
    expect(view?.manifest?.stem).toBe('Fig1')          // 上一张还在
    expect(view?.svg).toContain('old')
    expect(view?.status).toBe('rendering')             // 状态仍是自己的
  })

  it('自己那份画好之后就是 store 里那个对象（引用稳定）', async () => {
    engineRender.mockResolvedValue({ rev: 1, manifest: manifest('Fig1'), svg: '<svg/>' })
    await useRenderStore.getState().render('Fig1.pdf', [])
    const p = panel('a', 'Fig1.pdf', [])
    const st = useRenderStore.getState()
    expect(panelRender(st, p)).toBe(st.byKey[renderKeyOf(p)])
  })
})

describe('building：文件级的构建提示不写进变体条目', () => {
  it('置位/清除都只动 building 表', () => {
    const store = useRenderStore.getState()
    store.patch(renderKey('Fig1.pdf', []), { fileId: 'Fig1.pdf', status: 'ready' })
    store.noteBuilding('Fig1.pdf', { cold: true, cost: 'heavy' })

    let s = useRenderStore.getState()
    expect(s.building['Fig1.pdf']).toEqual({ cold: true, cost: 'heavy' })
    // 另一个副本的条目没被盖成「渲染中」——那样它会永远转圈（没人来收）
    expect(s.byKey[renderKey('Fig1.pdf', [])].status).toBe('ready')

    store.noteBuilding('Fig1.pdf', null)
    s = useRenderStore.getState()
    expect(s.building['Fig1.pdf']).toBeUndefined()
  })
})
