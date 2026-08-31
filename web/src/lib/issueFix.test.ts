/**
 * 安全自动修复的看护（ADR 0030）。
 *
 * 最重要的一条：**修完真的能过**。规范的下限是不含等号的（`eff <= floor`
 * 才算过不了），所以"提到正好 8 pt"根本没修好——用例把修完的文档再喂一遍
 * 求值器，那条问题必须消失。
 */
import { describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { loadProfile } from './profile'
import { fixOptions, planFix } from './issueFix'
import { validateCanvas, type ValidationIssue } from './validation'
import { applyIssueFix, applyIssueFixes } from '@/store/issueFixActions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'

const profile = loadProfile()

const panel = (over: Partial<PanelObject> = {}): PanelObject => ({
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  script: 'fig1.py',
  ...over,
})

const manifest = (els: { gid: string; role: string; label: string; editable: unknown[] }[]) => ({
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: els.map((e) => ({
    gid: e.gid,
    role: e.role,
    label: e.label,
    bbox: [0.1, 0.1, 0.5, 0.1],
    draggable: false,
    editable: e.editable,
  })),
})

const smallTick = manifest([
  {
    gid: 'axes_0.xticks',
    role: 'ticks',
    label: 'X 刻度文字',
    editable: [
      { prop: 'fontsize', type: 'number', value: 6 },
      { prop: 'direction', type: 'enum', value: 'out' },
    ],
  },
])

async function seed(objects: PanelObject[] = [panel()], m: unknown = smallTick) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_fix')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 60 }
    d.objects = objects.map((o) => ({ ...o }))
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  for (const o of objects) seedExactRender(o, m as never)
}

function issuesNow(): ValidationIssue[] {
  const s = useDocumentStore.getState()
  const r = useRenderStore.getState()
  return validateCanvas(
    { canvasId: s.activeCanvasId, canvasName: s.doc.name, doc: s.doc, profile },
    s.documentId,
    useAssetStore.getState().byId,
    { byKey: r.byKey, latest: r.latest },
  ).issues
}

/** 修完之后再查一遍：**同一个指纹**不能还在 */
function stillThere(id: string): boolean {
  // 修复写的是 override，渲染要等引擎；用例里直接把新值贴回 manifest 上，
  // 模拟"引擎按新 override 重画了一遍"
  const s = useDocumentStore.getState()
  const p = s.doc.objects.find((o) => o.type === 'panel') as PanelObject | undefined
  if (p) {
    const els = (smallTick.elements as { gid: string; editable: { prop: string; value: unknown }[] }[]).map(
      (el) => ({
        ...el,
        editable: el.editable.map((f) => {
          const hit = p.overrides.find((o) => o.gid === el.gid && o.prop === f.prop)
          return hit ? { ...f, value: hit.value } : f
        }),
      }),
    )
    seedExactRender(p, { ...smallTick, elements: els } as never)
  }
  return issuesNow().some((i) => i.issueId === id)
}

describe('字号：目标值必须落在能通过的那一侧', () => {
  it('提到规范允许的最小 0.5 档，且修完那条问题真的没了', async () => {
    await seed()
    const issue = issuesNow().find((i) => i.ruleCode === 'font-below-absolute-floor')!
    expect(issue.fixKind).toBe('safe_auto')
    const before = issue.issueId
    expect(applyIssueFix(issue, profile)).toEqual({ ok: true, applied: 1 })
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    const written = p.overrides.find((o) => o.prop === 'fontsize')!.value as number
    // 绝对下限是 8 且**不含等号**：写 8 等于没修
    expect(written).toBeGreaterThan(profile.absolute_min_font_size_pt)
    expect(stillThere(before)).toBe(false)
  })

  it('面板缩过之后按**读者量到的 pt** 反算，不直接写目标值', async () => {
    // 面板摆成 40mm 宽（原生 80mm）= 缩到一半：eff = size × 0.5
    await seed([panel({ w: 40, h: 30 })])
    const issue = issuesNow().find((i) => i.ruleCode === 'font-below-absolute-floor')!
    applyIssueFix(issue, profile)
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    const written = p.overrides.find((o) => o.prop === 'fontsize')!.value as number
    expect(written * 0.5).toBeGreaterThan(profile.absolute_min_font_size_pt)
    // 直接写目标值的话这里会是 8.5 而读者量到 4.25pt
    expect(written).toBeGreaterThan(profile.absolute_min_font_size_pt)
  })

  it('画布标注的字号是页面上的绝对 pt，不乘缩放', async () => {
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_fix_text')
    useDocumentStore.getState().commit(literal('准备'), (d) => {
      d.page = { w: 80, h: 60 }
      d.objects = [
        {
          id: 't1',
          type: 'text',
          text: '图注',
          sizePt: 5,
          bold: false,
          color: '#000000',
          align: 'left',
          x: 1,
          y: 1,
          w: 20,
          h: 6,
        },
      ]
    })
    const issue = issuesNow().find((i) => i.objectRef.objectId === 't1')!
    expect(issue.fixKind).toBe('safe_auto')
    applyIssueFix(issue, profile)
    const t = useDocumentStore.getState().doc.objects[0] as { sizePt: number }
    expect(t.sizePt).toBeGreaterThan(profile.absolute_min_font_size_pt)
  })
})

