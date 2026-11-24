/**
 * 「从数据到图」画好之后，新图落的那张画布带上草稿的规范绑定（`armBindingInherit`）：
 * 只写没有绑定的新画布；不进撤销栈（胶囊上「撤销」认的 `addedForEditDepth` 不变）；撤销加图 / 重做之后绑定仍在、不留半截状态；
 * 认第一张新出现的画布、不被切到老画布骗；过期 / 换项目 / 收手都不写；克隆来的 `journalPrev` 页宽记录换成新画布自己的，「不按期刊」不会改页宽。
 */
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PanelInfo } from '@/lib/api'
import { useJournalV2Sync } from '@/hooks/useJournalV2Sync'
import { applyV2, type V2Patch } from '@/lib/journalV2'
import { useJournalV2Store } from '@/store/journalV2Store'
import { clearJournal } from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { openFastEdit, useWorkspaceStore } from '@/store/workspace'
import { emptyProject, type DocumentProfile } from '@/types/document'
import { INHERIT_WINDOW_MS, armBindingInherit } from './dataFigureInherit'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const info = (id: string): PanelInfo => ({ id, name: id, folder: '.', kind: 'pdf', native_w_mm: 80, native_h_mm: 60, mtime: 1 })
const BINDING: DocumentProfile = {
  id: 'journal-base-v1',
  journal: { widths_mm: { single: 85 } },
  journalRef: { id: 'j-a', catalogRev: 1, retrieved: '2026-11-01', label: 'Journal A' },
  journalPrev: { profile: null, pageW: 111, setW: 111 },
}
const doc = () => useDocumentStore.getState()
const profileOf = (canvasId: string) => (canvasId === doc().activeCanvasId ? doc().doc.profile : doc().canvases.find((c) => c.id === canvasId)?.profile)
const v2Patch = (): V2Patch => ({
  catalog_rev: 2,
  rules: { min_raster_dpi: { value: 450, src: 'v2:s1:journal', quote: 'At least 450 dpi.', qualifier: 'min', strength: 'required', stage: 'any' } },
  sources: { 'v2:s1:journal': { url: 'https://example.invalid/v2/guide', kind: 'html', retrieved: '2026-11-15', coverage: 'journal_specific' } },
  confirm: [],
})
const stops: (() => void)[] = []
const arm = (b = BINDING, still = () => true, now?: () => number) => {
  const off = armBindingInherit(structuredClone(b), still, now)
  stops.push(off)
  return off
}

beforeEach(async () => {
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('prefers-reduced-motion'), media: q, addEventListener() {}, removeEventListener() {} }))
  localStorage.clear()
  useWorkspaceStore.getState().clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_inherit')
  const assets = [info('one.pdf'), info('two.pdf'), info('three.pdf')]
  useAssetStore.setState({ panels: assets, byId: Object.fromEntries(assets.map((a) => [a.id, a])) })
})
afterEach(() => {
  while (stops.length) stops.pop()!()
  useWorkspaceStore.getState().clear()
  vi.unstubAllGlobals()
})

/** 画布上先有一张图（所以下一次打开会新建画布，不复用空画布） */
const seedOne = () => {
  openFastEdit('one.pdf')
  expect(doc().doc.objects.length).toBe(1)
}

