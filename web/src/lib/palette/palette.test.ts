/**
 * 推荐配色的纯逻辑（ADR 0210 §四、§五、§七）：
 * 1. 色键表：同身份跨子图成一组；槽一旦分配就不动；归一化同名只建议并入；没有身份的不参与。
 * 2. 排序：色数不够 / 撞色的排后；默认 batlowS 过得了就用它，过不了换排第一的。
 * 3. 计划：只写配色管的那几格；用户手改过的不覆盖；一次计划 = 一次 commit 的全部 patch。
 * 4. 目录：期刊风格色板带 copyleft 标记；Okabe–Ito 只有目录里一份色值。
 */
import { describe, expect, it } from 'vitest'
import type { Manifest, ManifestElement } from '@/lib/api'
import type { PanelObject, PanelOverride } from '@/types/document'
import { DEFAULT_SCHEME, PALETTES, paletteById } from './catalog'
import {
  collectSeries,
  emptyTable,
  looksNeutral,
  mergeKeys,
  normalizeName,
  readColorKeys,
  reconcile,
  setNeutral,
  setScheme,
} from './colorKeys'
import { planPalette } from './plan'
import { colorForSlot, evaluate, initialScheme, rankPalettes } from './rank'
import { OKABE_ITO } from '@/lib/swatches'
import { canvasToDoc, docToCanvas, type FigureDocument } from '@/types/document'
import copyleftEmpty from './copyleftEmpty.json'
import { copyleftExcluded, isCopyleftCatalog, noCopyleftPalettes } from '../../../scripts/palette-copyleft-path.mjs'

/** 假的身份摘要：同名同摘要、不含原文（真摘要是 sha256 的前 16 位） */
const id = (label: string) => {
  let h = 2166136261
  for (const ch of label) h = Math.imul(h ^ ch.codePointAt(0)!, 16777619) >>> 0
  return `l1:${h.toString(16).padStart(8, '0')}${(h ^ 0xa5a5a5a5).toString(16).padStart(8, '0')}`
}

function series(gid: string, label: string | null, role = 'line'): ManifestElement {
  const prop = role === 'line' ? 'color' : 'facecolor'
  return {
    gid,
    role,
    label: gid,
    ...(label !== null ? { identity: id(label) } : {}),
    bbox: [0, 0, 1, 1],
    editable: [
      { prop, type: 'color', value: '#000000' },
      { prop: 'label', type: 'text', value: label ?? '' },
    ],
    draggable: false,
  } as unknown as ManifestElement
}

const manifestOf = (els: ManifestElement[]): Manifest =>
  ({ stem: 'f', size_mm: [80, 60], elements: [{ gid: 'axes_0', role: 'axes', label: 'ax', bbox: [0, 0, 1, 1], editable: [], draggable: false }, ...els] }) as unknown as Manifest

const panel = (pid: string, overrides: PanelOverride[] = []): PanelObject =>
  ({ id: pid, type: 'panel', fileId: `${pid}.pdf`, fileKind: 'pdf', nativeW: 80, nativeH: 60, x: 0, y: 0, w: 80, h: 60, overrides }) as unknown as PanelObject

/** 两张子图：A 里 Control、Drug A，B 里顺序反过来，外加一条没名字的 */
function twoPanels(overridesB: PanelOverride[] = []) {
  const pA = panel('A')
  const pB = panel('B', overridesB)
  const ms: Record<string, Manifest> = {
    A: manifestOf([series('axes_0.lines_0', 'Control'), series('axes_0.lines_1', 'Drug A')]),
    B: manifestOf([
      series('axes_0.lines_0', 'Drug A'),
      series('axes_0.lines_1', 'Control'),
      series('axes_0.lines_2', null),
    ]),
  }
  return { panels: [pA, pB], manifestOf: (p: PanelObject) => ms[p.id] }
}

