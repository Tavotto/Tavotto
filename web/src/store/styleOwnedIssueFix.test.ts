/**
 * #549 × ADR 0081 §十三 的交叉点：一键修复（#549 的后端事务，前端整张换上裁决后的 override 列表）
 * 是**用户的动作**——它改了或删了一条样式写的 override，那一条就从 `style.owned` 里注销（归用户，
 * 脚本重跑不让位）；它没动的那几条登记原样保留。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { literal } from '@/i18n'
import type { ValidationIssue } from '@/lib/validation'
import { emptyProject, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { applyIssueFix } from './issueFixActions'

const s = () => useDocumentStore.getState()

/** 后端裁决回来的整张列表 */
let verdict: { gid: string; prop: string; value: unknown }[] = []
const realFetch = globalThis.fetch
beforeEach(() => {
  globalThis.fetch = (async (input: RequestInfo | URL) =>
    String(input).includes('/api/engine/specfix')
      ? new Response(
          JSON.stringify({ ok: true, exit: 'done', patches: verdict, changes: [], skipped: [], unresolved: [], blocking: [], adjustments: [] }),
          { status: 200 },
        )
      : new Response('{}', { status: 200 })) as typeof fetch
})
afterEach(() => {
  globalThis.fetch = realFetch
})

const STYLE_X = { value: 10, base: 9 }
const STYLE_Y = { value: 10, base: 9 }

async function seed() {
  const panel: PanelObject = {
    id: 'a',
    type: 'panel',
    fileId: 'FigA',
    fileKind: 'pdf',
    script: 'FigA.py',
    name: 'FigA',
    nativeW: 80,
    nativeH: 60,
    x: 0,
    y: 0,
    w: 80,
    h: 60,
    overrides: [
      { gid: 'axes_0.xlabel', prop: 'fontsize', value: 10 },
      { gid: 'axes_0.ylabel', prop: 'fontsize', value: 10 },
    ],
  }
  const project = emptyProject()
  project.canvases[0].objects = [panel]
  project.canvases[0].style = {
    id: 's1',
    snapshot: { element: { axis_label: { fontsize: 10 } }, pt_basis: 'page' },
    owned: { 'a@FigA': { 'axes_0.xlabel': { fontsize: STYLE_X }, 'axes_0.ylabel': { fontsize: STYLE_Y } } },
  }
  await s().switchDocument(project, `d_${Math.random().toString(36).slice(2)}`)
}

const issue = (): ValidationIssue =>
  ({
    issueId: 'i1',
    ruleCode: 'font-too-small',
    severity: 'error',
    context: 'always',
    objectRef: { documentId: s().documentId, canvasId: s().activeCanvasId, objectId: 'a', gid: 'axes_0.xlabel' },
    subject: 'element',
    propertyPath: 'fontsize',
    message: literal('字号太小'),
    technicalDetails: {},
    fixKind: 'safe_auto',
  }) as unknown as ValidationIssue

const owned = () => s().doc.style?.owned?.['a@FigA']
const ov = (gid: string) => (s().doc.objects[0] as PanelObject).overrides.find((o) => o.gid === gid)?.value

describe('一键修复（#549 后端事务）与样式写的 override 登记（ADR 0081 §十三）', () => {
  it('修复改了样式写的那一条（10 → 12）：注销它；没动的那条登记保留', async () => {
    await seed()
    verdict = [
      { gid: 'axes_0.xlabel', prop: 'fontsize', value: 12 },
      { gid: 'axes_0.ylabel', prop: 'fontsize', value: 10 },
    ]
    const out = await applyIssueFix(issue())
    expect(out.ok, JSON.stringify(out)).toBe(true)
    expect(ov('axes_0.xlabel')).toBe(12)
    expect(owned()?.['axes_0.xlabel'], '修过的那一条归用户').toBeUndefined()
    expect(owned()?.['axes_0.ylabel']?.fontsize, '没动的登记原样').toEqual(STYLE_Y)
    s().undo()
    expect(owned()?.['axes_0.xlabel']?.fontsize, '撤销连登记一起回来').toEqual(STYLE_X)
  })

  it('修复删掉了样式写的那一条：它的登记一起注销；没动的保留', async () => {
    await seed()
    verdict = [{ gid: 'axes_0.ylabel', prop: 'fontsize', value: 10 }]
    const out = await applyIssueFix(issue())
    expect(out.ok, JSON.stringify(out)).toBe(true)
    expect(ov('axes_0.xlabel')).toBeUndefined()
    expect(owned()?.['axes_0.xlabel']).toBeUndefined()
    expect(owned()?.['axes_0.ylabel']?.fontsize).toEqual(STYLE_Y)
  })

  it('修复只新增了别的项（标题），样式写的两条原样：登记一条不少', async () => {
    await seed()
    verdict = [
      { gid: 'axes_0.xlabel', prop: 'fontsize', value: 10 },
      { gid: 'axes_0.ylabel', prop: 'fontsize', value: 10 },
      { gid: 'axes_0.title', prop: 'fontsize', value: 11 },
    ]
    await applyIssueFix(issue())
    expect(owned()).toEqual({ 'axes_0.xlabel': { fontsize: STYLE_X }, 'axes_0.ylabel': { fontsize: STYLE_Y } })
  })
})
