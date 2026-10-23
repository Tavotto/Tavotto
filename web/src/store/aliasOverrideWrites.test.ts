/**
 * 「一个量只留一个 override 地址」的写入口（`upsertOverrides` + `lib/aliasOverrides.ts`）。
 *
 * 引擎里窄端永远排在广播之后应用：先把图例标题元素的字号改 9、再在图例上把「标题字号」改 11，
 * 两条都在时图上仍是 9，而图例控件读自己那条显示 11——「改了、图不变、控件说已改」。事实来自 manifest
 * 元素的 `alias_covers`（引擎 `overrides.alias_covers`），写入口在同一次 commit 里清掉另一个地址。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { Manifest, ManifestElement } from '@/lib/api'
import { setOverride, setOverrides } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: () => new Promise(() => {}),
}))

const LEG = 'axes_0.legend'
const TITLE = 'axes_0.legend.title'
const T0 = 'axes_0.legend.texts_0'
const T1 = 'axes_0.legend.texts_1'
const CB = 'axes_1.colorbar'
const CB_X = 'axes_2.ticks_x'
const CB_Y = 'axes_2.ticks_y'

const el = (gid: string, extra: Partial<ManifestElement> = {}): ManifestElement =>
  ({ gid, role: 'text', label: gid, editable: [], ...extra }) as ManifestElement

const MANIFEST = {
  elements: [
    el(LEG, {
      role: 'legend',
      alias_covers: { title_fontsize: { fontsize: [TITLE] }, fontsize: { fontsize: [T0, T1] } },
      alias_pure: ['title_fontsize'],
    }),
    el(TITLE),
    el(T0),
    el(T1),
    el(CB, {
      role: 'colorbar',
      alias_covers: { tick_fontsize: { fontsize: [CB_X, CB_Y] }, tick_color: { color: [CB_X, CB_Y] } },
    }),
    el(CB_X),
    el(CB_Y),
  ],
} as unknown as Manifest

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

const ov = () => (useDocumentStore.getState().doc.objects[0] as PanelObject).overrides
const past = () => useDocumentStore.getState().past.length

async function seed(overrides: PanelObject['overrides'], manifest: Manifest | null = MANIFEST) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_alias_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(panel(overrides))
  })
  useDocumentStore.setState({ past: [], future: [] })
  if (manifest) seedExactRender(useDocumentStore.getState().doc.objects[0] as PanelObject, manifest)
}

beforeEach(() => useRenderStore.getState().clear())

describe('图例标题（1:1）', () => {
  it('先窄后广：写图例「标题字号」清掉标题元素上的字号 override', async () => {
    await seed([{ gid: TITLE, prop: 'fontsize', value: 9 }])
    setOverride('p1', LEG, 'title_fontsize', 11, true)
    expect(ov()).toEqual([{ gid: LEG, prop: 'title_fontsize', value: 11 }])
    expect(past()).toBe(1)
  })

  it('先广后窄：写标题元素的字号清掉图例上的「标题字号」', async () => {
    await seed([{ gid: LEG, prop: 'title_fontsize', value: 11 }])
    setOverride('p1', TITLE, 'fontsize', 9, true)
    expect(ov()).toEqual([{ gid: TITLE, prop: 'fontsize', value: 9 }])
    expect(past()).toBe(1)
  })

  it('撤销一次回到写之前（被清掉的那条也回来）', async () => {
    await seed([{ gid: TITLE, prop: 'fontsize', value: 9 }])
    setOverride('p1', LEG, 'title_fontsize', 11, true)
    useDocumentStore.getState().undo()
    expect(ov()).toEqual([{ gid: TITLE, prop: 'fontsize', value: 9 }])
  })

  it('同值写入但另一个地址上留着旧 override：不是 no-op，要清掉', async () => {
    await seed([
      { gid: LEG, prop: 'title_fontsize', value: 11 },
      { gid: TITLE, prop: 'fontsize', value: 9 },
    ])
    setOverride('p1', LEG, 'title_fontsize', 11, true)
    expect(ov()).toEqual([{ gid: LEG, prop: 'title_fontsize', value: 11 }])
  })
})

describe('图例整体字号 vs 单条（1:多）', () => {
  it('写整体字号清掉每条图例项上的字号，不碰别的 prop', async () => {
    await seed([
      { gid: T0, prop: 'fontsize', value: 5 },
      { gid: T1, prop: 'color', value: '#ff0000' },
      { gid: T1, prop: 'fontsize', value: 6 },
    ])
    setOverride('p1', LEG, 'fontsize', 8, true)
    expect(ov()).toEqual([
      { gid: T1, prop: 'color', value: '#ff0000' },
      { gid: LEG, prop: 'fontsize', value: 8 },
    ])
  })

  it('写单条字号保留整体字号（它还管着别的图例项）', async () => {
    await seed([{ gid: LEG, prop: 'fontsize', value: 8 }])
    setOverride('p1', T0, 'fontsize', 5, true)
    expect(ov()).toEqual([
      { gid: LEG, prop: 'fontsize', value: 8 },
      { gid: T0, prop: 'fontsize', value: 5 },
    ])
  })
})

describe('单条目图例：整体字号不纯，不许被反向清', () => {
  // 只有一条图例项时 `fontsize` 也是 1:1，但 `_set_legend_fontsize` 还改 `Legend._fontsize` 并重建布局
  // （示意线长 / 内边距 / 文字间距），窄端存不下——引擎不把它声明为 alias_pure
  const ONE = {
    elements: [
      el(LEG, { role: 'legend', alias_covers: { fontsize: { fontsize: [T0] } } }),
      el(T0),
    ],
  } as unknown as Manifest

  it('先改图例整体字号、再改那一条的字号：(legend, fontsize) 保留', async () => {
    await seed([{ gid: LEG, prop: 'fontsize', value: 8 }], ONE)
    setOverride('p1', T0, 'fontsize', 5, true)
    expect(ov()).toEqual([
      { gid: LEG, prop: 'fontsize', value: 8 },
      { gid: T0, prop: 'fontsize', value: 5 },
    ])
  })

  it('对照：同样 1:1 但被声明为纯别名时才反向清', async () => {
    const pure = {
      elements: [
        el(LEG, { role: 'legend', alias_covers: { fontsize: { fontsize: [T0] } }, alias_pure: ['fontsize'] }),
        el(T0),
      ],
    } as unknown as Manifest
    await seed([{ gid: LEG, prop: 'fontsize', value: 8 }], pure)
    setOverride('p1', T0, 'fontsize', 5, true)
    expect(ov()).toEqual([{ gid: T0, prop: 'fontsize', value: 5 }])
  })
})

describe('色条刻度字号', () => {
  it('写色条「刻度字号」清掉色条轴刻度组上的字号；刻度颜色那条不动', async () => {
    await seed([
      { gid: CB_X, prop: 'fontsize', value: 6 },
      { gid: CB_Y, prop: 'fontsize', value: 7 },
      { gid: CB_X, prop: 'color', value: '#00ff00' },
    ])
    setOverride('p1', CB, 'tick_fontsize', 9, true)
    expect(ov()).toEqual([
      { gid: CB_X, prop: 'color', value: '#00ff00' },
      { gid: CB, prop: 'tick_fontsize', value: 9 },
    ])
  })

  it('写其中一组刻度保留色条的「刻度字号」（1:2，它还管着另一组）', async () => {
    await seed([{ gid: CB, prop: 'tick_fontsize', value: 9 }])
    setOverride('p1', CB_X, 'fontsize', 6, true)
    expect(ov()).toHaveLength(2)
  })
})

describe('批量写入与退化', () => {
  it('同一批里两端一起写：都保留（窄端赢是引擎既有语义），只清批外的', async () => {
    await seed([{ gid: T0, prop: 'fontsize', value: 5 }, { gid: T1, prop: 'fontsize', value: 6 }])
    setOverrides('p1', literal('批量'), [
      { gid: LEG, prop: 'fontsize', value: 8 },
      { gid: T0, prop: 'fontsize', value: 7 },
    ])
    expect(ov()).toEqual([
      { gid: T0, prop: 'fontsize', value: 7 },
      { gid: LEG, prop: 'fontsize', value: 8 },
    ])
    expect(past()).toBe(1)
  })

  it('没有 manifest / 旧引擎没发 alias_covers：退化为只 upsert，不报错', async () => {
    await seed([{ gid: TITLE, prop: 'fontsize', value: 9 }], null)
    setOverride('p1', LEG, 'title_fontsize', 11, true)
    expect(ov()).toEqual([
      { gid: TITLE, prop: 'fontsize', value: 9 },
      { gid: LEG, prop: 'title_fontsize', value: 11 },
    ])
    useRenderStore.getState().clear()
    await seed([{ gid: TITLE, prop: 'fontsize', value: 9 }], { elements: [el(LEG), el(TITLE)] } as unknown as Manifest)
    setOverride('p1', LEG, 'title_fontsize', 11, true)
    expect(ov()).toHaveLength(2)
  })
})