describe('确定性的枚举类修复', () => {
  it('刻度朝向写成规范要求的那个值', async () => {
    await seed()
    const issue = issuesNow().find((i) => i.ruleCode === 'tick-direction')
    if (!issue) return // 默认规范没要求朝向时这条不适用
    expect(issue.fixKind).toBe('safe_auto')
    applyIssueFix(issue, profile)
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(p.overrides.find((o) => o.prop === 'direction')!.value).toBe(
      profile.axis_policy.tick_direction,
    )
  })
})

describe('不确定的一律不自动做', () => {
  it('字体替换 / 色图 / 越界 / 重叠都不给「修复」按钮', async () => {
    await seed()
    for (const code of [
      'font-family-substituted',
      'discouraged-colormap',
      'out-of-page',
      'overlap',
      'hidden',
      'missing-asset',
    ]) {
      const fake = { ...issuesNow()[0], ruleCode: code } as ValidationIssue
      expect(planFix(fake, profile, useDocumentStore.getState().doc)).toBeNull()
    }
  })
})

describe('user_choice：两个同样合理的答案不许替用户挑', () => {
  it('页宽给出单栏 / 双栏两个选项，没选之前修不了', async () => {
    await seed([panel({ w: 77, h: 60 })])
    useDocumentStore.getState().commit(literal('改页宽'), (d) => {
      d.page = { w: 77, h: 60 }
    })
    const issue = issuesNow().find((i) => i.ruleCode === 'page-width')!
    expect(issue.fixKind).toBe('user_choice')
    expect(fixOptions(issue, profile).map((o) => o.choice)).toEqual(['single', 'double'])
    expect(applyIssueFix(issue, profile)).toEqual({ ok: false, reason: 'needs_choice' })
    expect(applyIssueFix(issue, profile, 'single')).toEqual({ ok: true, applied: 1 })
    expect(useDocumentStore.getState().doc.page.w).toBe(profile.widths_mm.single)
  })
})

describe('事务与撤销', () => {
  it('一个修复一条历史，⌘Z 一次全回来', async () => {
    await seed()
    const issue = issuesNow().find((i) => i.ruleCode === 'font-below-absolute-floor')!
    const past = useDocumentStore.getState().past.length
    applyIssueFix(issue, profile)
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    useDocumentStore.getState().undo()
    const p = useDocumentStore.getState().doc.objects[0] as PanelObject
    expect(p.overrides).toEqual([])
  })

  it('批量修复是**一个**批事务，不是 N 条历史', async () => {
    await seed([
      panel(),
      panel({ id: 'p2', x: 0, y: 0, w: 80, h: 60 }),
    ])
    const fixable = issuesNow().filter((i) => i.fixKind === 'safe_auto')
    expect(fixable.length).toBeGreaterThan(1)
    const past = useDocumentStore.getState().past.length
    const res = applyIssueFixes(fixable, profile)
    expect(res).toEqual({ ok: true, applied: fixable.length })
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    useDocumentStore.getState().undo()
    for (const o of useDocumentStore.getState().doc.objects) {
      if (o.type === 'panel') expect(o.overrides).toEqual([])
    }
  })

  it('走的是统一 document action（dirty / autosave / undo 因此全部照常）', async () => {
    await seed()
    const commit = vi.spyOn(useDocumentStore.getState(), 'commit')
    const issue = issuesNow().find((i) => i.fixKind === 'safe_auto')!
    applyIssueFix(issue, profile)
    // 不是"直接 setState 改 doc"：那样 dirty、autosave 与撤销全都不会发生
    expect(commit).toHaveBeenCalledTimes(1)
    expect(commit.mock.calls[0][0]).toMatchObject({ key: 'history.fixIssue' })
    commit.mockRestore()
  })
})

describe('跨画布', () => {
  it('修另一张画布上的问题时先切过去', async () => {
    await seed()
    const first = useDocumentStore.getState().activeCanvasId
    const issue = issuesNow().find((i) => i.fixKind === 'safe_auto')!
    useDocumentStore.getState().addCanvas('画布 2')
    expect(useDocumentStore.getState().activeCanvasId).not.toBe(first)
    expect(applyIssueFix(issue, profile)).toEqual({ ok: true, applied: 1 })
    expect(useDocumentStore.getState().activeCanvasId).toBe(first)
  })

  it('批量修复只动**本画布**——撤销栈是按画布换入换出的', async () => {
    await seed()
    const issue = issuesNow().find((i) => i.fixKind === 'safe_auto')!
    const elsewhere = { ...issue, objectRef: { ...issue.objectRef, canvasId: 'c_other' } }
    expect(applyIssueFixes([elsewhere], profile)).toEqual({ ok: false, reason: 'no_plan' })
  })

  it('对象已经不在了就如实回 object_missing', async () => {
    await seed()
    const issue = issuesNow().find((i) => i.fixKind === 'safe_auto')!
    useDocumentStore.getState().commit(literal('删'), (d) => {
      d.objects = []
    })
    expect(applyIssueFix(issue, profile)).toEqual({ ok: false, reason: 'object_missing' })
  })
})