describe('色键表', () => {
  it('同一个显式 label 跨子图是同一组，顺序不同也同色', () => {
    const { panels, manifestOf: m } = twoPanels()
    const r = reconcile(emptyTable(DEFAULT_SCHEME), collectSeries(panels, m))
    expect(r.groups.map((g) => [g.name, g.slot, g.series.length])).toEqual([
      ['Control', 0, 2],
      ['Drug A', 1, 2],
    ])
    expect(r.unlabeled.map((s) => s.gid)).toEqual(['axes_0.lines_2'])
    // 文档里只有身份摘要，没有 label 原文
    expect(JSON.stringify(r.table)).not.toContain('Drug A')
  })

  it('槽一旦分配就不动：新组取最小空槽，消失的组也不让出槽', () => {
    const { panels, manifestOf: m } = twoPanels()
    const t1 = reconcile(emptyTable(DEFAULT_SCHEME), collectSeries(panels, m)).table
    // 第二次只剩 Drug A，再加一组新的
    const only = [panel('A')]
    const t2 = reconcile(t1, collectSeries(only, () => manifestOf([series('axes_0.lines_0', 'Drug A'), series('axes_0.lines_1', 'Drug B')])))
    expect(t2.groups.map((g) => [g.name, g.slot])).toEqual([
      ['Drug A', 1],
      ['Drug B', 2],
    ])
    expect(t2.table.keys.map((k) => k.slot)).toEqual([0, 1, 2])
  })

  it('归一化后同名（大小写、全半角、空格）只建议并入，确认后才合', () => {
    const p = [panel('A')]
    const ms = manifestOf([series('l0', 'Drug A'), series('l1', 'ｄｒｕｇ  a')])
    const r = reconcile(emptyTable(DEFAULT_SCHEME), collectSeries(p, () => ms))
    expect(r.groups).toHaveLength(2)
    expect(r.merges).toEqual([{ into: 'k1', from: 'k2', name: 'Drug A' }])
    const merged = mergeKeys(r.table, 'k1', 'k2')
    const r2 = reconcile(merged, collectSeries(p, () => ms))
    expect(r2.groups.map((g) => [g.key, g.series.length])).toEqual([['k1', 2]])
    expect(r2.merges).toEqual([])
    expect(normalizeName('  Ｃｏｎｔｒｏｌ\tGroup ')).toBe('control group')
  })

  it('对照组只建议中性灰；设上后取消回到最小空槽', () => {
    const { panels, manifestOf: m } = twoPanels()
    const r = reconcile(emptyTable(DEFAULT_SCHEME), collectSeries(panels, m))
    expect(r.groups.map((g) => g.suggestNeutral)).toEqual([true, false])
    expect(r.table.keys[0].slot).toBe(0) // 没有自动生效
    const n = setNeutral(r.table, 'k1', true)
    expect(n.keys[0].slot).toBe('neutral')
    expect(reconcile(n, collectSeries(panels, m)).groups[0].suggestNeutral).toBe(false)
    expect(setNeutral(n, 'k1', false).keys[0].slot).toBe(0)
    for (const name of ['Vehicle', 'WT', 'wild-type', 'Sham', '对照组', 'DMSO']) expect(looksNeutral(name)).toBe(true)
    for (const name of ['Drug A', 'Controlled release', 'KO']) expect(looksNeutral(name)).toBe(false)
  })

  it('从文档读：形状不对的整份不认，坏条目逐条丢', () => {
    expect(readColorKeys(null)).toBeNull()
    expect(readColorKeys({ version: 2, scheme: 'x', keys: [] })).toBeNull()
    const t = readColorKeys({
      version: 1,
      scheme: 'okabe-ito',
      keys: [
        { key: 'k1', slot: 0, identities: ['l1:a'] },
        { key: 'k2', slot: 0, identities: ['l1:b'] }, // 槽重复
        { key: 'k3', slot: -1, identities: ['l1:c'] },
        { key: 'k4', slot: 'neutral', identities: ['l1:a', 'l1:d'] }, // l1:a 已归 k1
        'junk',
      ],
      owned: [{ panel: 'A', gid: 'g', prop: 'color', value: '#000000' }, { panel: 'A' }],
    })!
    expect(t.keys).toEqual([
      { key: 'k1', slot: 0, identities: ['l1:a'] },
      { key: 'k4', slot: 'neutral', identities: ['l1:d'] },
    ])
    expect(t.owned).toHaveLength(1)
  })

  it('换方案只换槽到颜色，不动键到槽', () => {
    const { panels, manifestOf: m } = twoPanels()
    const t = reconcile(emptyTable(DEFAULT_SCHEME), collectSeries(panels, m)).table
    const t2 = setScheme(t, 'okabe-ito')
    expect(t2.keys).toBe(t.keys)
    expect(t2.scheme).toBe('okabe-ito')
  })
})