describe('新画布', () => {
  it('打开新图：新画布没有绑定就写上（深拷贝）；老画布一个字节不动', () => {
    seedOne()
    const from = doc().activeCanvasId
    arm()
    openFastEdit('two.pdf')
    const to = doc().activeCanvasId
    expect(to).not.toBe(from)
    expect(doc().doc.profile).toMatchObject({ id: BINDING.id, journal: BINDING.journal, journalRef: BINDING.journalRef })
    expect(doc().doc.profile).not.toBe(BINDING)
    expect(profileOf(from)).toBeUndefined()
  })

  it('不进撤销栈：胶囊上「撤销」认的深度不变；撤销加图后空画布仍带绑定，重做回来也在', () => {
    seedOne()
    arm()
    openFastEdit('two.pdf')
    const ws = useWorkspaceStore.getState()
    expect(ws.addedForEdit).not.toBeNull()
    expect(ws.addedForEditDepth).toBe(doc().past.length) // RedrawPill：historyDepth === addedDepth 才给「撤销」
    expect(doc().past.length).toBe(1) // 新画布的历史里只有「加图」这一步：写绑定没有另占一步
    const id = doc().activeCanvasId
    doc().undo()
    expect(doc().activeCanvasId).toBe(id)
    expect(doc().doc.objects.length).toBe(0)
    expect(doc().doc.profile?.journalRef?.id).toBe('j-a')
    doc().redo()
    expect(doc().doc.objects.length).toBe(1)
    expect(doc().doc.profile?.journalRef?.id).toBe('j-a')
  })

  it('切到已有的老画布不算；之后真的新建的那张才认', () => {
    seedOne()
    const first = doc().activeCanvasId
    const other = doc().addCanvas('B')
    doc().switchCanvas(first)
    arm()
    doc().switchCanvas(other)
    expect(doc().doc.profile).toBeUndefined()
    doc().switchCanvas(first)
    openFastEdit('three.pdf')
    expect(doc().activeCanvasId).not.toBe(other)
    expect(doc().doc.profile?.journalRef?.id).toBe('j-a')
  })

  it('只认第一张新出现的画布；认过之后再新建的不带', () => {
    seedOne()
    arm()
    openFastEdit('two.pdf')
    const second = doc().activeCanvasId
    openFastEdit('three.pdf')
    expect(doc().activeCanvasId).not.toBe(second)
    expect(doc().doc.profile).toBeUndefined()
    expect(profileOf(second)?.journalRef?.id).toBe('j-a')
  })

  it('克隆来的退路页宽记录换成新画布自己的：「不按期刊」不会把页宽打回别处', () => {
    seedOne()
    arm()
    openFastEdit('two.pdf')
    const w = doc().doc.page.w
    expect(doc().doc.profile?.journalPrev).toMatchObject({ pageW: w, setW: w })
    clearJournal()
    expect(doc().doc.page.w).toBe(w)
    expect(doc().doc.profile?.journalRef).toBeUndefined()
  })
})

describe('不写的几种情形', () => {
  it('过期（窗口外才出现新画布）', () => {
    seedOne()
    let t = 1000
    arm(BINDING, () => true, () => t)
    t += INHERIT_WINDOW_MS + 1
    openFastEdit('two.pdf')
    expect(doc().doc.profile).toBeUndefined()
  })

  it('still() 为假（换过项目）', () => {
    seedOne()
    arm(BINDING, () => false)
    openFastEdit('two.pdf')
    expect(doc().doc.profile).toBeUndefined()
  })

  it('收手之后不写', () => {
    seedOne()
    const off = arm()
    off()
    openFastEdit('two.pdf')
    expect(doc().doc.profile).toBeUndefined()
  })

  it('换了文档（项目）之后出现的新画布不写', async () => {
    seedOne()
    arm()
    await doc().switchDocument(emptyProject(), 'd_other')
    doc().addCanvas('X')
    expect(doc().doc.profile).toBeUndefined()
  })
})

describe('激活的画布是空的：打开新图会直接用它（不新建）', () => {
  it('图真的落进来才写（没打开之前不动空画布），没有绑定就写上', () => {
    arm()
    expect(doc().doc.profile).toBeUndefined()
    openFastEdit('one.pdf')
    expect(doc().canvases.length).toBe(1)
    expect(doc().doc.profile?.journalRef?.id).toBe('j-a')
    expect(doc().past.length).toBe(1) // 只有加图那一步
  })

  it('已有绑定不动（只写没有绑定的）', () => {
    useDocumentStore.getState().silent((d) => {
      d.profile = { id: 'lab-spec' }
    })
    arm()
    openFastEdit('one.pdf')
    expect(doc().doc.profile).toEqual({ id: 'lab-spec' })
  })

  it('一直没落（运行没成）：什么都不留；收手之后落进来也不写', () => {
    const off = arm()
    expect(doc().doc.profile).toBeUndefined()
    off()
    openFastEdit('one.pdf')
    expect(doc().doc.profile).toBeUndefined()
  })
})

describe('与期刊 v2 自动更新同台（useJournalV2Sync）', () => {
  it('绑定里已经叠过 v2、会话里也记着同一份补丁：新画布上的同步不再追加撤销步，「撤销」仍认得出加图那一步', async () => {
    const patch = v2Patch()
    useJournalV2Store.getState().remember('j-a', patch)
    const withV2 = applyV2(BINDING as never, patch) as DocumentProfile
    seedOne()
    arm(withV2)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    function Probe() {
      useJournalV2Sync()
      return null
    }
    await act(async () => root.render(<Probe />))
    openFastEdit('two.pdf')
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(doc().doc.profile?.journalRef?.id).toBe('j-a')
    expect(useWorkspaceStore.getState().addedForEditDepth).toBe(doc().past.length)
    expect(doc().past.length).toBe(1)
    await act(async () => root.unmount())
    host.remove()
  })
})
