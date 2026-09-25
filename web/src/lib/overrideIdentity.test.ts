/**
 * override 的目标身份（ADR 0083，QA SCI-03-B1）：前端这一半。
 *
 * 引擎按 patch 的 `identity` 核对 gid 此刻指向的是不是写编辑时那个对象；前端要做对的
 * 只有「抄得对」——新写 / 改值的那一条抄**写它时用户看着的那一版** manifest 的身份，
 * 有来历的身份（恢复回来的）不许按此刻重抄，没动过的条目不碰。
 */
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { enablePatches, produceWithPatches } from 'immer'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { useEngineSync } from '@/hooks/useEngineSync'
import { setOverride, setOverrides } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type FigureDocument, type PanelObject, type PanelOverride } from '@/types/document'
import { isStaleIdentityOverride, stampOverrideIdentities } from './overrideIdentity'

enablePatches()

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}

const ALPHA = 'l1:aaaaaaaaaaaaaaaa'
const BETA = 'l1:bbbbbbbbbbbbbbbb'

/** 写编辑那一刻的 manifest：lines_0 = alpha、lines_1 = beta、lines_2 没有显式 label。 */
const manifest = (first = ALPHA, second = BETA): Manifest =>
  ({
    stem: 'Fig1',
    size_mm: [80, 60],
    elements: [
      { gid: 'axes_0.lines_0', role: 'line', label: 'a', identity: first, bbox: [0, 0, 1, 1], editable: [], draggable: false },
      { gid: 'axes_0.lines_1', role: 'line', label: 'b', identity: second, bbox: [0, 0, 1, 1], editable: [], draggable: false },
      { gid: 'axes_0.lines_2', role: 'line', label: 'c', bbox: [0, 0, 1, 1], editable: [], draggable: false },
    ],
  }) as unknown as Manifest

const panel = (overrides: PanelOverride[]): PanelObject =>
  ({
    id: 'p1',
    type: 'panel',
    x: 0,
    y: 0,
    w: 80,
    h: 60,
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    script: 'fig1.py',
    overrides,
  }) as PanelObject

const doc = (overrides: PanelOverride[]): FigureDocument => {
  const d = structuredClone(emptyProject().canvases[0]) as unknown as FigureDocument
  return { ...d, objects: [panel(overrides)] } as FigureDocument
}

/** 一次提交 + 抄写，回提交之后那一版面板的 overrides。 */
function commitAndStamp(
  before: PanelOverride[],
  edit: (o: PanelOverride[]) => PanelOverride[],
  m: Manifest | null = manifest(),
): PanelOverride[] {
  const base = doc(before)
  const [next] = produceWithPatches(base, (d) => {
    const p = d.objects[0] as PanelObject
    p.overrides = edit(p.overrides)
  })
  const [stamped] = produceWithPatches(next, (d) => stampOverrideIdentities(d, base, next, () => m))
  return (stamped.objects[0] as PanelObject).overrides
}