describe('推荐排序', () => {
  it('色数不够的排后，且不循环复用颜色', () => {
    const tol = paletteById('tol.high-contrast')!
    expect(colorForSlot(3, tol)).toBeNull()
    const r = evaluate(tol, [0, 1, 2, 3])
    expect([r.tooFew, r.fits]).toEqual([true, false])
    const ranked = rankPalettes([0, 1, 2, 3])
    expect(ranked.findIndex((x) => x.palette.id === 'tol.high-contrast')).toBeGreaterThan(
      ranked.findLastIndex((x) => x.fits),
    )
  })

  it('过了硬约束的在前，按四种视觉下的最小色差从大到小', () => {
    const ranked = rankPalettes([0, 1, 2, 'neutral'])
    const fit = ranked.filter((r) => r.fits)
    expect(fit.length).toBeGreaterThan(3)
    for (let i = 1; i < fit.length; i++) expect(fit[i - 1].closest!.deltaE).toBeGreaterThanOrEqual(fit[i].closest!.deltaE)
    for (const r of ranked.filter((x) => !x.fits && !x.tooFew)) expect(r.closest!.deltaE).toBeLessThan(10)
    // ColorBrewer Set2 前 3 色加灰：灰和 Set2 的浅色在红弱下撞
    expect(ranked.find((r) => r.palette.id === 'colorbrewer.set2')!.fits).toBe(false)
  })

  it('默认 batlowS：过得了就用它，过不了换排第一的', () => {
    expect(initialScheme([0, 1, 2, 'neutral'])).toBe(DEFAULT_SCHEME)
    const five: (number | 'neutral')[] = [0, 1, 2, 3, 4]
    expect(evaluate(paletteById(DEFAULT_SCHEME)!, five).fits).toBe(false)
    expect(initialScheme(five)).toBe(rankPalettes(five)[0].palette.id)
    expect(initialScheme([0])).toBe(DEFAULT_SCHEME)
  })
})

describe('写入计划', () => {
  it('同一组在每张子图里写同一个颜色，一份计划覆盖所有面板', () => {
    const { panels, manifestOf: m } = twoPanels()
    const r = reconcile(emptyTable(DEFAULT_SCHEME), collectSeries(panels, m))
    const pal = paletteById(DEFAULT_SCHEME)!
    const plan = planPalette(r.table, pal, r.groups, panels)
    const colorOf = (pid: string, gid: string) =>
      plan.panels.find((p) => p.panel.id === pid)!.patches.find((x) => x.gid === gid)!.value
    expect(colorOf('A', 'axes_0.lines_0')).toBe(pal.colors[0])
    expect(colorOf('B', 'axes_0.lines_1')).toBe(pal.colors[0])
    expect(colorOf('B', 'axes_0.lines_0')).toBe(pal.colors[1])
    expect(plan.panels.flatMap((p) => p.patches).some((x) => x.gid === 'axes_0.lines_2')).toBe(false)
    expect(plan.owned).toHaveLength(4)
  })

  it('用户手改过的不覆盖；配色自己上次写的照常换', () => {
    const pal = paletteById(DEFAULT_SCHEME)!
    const { panels: p0, manifestOf: m } = twoPanels([{ gid: 'axes_0.lines_0', prop: 'color', value: '#ff0000' }])
    const r = reconcile(emptyTable(DEFAULT_SCHEME), collectSeries(p0, m))
    const first = planPalette(r.table, pal, r.groups, p0)
    expect(first.skipped.map((s) => [s.panelId, s.gid])).toEqual([['B', 'axes_0.lines_0']])
    // 把第一次的计划落下去，再换方案
    const applied = p0.map((p) => {
      const pp = first.panels.find((x) => x.panel.id === p.id)
      return pp ? { ...p, overrides: [...p.overrides, ...pp.patches] } : p
    })
    const table = { ...r.table, owned: first.owned }
    const oi = paletteById('okabe-ito')!
    const second = planPalette(setScheme(table, 'okabe-ito'), oi, r.groups, applied)
    expect(second.skipped.map((s) => [s.panelId, s.gid])).toEqual([['B', 'axes_0.lines_0']])
    expect(second.panels.find((x) => x.panel.id === 'A')!.patches.map((x) => x.value)).toEqual([oi.colors[0], oi.colors[1]])
    // 用户在属性页里改了配色写下的那一格：从此归用户
    const edited = applied.map((p) =>
      p.id === 'A' ? { ...p, overrides: [...p.overrides, { gid: 'axes_0.lines_1', prop: 'color', value: '#123456' }] } : p,
    )
    const third = planPalette({ ...table, scheme: 'okabe-ito' }, oi, r.groups, edited)
    expect(third.skipped.map((s) => [s.panelId, s.gid])).toContainEqual(['A', 'axes_0.lines_1'])
    expect(third.owned.some((o) => o.panel === 'A' && o.gid === 'axes_0.lines_1')).toBe(false)
  })

  it('画布样式写下、仍归样式的那一格不算手改，配色照写', () => {
    const pal = paletteById(DEFAULT_SCHEME)!
    const { panels, manifestOf: m } = twoPanels([{ gid: 'axes_0.lines_0', prop: 'color', value: '#ff0000' }])
    const r = reconcile(emptyTable(DEFAULT_SCHEME), collectSeries(panels, m))
    const plan = planPalette(r.table, pal, r.groups, panels, (p, gid) => p.id === 'B' && gid === 'axes_0.lines_0')
    expect(plan.skipped).toEqual([])
    expect(plan.panels.find((x) => x.panel.id === 'B')!.patches.find((x) => x.gid === 'axes_0.lines_0')!.value).toBe(pal.colors[1])
  })

  it('色数不够的组不上色，计划里列出来', () => {
    const p = [panel('A')]
    const ms = manifestOf(['a', 'b', 'c', 'd'].map((n, i) => series(`l${i}`, n)))
    const r = reconcile(emptyTable('tol.high-contrast'), collectSeries(p, () => ms))
    const plan = planPalette(r.table, paletteById('tol.high-contrast')!, r.groups, p)
    expect(plan.uncolored).toEqual(['k4'])
    expect(plan.panels[0].patches).toHaveLength(3)
  })
})

