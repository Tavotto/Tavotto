/**
 * Style / Spec 与文档的关系（Session 10，ADR 0029）。
 *
 * 两条边界各一组用例，错了都会以"我改的东西撤不回来 / 保存丢了"的形态出现：
 *
 * 1. **应用样式是对图的修改** —— 一条历史，⌘Z 整体撤回，包括页面背景；
 * 2. **选规范是项目文档设置** —— 正确 dirty、带快照落盘、同步也进历史。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import { DEFAULT_PROFILE_ID } from '@/lib/profile'
import { bindingFor, builtinCatalog, resolveDocumentSpec, sameRules } from '@/lib/specBinding'
import { planStyle, type StylePreset } from '@/lib/stylePresets'
import { applyStylePlan } from './actions'
import { startAutosave, useDocumentStore } from './documentStore'
import { emptyProject, type PanelObject } from '@/types/document'

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const s = () => useDocumentStore.getState()
const entry = () => builtinCatalog().find((e) => e.id === DEFAULT_PROFILE_ID)!

/**
 * `dirty` 是**自动保存那条订阅**置的，不是 `commit` 自己置的（三档表见
 * documentStore）。不起订阅的话所有 `dirty` 判据恒假——什么都没量到。
 */
let stopAutosave: (() => void) | null = null

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  script: 'fig1.py',
  name: 'Fig1',
  nativeW: 80,
  nativeH: 60,
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  overrides: [],
}

const manifest = {
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [
    {
      gid: 'axes_0.title',
      role: 'title',
      label: '标题',
      bbox: [0, 0, 1, 0.1],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: 9 }],
    },
  ],
}

const preset: StylePreset = {
  name: '投稿用',
  element: { title: { fontsize: 12 } },
  background: '#eeeeee',
}

beforeEach(async () => {
  localStorage.clear()
  stopAutosave?.()
  await s().switchDocument(emptyProject(), 'd_style_spec')
  s().commit(literal('放入面板'), (d) => {
    d.objects = [{ ...panel }]
    d.page = { ...d.page, bg: '#ffffff' }
  })
  stopAutosave = startAutosave()
  useDocumentStore.setState({ dirty: false })
})

afterEach(() => {
  stopAutosave?.()
  stopAutosave = null
})

describe('应用样式 = 一次可撤销的文档修改', () => {
  it('override 与页面背景一起进同一条历史，⌘Z 一次全退回', () => {
    const plan = planStyle(preset, [panel], () => manifest as never, s().doc, false)
    applyStylePlan(plan, preset)

    const after = s().doc.objects[0] as PanelObject
    expect(after.overrides).toEqual([{ gid: 'axes_0.title', prop: 'fontsize', value: 12 }])
    expect(s().doc.page.bg).toBe('#eeeeee')
    expect(s().dirty).toBe(true)

    expect(s().undo()).not.toBeNull()
    const back = s().doc.objects[0] as PanelObject
    expect(back.overrides).toEqual([])
    expect(back.w).toBe(80)
    expect(s().doc.page.bg).toBe('#ffffff')
  })

  it('样式没管背景时**不动背景**（"没管"与"设成白色"不是一回事）', () => {
    const noBg: StylePreset = { name: '只改字号', element: { title: { fontsize: 12 } } }
    const plan = planStyle(noBg, [panel], () => manifest as never, s().doc, false)
    expect(plan.background).toBeUndefined()
    applyStylePlan(plan, noBg)
    expect(s().doc.page.bg).toBe('#ffffff')
  })
})

describe('选规范 = 项目文档设置', () => {
  it('写进文档、标脏、且带着当时那份规则的快照', () => {
    s().commit(literal('选规范'), (d) => {
      d.profile = bindingFor(entry())
    })
    expect(s().dirty).toBe(true)
    const bound = s().doc.profile!
    expect(bound.id).toBe(DEFAULT_PROFILE_ID)
    expect(sameRules(bound.snapshot, entry().data)).toBe(true)
    expect(bound.snapshotVersion).toBe(entry().version)
  })

  it('规范绑定进撤销历史（选错了要退得回来）', () => {
    s().commit(literal('选规范'), (d) => {
      d.profile = bindingFor(entry())
    })
    expect(s().undo()).not.toBeNull()
    expect(s().doc.profile).toBeUndefined()
  })

  it('同步到新版是**另一条**历史，且换掉的是整份快照', () => {
    const old = entry()
    s().commit(literal('选规范'), (d) => {
      d.profile = bindingFor(old)
    })
    const next = { ...old, version: '2.0.0', data: { ...old.data, min_effective_font_size_pt: 6 } }
    expect(resolveDocumentSpec(s().doc.profile, [next]).updateAvailable).toBe(true)

    s().commit(literal('同步规范'), (d) => {
      d.profile = bindingFor(next)
    })
    const resolved = resolveDocumentSpec(s().doc.profile, [next])
    expect(resolved.profile.min_effective_font_size_pt).toBe(6)
    expect(resolved.updateAvailable).toBe(false)

    s().undo()
    expect(resolveDocumentSpec(s().doc.profile, [next]).profile.min_effective_font_size_pt).toBe(8)
  })

  it('快照落进磁盘形态（序列化后还在）', () => {
    s().commit(literal('选规范'), (d) => {
      d.profile = bindingFor(entry())
    })
    const round = JSON.parse(JSON.stringify(s().doc)) as typeof s extends never ? never : any
    expect(round.profile.snapshot.min_effective_font_size_pt).toBe(8)
  })
})