describe('stampOverrideIdentities：抄写规则', () => {
  it('新写的一条抄上此刻那个 gid 的身份', () => {
    const out = commitAndStamp([], () => [{ gid: 'axes_0.lines_0', prop: 'color', value: '#f0f' }])
    expect(out).toEqual([{ gid: 'axes_0.lines_0', prop: 'color', value: '#f0f', identity: ALPHA }])
  })

  it('没有显式 label 的元素不带身份（按位置匹配，与引入前一致）', () => {
    const out = commitAndStamp([], () => [{ gid: 'axes_0.lines_2', prop: 'color', value: '#f0f' }])
    expect(out[0]).not.toHaveProperty('identity')
  })

  it('没动过的条目一个字都不碰：旧文档里不带身份的照旧不带', () => {
    const legacy = { gid: 'axes_0.lines_1', prop: 'color', value: '#123' }
    const out = commitAndStamp([legacy], (o) => [...o, { gid: 'axes_0.lines_0', prop: 'alpha', value: 0.5 }])
    expect(out[0]).toEqual(legacy)
    expect(out[1].identity).toBe(ALPHA)
  })

  it('原地改值（upsert 的展开写法继承了旧身份）：按此刻的对象重抄', () => {
    // 脚本改过之后 lines_0 已经是 beta 了；用户在 beta 上改颜色，这条编辑属于 beta
    const stale = { gid: 'axes_0.lines_0', prop: 'color', value: '#f0f', identity: ALPHA }
    const out = commitAndStamp([stale], (o) => [{ ...o[0], value: '#0f0' }], manifest(BETA, ALPHA))
    expect(out[0].identity).toBe(BETA)
  })

  it('带着别的身份回来的（历史 / 版本恢复）不按此刻重抄——重抄等于把旧编辑按位置绑到新对象上', () => {
    const now = { gid: 'axes_0.lines_0', prop: 'color', value: '#000', identity: BETA }
    const restored = { gid: 'axes_0.lines_0', prop: 'color', value: '#f0f', identity: ALPHA }
    const out = commitAndStamp([now], () => [restored], manifest(BETA, ALPHA))
    expect(out[0].identity).toBe(ALPHA)
  })

  it('同值被重建成 {gid, prop, value}（filter + push）：身份照旧', () => {
    const prev = { gid: 'axes_0.lines_0', prop: 'color', value: '#f0f', identity: ALPHA }
    const out = commitAndStamp([prev], () => [{ gid: 'axes_0.lines_0', prop: 'color', value: '#f0f' }], manifest(BETA, ALPHA))
    expect(out[0].identity).toBe(ALPHA)
  })

  it('没有 manifest（从没画出来过）就不抄：没见过的对象无从抄起', () => {
    const out = commitAndStamp([], () => [{ gid: 'axes_0.lines_0', prop: 'color', value: '#f0f' }], null)
    expect(out[0]).not.toHaveProperty('identity')
  })
})

describe('isStaleIdentityOverride：与引擎同一条判据', () => {
  const m = manifest(BETA, ALPHA)
  it('gid 还在、身份对不上 = 失效', () => {
    expect(isStaleIdentityOverride({ gid: 'axes_0.lines_0', prop: 'color', value: 1, identity: ALPHA }, m)).toBe(true)
  })
  it('对得上、不带身份、不认识的方案前缀都不算', () => {
    expect(isStaleIdentityOverride({ gid: 'axes_0.lines_0', prop: 'color', value: 1, identity: BETA }, m)).toBe(false)
    expect(isStaleIdentityOverride({ gid: 'axes_0.lines_0', prop: 'color', value: 1 }, m)).toBe(false)
    expect(isStaleIdentityOverride({ gid: 'axes_0.lines_0', prop: 'color', value: 1, identity: 'x:1' }, m)).toBe(false)
  })
  it('元素此刻没有身份（label 被删了）而编辑带着 = 失效', () => {
    expect(isStaleIdentityOverride({ gid: 'axes_0.lines_2', prop: 'color', value: 1, identity: ALPHA }, m)).toBe(true)
  })
})

describe('真实写入口：setOverride / setOverrides 经 documentStore 提交时抄写', () => {
  let unmount: () => Promise<void>

  beforeEach(async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true
    useRenderStore.getState().clear()
    useRenderStore.setState({ render: async () => {} })
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_identity')
    const p = panel([])
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.objects = [p]
    })
    seedExactRender(p, manifest())
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
      await act(async () => root.unmount())
      container.remove()
    }
  })

  afterEach(async () => {
    await unmount()
  })

  const overrides = () => (useDocumentStore.getState().doc.objects[0] as PanelObject).overrides

  it('setOverride 写下的那条带着身份；撤销一次连身份一起回去（同一条历史）', () => {
    const past = useDocumentStore.getState().past.length
    setOverride('p1', 'axes_0.lines_0', 'color', '#ff00ff', 'none')
    expect(overrides()).toEqual([{ gid: 'axes_0.lines_0', prop: 'color', value: '#ff00ff', identity: ALPHA }])
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    useDocumentStore.getState().undo()
    expect(overrides()).toEqual([])
  })

  it('setOverrides（原地 upsert）新增的一条同样带身份', () => {
    setOverrides('p1', literal('批量'), [{ gid: 'axes_0.lines_1', prop: 'linewidth', value: 3 }], 'none')
    expect(overrides()).toEqual([{ gid: 'axes_0.lines_1', prop: 'linewidth', value: 3, identity: BETA }])
  })

  it('卸下同步器之后不再抄（登记是 useEngineSync 挂上的，注销干净）', async () => {
    await unmount()
    unmount = async () => {}
    setOverride('p1', 'axes_0.lines_0', 'color', '#00ff00', 'none')
    expect(overrides()[0]).not.toHaveProperty('identity')
  })
})