describe('目录', () => {
  it('期刊风格色板带 copyleft 标记，其余都是宽松许可', () => {
    const gpl = PALETTES.filter((p) => p.copyleft)
    expect(gpl.map((p) => p.source)).toEqual(gpl.map(() => 'ggsci'))
    expect(PALETTES.filter((p) => !p.copyleft).every((p) => !p.license.startsWith('GPL'))).toBe(true)
    if (process.env.TAVOTTO_NO_COPYLEFT_PALETTES?.trim() === '1') {
      // 排除构建：产物里一个 GPL 方案都没有
      expect(gpl).toEqual([])
      expect(paletteById('ggsci.npg')).toBeNull()
    } else {
      expect(gpl.length).toBe(6)
      expect(paletteById('ggsci.npg')?.styleOf).toBe('Nature Publishing Group')
    }
    expect(paletteById('tol.bright')?.neutral).toBe('#BBBBBB')
  })

  it('构建开关 TAVOTTO_NO_COPYLEFT_PALETTES=1 只在加载那份 GPL 目录时换成空表', () => {
    expect(copyleftExcluded({})).toBe(false)
    expect(copyleftExcluded({ TAVOTTO_NO_COPYLEFT_PALETTES: '1' })).toBe(true)
    expect(copyleftExcluded({ TAVOTTO_NO_COPYLEFT_PALETTES: '0' })).toBe(false)
    expect(isCopyleftCatalog('/repo/src/tavotto/profiles/palettes_copyleft.json?import')).toBe(true)
    expect(isCopyleftCatalog('C:\\repo\\src\\tavotto\\profiles\\palettes_copyleft.json')).toBe(true)
    expect(isCopyleftCatalog('/repo/src/tavotto/profiles/palettes.json')).toBe(false)
    // 开关没开：插件什么都不接管（走正常加载）；开着也不碰别的文件
    expect(noCopyleftPalettes({}).load('/repo/src/tavotto/profiles/palettes_copyleft.json')).toBeNull()
    expect(noCopyleftPalettes({ TAVOTTO_NO_COPYLEFT_PALETTES: '1' }).load('/repo/src/tavotto/profiles/palettes.json')).toBeNull()
    expect(copyleftEmpty.palettes).toEqual([])
    expect(copyleftEmpty.copyleft).toBe(true)
  })

  it('色键表随画布存取；没有它的旧画布逐字不变', () => {
    const base = { schema: 2, name: 'F', page: { w: 180, h: 120 }, objects: [], guides: [] } as unknown as FigureDocument
    const colorKeys = { version: 1 as const, scheme: 'okabe-ito', keys: [{ key: 'k1', slot: 0, identities: ['l1:0123456789abcdef'] }], owned: [] }
    expect(canvasToDoc(docToCanvas({ ...base, colorKeys }, 'c1')).colorKeys).toEqual(colorKeys)
    expect(Object.keys(docToCanvas(base, 'c1'))).not.toContain('colorKeys')
  })

  it('取色面板的 Okabe–Ito 就是目录里那一份', () => {
    expect(OKABE_ITO).toEqual(paletteById('okabe-ito')!.colors.map((c) => c.toLowerCase()))
    expect(OKABE_ITO[7]).toBe('#000000')
  })
})
