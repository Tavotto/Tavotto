/**
 * ADR 0098 §三 的整条前端链：打开升级前的排版 → 面板保持原样（补了 figsize 那条）→ 精确渲染
 * 回来报「脚本另有图幅」→ `adoptScriptFrame` 一次提交 → 新图幅的渲染回来之后图幅同步器不再
 * 挪它 → ⌘Z 回到切换之前的逐字节同一份。
 */
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { useEngineSync } from '@/hooks/useEngineSync'
import { seedExactRender } from '@/test/renderFixtures'
import { useRenderStore } from '@/store/renderStore'
import { useDocumentStore } from '@/store/documentStore'
import { adoptScriptFrame } from '@/store/actions'
import { LEGACY_FRAME_OVERRIDE, type ManifestFrame } from '@/lib/figureFrame'
import type { PanelObject, ProjectDocument } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}

const FRAME: ManifestFrame = {
  source: 'savefig',
  active: false,
  figsize_mm: [80, 57.6],
  savefig_mm: [0.43, 2.08, 73.49, 57.79],
}

/** 升级前存下的排版：面板没有 `figureFrame` 记号，带着一处图内修改 */
function legacyProject(): ProjectDocument {
  const p = {
    id: 'p1',
    type: 'panel',
    fileId: 'Fig1_kinetics.pdf',
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 57.6,
    x: 10,
    y: 14,
    w: 80,
    h: 57.6,
    script: 'fig1_kinetics.py',
    overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'Kinetics' }],
  } as PanelObject
  return {
    schema: 3,
    project: { id: 'proj', name: 'Legacy' },
    canvases: [{ id: 'c1', name: 'Figure 1', page: { w: 180, h: 100 }, objects: [p], guides: [] }],
    activeCanvasId: 'c1',
    createdAt: 1,
    updatedAt: 1,
  }
}

const s = () => useDocumentStore.getState()
const panelNow = () => s().doc.objects[0] as PanelObject
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 50))
  })
}

let teardown: (() => Promise<void>) | null = null
beforeEach(() => {
  useRenderStore.getState().clear()
  useRenderStore.setState({ render: async () => {} })
})
afterEach(async () => {
  await teardown?.()
  teardown = null
})

it('升级前的排版：打开后原样 → 一键切换内容不动 → ⌘Z 回到原样', async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
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
  teardown = async () => {
    await act(async () => root.unmount())
    container.remove()
  }
  await act(async () => {
    await s().switchDocument(legacyProject(), 'd-legacy')
  })

  // 迁移：补上 figsize 那条，别的一个字节不动
  const opened = panelNow()
  expect(opened.overrides).toEqual([
    { gid: 'axes_0.title', prop: 'text', value: 'Kinetics' },
    LEGACY_FRAME_OVERRIDE,
  ])
  expect([opened.x, opened.y, opened.w, opened.h, opened.nativeW, opened.nativeH]).toEqual([10, 14, 80, 57.6, 80, 57.6])

  // 引擎按 figsize 画回来（与升级前同一个尺寸），并报出脚本的图幅
  await act(async () => {
    seedExactRender(opened, { stem: 'Fig1_kinetics', size_mm: [80, 57.6], elements: [], frame: FRAME })
  })
  await settle()
  const before = structuredClone(panelNow())
  expect(before.nativeW).toBe(80)

  await act(async () => {
    expect(adoptScriptFrame('p1')).toBe(true)
  })
  const after = panelNow()
  expect(after.overrides).toEqual([{ gid: 'axes_0.title', prop: 'text', value: 'Kinetics' }])
  expect([after.nativeW, after.nativeH]).toEqual([73.49, 57.79])
  // 内容不动：图内 (0,0) 在页面上仍落在原来的 (10,14)——新图幅左上角挪进去 0.43 / 2.08 mm
  expect(after.x).toBeCloseTo(10 + 0.43, 9)
  expect(after.y).toBeCloseTo(14 + 2.08, 9)
  expect(after.w).toBeCloseTo(73.49, 9)

  // 新图幅的渲染回来：size_mm 与已换好的原生图幅一致，同步器不再动它
  await act(async () => {
    seedExactRender(after, {
      stem: 'Fig1_kinetics',
      size_mm: [73.49, 57.79],
      elements: [],
      frame: { ...FRAME, active: true },
    })
  })
  await settle()
  expect(panelNow()).toEqual(after)

  // ⌘Z：一步回到切换之前
  await act(async () => s().undo())
  await settle()
  expect(panelNow()).toEqual(before)
})

it('布局版本恢复：升级前存下的检查点里的面板照样迁移（与读档同一个函数）', async () => {
  await act(async () => {
    await s().switchDocument(legacyProject(), 'd-version')
  })
  const version = structuredClone(s().doc)
  const legacyPanel = version.objects[0] as PanelObject
  delete legacyPanel.figureFrame
  legacyPanel.overrides = [{ gid: 'axes_0.title', prop: 'text', value: 'Checkpoint' }]
  const { restoreLayoutVersion } = await import('@/store/actions')
  const { literal } = await import('@/i18n')
  await act(async () => {
    restoreLayoutVersion(literal('恢复布局版本'), version)
  })
  expect(panelNow().overrides).toEqual([
    { gid: 'axes_0.title', prop: 'text', value: 'Checkpoint' },
    LEGACY_FRAME_OVERRIDE,
  ])
  expect(panelNow().figureFrame).toBe(1)
})
