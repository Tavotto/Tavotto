/**
 * 安全自动修复的看护（ADR 0030 / 0080）。
 *
 * 面板内部的问题由后端事务修（计划、真实渲染、裁决都在 `engine/specfix.py`，
 * 真链路用例在 `tests/test_specfix_real.py`）；这里盯的是前端这一侧的承诺：
 *
 * * 发给后端的是**这张图此刻的**全量列表、页面缩放比、这份文档的规范、点名的问题；
 * * 后端说通过，才把它回的那份列表**一次 commit** 写进文档（⌘Z 一次撤回）；
 * * 后端说不通过、出错、或等待期间文档被改过：**文档一个字不改**，并说出原因；
 * * 「全部处理」不含建议档，只修本画布；
 * * 画布标注的字号与页宽仍在前端算（没有渲染这一步）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { SpecFixResponse } from './api'
import { setEngineTransport } from './engineTransport'
import { loadProfile } from './profile'
import { fixOptions, fixRoute, planFix } from './issueFix'
import { validateCanvas, type ValidationIssue } from './validation'
import { applyIssueFix, applyIssueFixes, batchable } from '@/store/issueFixActions'
import { useAssetStore } from '@/store/assetStore'
import { useProfileStore } from '@/store/profileStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type PanelObject } from '@/types/document'

const engineSpecfix = vi.fn()
// 修完 / 丢弃时会触发重渲染；挂起就好，但要记下发了什么
const engineRender = vi.fn((..._args: unknown[]) => new Promise(() => {}))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineSpecfix: (...args: unknown[]) => engineSpecfix(...args),
  engineRender: (...args: unknown[]) => engineRender(...args),
}))

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

/** 刻度 6 pt（低于绝对下限）、朝外（规范要朝内）、字体 DejaVu Sans（不在白名单）、轴标题常规字重（建议加粗） */
const smallTick = manifest([
  {
    gid: 'axes_0.xticks',
    role: 'ticks',
    label: 'X 刻度文字',
    editable: [
      { prop: 'fontsize', type: 'number', value: 6 },
      { prop: 'direction', type: 'enum', value: 'out' },
      { prop: 'fontfamily', type: 'enum', value: 'DejaVu Sans' },
    ],
  },
  {
    gid: 'axes_0.xlabel',
    role: 'axis_label',
    label: 'X 轴标题',
    editable: [
      { prop: 'fontsize', type: 'number', value: 9 },
      { prop: 'weight', type: 'enum', value: 'normal' },
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

const FIXED = [{ gid: 'axes_0.xticks', prop: 'fontsize', value: 8.5 }]

/** 后端的一份「通过」：回最终的全量列表 */
const passed = (patches: SpecFixResponse["patches"] = FIXED, over: Partial<SpecFixResponse> = {}): SpecFixResponse => ({
  ok: true,
  exit: 'done',
  patches,
  changes: [],
  skipped: [],
  unresolved: [],
  blocking: [],
  adjustments: [],
  ...over,
})

const refused = (exit: string): SpecFixResponse => ({ ...passed([]), ok: false, exit })

const overridesOf = (id = 'p1') =>
  (useDocumentStore.getState().doc.objects.find((o) => o.id === id) as PanelObject).overrides

const floorIssue = () => issuesNow().find((i) => i.ruleCode === 'font-below-absolute-floor')!

beforeEach(() => {
  engineSpecfix.mockReset()
  engineRender.mockClear()
})

afterEach(() => {
  setEngineTransport(null)
})

describe('路由：面板内部走后端，画布层在前端', () => {
  it('面板里的字号 / 字体 / 朝向都能修，而且都走后端（前端不再算面板的计划）', async () => {
    await seed()
    const doc = useDocumentStore.getState().doc
    for (const code of ['font-below-absolute-floor', 'font-family-substituted', 'tick-direction']) {
      const issue = issuesNow().find((i) => i.ruleCode === code)!
      expect(issue, code).toBeTruthy()
      expect(issue.fixKind, code).toBe('safe_auto')
      expect(fixRoute(issue, doc), code).toBe('engine')
      expect(planFix(issue, profile, doc), code).toBeNull()
    }
  })

  it('色图 / 越界 / 重叠 / 隐藏 / 缺素材依旧不给「修复」', async () => {
    await seed()
    const doc = useDocumentStore.getState().doc
    const any = issuesNow()[0]
    for (const code of ['discouraged-colormap', 'out-of-page', 'overlap', 'hidden', 'missing-asset']) {
      const fake = { ...any, ruleCode: code } as ValidationIssue
      expect(fixRoute(fake, doc), code).toBeNull()
    }
  })

  it('没连脚本的面板不走后端（没有引擎会话）', async () => {
    await seed()
    const issue = issuesNow().find((i) => i.ruleCode === 'tick-direction')!
    useDocumentStore.getState().commit(literal('断开'), (d) => {
      ;(d.objects[0] as PanelObject).script = null
    })
    expect(fixRoute(issue, useDocumentStore.getState().doc)).toBeNull()
  })
})

describe('发给后端的是什么', () => {
  it('此刻的全量列表、页面缩放比、这份规范、点名的 (规则, gid)', async () => {
    const base = [{ gid: 'axes_0.title', prop: 'text', value: '改过的标题' }]
    await seed([panel({ w: 40, h: 30, overrides: base })])
    engineSpecfix.mockResolvedValue(passed([...base, ...FIXED]))
    await applyIssueFix(floorIssue())
    expect(engineSpecfix).toHaveBeenCalledTimes(1)
    const [id, patches, scale, prof, only] = engineSpecfix.mock.calls[0]
    expect(id).toBe('Fig1.pdf')
    expect(patches).toEqual(base)
    // 摆成 40 mm 宽（原生 80）= 缩到一半：后端要按读者量到的 pt 算
    expect(scale).toBeCloseTo(0.5, 9)
    expect(prof).toEqual(profile)
    expect(only).toEqual([{ rule: 'font-below-absolute-floor', gid: 'axes_0.xticks' }])
  })
})

describe('通过才写，而且只写一次', () => {
  it('后端回的整张列表原样换上；一条历史，⌘Z 一次全回来', async () => {
    await seed()
    engineSpecfix.mockResolvedValue(passed())
    const past = useDocumentStore.getState().past.length
    const res = await applyIssueFix(floorIssue())
    expect(res).toEqual({ ok: true, applied: 1, failed: [] })
    expect(overridesOf()).toEqual(FIXED)
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    useDocumentStore.getState().undo()
    expect(overridesOf()).toEqual([])
  })

  it('走的是统一 document action（dirty / autosave / undo 因此全部照常）', async () => {
    await seed()
    engineSpecfix.mockResolvedValue(passed())
    const commit = vi.spyOn(useDocumentStore.getState(), 'commit')
    await applyIssueFix(floorIssue())
    expect(commit).toHaveBeenCalledTimes(1)
    expect(commit.mock.calls[0][0]).toMatchObject({ key: 'history.fixIssue' })
    commit.mockRestore()
  })

  it('批量：两张图各跑一次事务，回来之后**一个**批事务', async () => {
    await seed([panel(), panel({ id: 'p2' })])
    engineSpecfix.mockResolvedValue(passed())
    const past = useDocumentStore.getState().past.length
    const res = await applyIssueFixes(issuesNow())
    expect(res.ok).toBe(true)
    expect(engineSpecfix).toHaveBeenCalledTimes(2)
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    expect(overridesOf('p1')).toEqual(FIXED)
    expect(overridesOf('p2')).toEqual(FIXED)
  })
})

describe('不通过：文档一个字不改，并说出原因', () => {
  it.each([
    ['constraint_conflict', 'would_worsen'],
    ['protected_changed', 'would_worsen'],
    ['not_resolved', 'not_resolved'],
    ['font_unavailable', 'font_unavailable'],
    ['something_new', 'engine_failed'],
  ])('后端退出码 %s → %s', async (exit, reason) => {
    await seed()
    engineSpecfix.mockResolvedValue(refused(exit))
    const past = useDocumentStore.getState().past.length
    const res = await applyIssueFix(floorIssue())
    expect(res.ok).toBe(false)
    expect(res.failed[0].reason).toBe(reason)
    expect(overridesOf()).toEqual([])
    expect(useDocumentStore.getState().past.length).toBe(past)
  })

  it('后端抛错（渲染失败 / 断线）= engine_failed，不改图', async () => {
    await seed()
    engineSpecfix.mockRejectedValue(new Error('boom'))
    const res = await applyIssueFix(floorIssue())
    expect(res).toMatchObject({ ok: false, reason: 'engine_failed' })
    expect(overridesOf()).toEqual([])
  })

  it('字体没装：其余照修、字体那条如实报出来，连规范要的字体名一起', async () => {
    await seed()
    engineSpecfix.mockResolvedValue(
      passed(FIXED, {
        skipped: [
          { rule: 'font-family-substituted', gid: 'axes_0.xticks', reason: 'font_unavailable' },
        ],
      }),
    )
    const res = await applyIssueFixes(issuesNow())
    expect(res.ok).toBe(true)
    expect(res.failed).toEqual([
      { reason: 'font_unavailable', count: 1, font: profile.font_family.latin },
    ])
    expect(overridesOf()).toEqual(FIXED)
  })

  it('出界放不下（no_fit）只算这一条没修，同一批别的照写', async () => {
    await seed()
    engineSpecfix.mockResolvedValue(
      passed(FIXED, {
        skipped: [{ rule: 'tick-direction', gid: 'axes_0.xticks', reason: 'no_fit' }],
      }),
    )
    const res = await applyIssueFixes(issuesNow())
    expect(res.ok).toBe(true)
    expect(res.failed).toEqual([{ reason: 'no_fit', count: 1 }])
    expect(overridesOf()).toEqual(FIXED)
  })

  it('整张图没提交时，有逐条原因的按逐条说，其余按退出码说', async () => {
    await seed()
    engineSpecfix.mockResolvedValue({
      ...refused('nothing_to_do'),
      skipped: [{ rule: 'font-below-absolute-floor', gid: 'axes_0.xticks', reason: 'no_fit' }],
    })
    const res = await applyIssueFix(floorIssue())
    expect(res).toMatchObject({ ok: false, reason: 'no_fit' })
    expect(overridesOf()).toEqual([])
  })

  it('等待期间用户改了这张图：结果丢弃，用户的改动原样留着', async () => {
    await seed()
    let release: (v: SpecFixResponse) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((r) => (release = r)))
    const pending = applyIssueFix(floorIssue())
    const mine = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 12 }]
    useDocumentStore.getState().commit(literal('用户改了'), (d) => {
      ;(d.objects[0] as PanelObject).overrides = mine
    })
    release(passed())
    const res = await pending
    expect(res).toMatchObject({ ok: false, reason: 'stale' })
    expect(overridesOf()).toEqual(mine)
  })

  it('丢弃一份后端已通过的结果时，把 worker 按这张图此刻的列表重放一遍', async () => {
    await seed()
    let release: (v: SpecFixResponse) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((r) => (release = r)))
    const pending = applyIssueFix(floorIssue())
    // 取一份别的用例没用过的列表：挂起的渲染按「文件 + 列表」占着槽位，同一个键会排队
    const mine = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 13.25 }]
    useDocumentStore.getState().commit(literal('用户改了'), (d) => {
      ;(d.objects[0] as PanelObject).overrides = mine
    })
    engineRender.mockClear()
    release(passed())
    expect(await pending).toMatchObject({ ok: false, reason: 'stale' })
    // 重放的是此刻的列表，不是后端回的候选
    expect(engineRender.mock.calls.some((c) => JSON.stringify(c[1]) === JSON.stringify(mine))).toBe(
      true,
    )
    expect(engineRender.mock.calls.some((c) => JSON.stringify(c[1]) === JSON.stringify(FIXED))).toBe(
      false,
    )
  })

  it('等待期间换了文档：丢弃，而且不拿旧文档的面板去新文档里重放', async () => {
    await seed()
    let release: (v: SpecFixResponse) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((r) => (release = r)))
    const pending = applyIssueFix(floorIssue())
    // 另一份文档里恰好也有一个 id 为 p1 的面板
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other_project')
    useDocumentStore.getState().commit(literal('另一份'), (d) => {
      d.objects = [panel({ overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'B' }] })]
    })
    engineRender.mockClear()
    release(passed())
    expect(await pending).toMatchObject({ ok: false, reason: 'stale' })
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('后端拒绝、等待期间用户又改了这张图：按此刻的列表重放（回滚盖回了旧列表）', async () => {
    await seed()
    let release: (v: SpecFixResponse) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((r) => (release = r)))
    const pending = applyIssueFix(floorIssue())
    const mine = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 13.75 }]
    useDocumentStore.getState().commit(literal('用户改了'), (d) => {
      ;(d.objects[0] as PanelObject).overrides = mine
    })
    engineRender.mockClear()
    release(refused('constraint_conflict'))
    expect(await pending).toMatchObject({ ok: false, reason: 'would_worsen' })
    expect(engineRender.mock.calls.some((c) => JSON.stringify(c[1]) === JSON.stringify(mine))).toBe(
      true,
    )
  })

  it('后端结果不确定（回程断线 / 成功体形状不对）：文档不改，按此刻的列表重放 worker', async () => {
    // 取一份别的用例没用过的列表：挂起的渲染按「文件 + 列表」占着槽位，同一个键会排队
    const base = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 14.25 }]
    await seed([panel({ overrides: base })])
    // 服务端可能已经通过、worker 停在候选上，只是回程没拿到
    engineSpecfix.mockRejectedValue(new TypeError('Failed to fetch'))
    engineRender.mockClear()
    const res = await applyIssueFix(floorIssue())
    expect(res).toMatchObject({ ok: false, reason: 'engine_failed' })
    expect(overridesOf()).toEqual(base)
    expect(engineRender.mock.calls.some((c) => JSON.stringify(c[1]) === JSON.stringify(base))).toBe(
      true,
    )
  })

  it('成功体缺了下游要读的字段（skipped）：不抛出去，按结果不确定处理并重放', async () => {
    // api 层会先拦下这种形状；这里绕过它，盯的是消费侧自己也不许在 catch 之外读空字段
    const base = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 14.75 }]
    await seed([panel({ overrides: base })])
    const { skipped: _, ...noSkipped } = refused('constraint_conflict')
    engineSpecfix.mockResolvedValue(noSkipped)
    engineRender.mockClear()
    const res = await applyIssueFix(floorIssue())
    expect(res).toMatchObject({ ok: false, reason: 'engine_failed' })
    expect(overridesOf()).toEqual(base)
    expect(engineRender.mock.calls.some((c) => JSON.stringify(c[1]) === JSON.stringify(base))).toBe(
      true,
    )
  })

  it('后端拒绝但说 worker 已作废（回滚不干净）：文档不改，按此刻的列表重放', async () => {
    const base = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 15.25 }]
    await seed([panel({ overrides: base })])
    engineSpecfix.mockResolvedValue({ ...refused('constraint_conflict'), worker_retired: true })
    engineRender.mockClear()
    const res = await applyIssueFix(floorIssue())
    expect(res).toMatchObject({ ok: false, reason: 'would_worsen' })
    expect(overridesOf()).toEqual(base)
    expect(engineRender.mock.calls.some((c) => JSON.stringify(c[1]) === JSON.stringify(base))).toBe(
      true,
    )
  })

  it('native 会话回滚不干净（只带 replay_required，worker 没作废）：同样按此刻的列表重放', async () => {
    // Codex #549 第八轮 P1：native 会话是用户自己的 Python，不杀；引擎在重放时重试还原
    const base = [{ gid: 'axes_0.xlabel', prop: 'fontsize', value: 15.5 }]
    await seed([panel({ overrides: base })])
    engineSpecfix.mockResolvedValue({
      ...refused('constraint_conflict'),
      worker_retired: false,
      replay_required: true,
    })
    engineRender.mockClear()
    const res = await applyIssueFix(floorIssue())
    expect(res).toMatchObject({ ok: false, reason: 'would_worsen' })
    expect(overridesOf()).toEqual(base)
    expect(engineRender.mock.calls.some((c) => JSON.stringify(c[1]) === JSON.stringify(base))).toBe(
      true,
    )
  })

  it('结果不确定、等待期间换了文档：不拿旧文档的面板去新文档里重放', async () => {
    await seed()
    let reject: (e: Error) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((_, r) => (reject = r)))
    const pending = applyIssueFix(floorIssue())
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other_uncertain')
    useDocumentStore.getState().commit(literal('另一份'), (d) => {
      d.objects = [panel({ overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'C' }] })]
    })
    engineRender.mockClear()
    reject(new TypeError('Failed to fetch'))
    expect(await pending).toMatchObject({ ok: false, reason: 'engine_failed' })
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('后端拒绝、这张图没被改过：不多发一次渲染', async () => {
    await seed()
    engineSpecfix.mockResolvedValue(refused('constraint_conflict'))
    engineRender.mockClear()
    await applyIssueFix(floorIssue())
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('等待期间用户缩放了这张图（override 没变）：缩放比已不是发出去的那个，丢弃', async () => {
    await seed()
    let release: (v: SpecFixResponse) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((r) => (release = r)))
    const pending = applyIssueFix(floorIssue())
    useDocumentStore.getState().commit(literal('缩放'), (d) => {
      const p = d.objects[0] as PanelObject
      p.w = 40
      p.h = 30
    })
    release(passed())
    expect(await pending).toMatchObject({ ok: false, reason: 'stale' })
    expect(overridesOf()).toEqual([])
  })

  it('等待期间换了这份文档的规范：按旧规范验出来的结果丢弃', async () => {
    await seed()
    let release: (v: SpecFixResponse) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((r) => (release = r)))
    const pending = applyIssueFix(floorIssue())
    useDocumentStore.getState().commit(literal('换规范'), (d) => {
      d.profile = { id: 'free-form-v1' } as never
    })
    release(passed())
    expect(await pending).toMatchObject({ ok: false, reason: 'stale' })
    expect(overridesOf()).toEqual([])
  })

  it('等待期间在设置里改了绑定着的那套规范（绑定没变、规则变了）：丢弃', async () => {
    await seed()
    // 自建规范 + 跟随全局：库里那条一改，这份文档的规则就跟着变，而 doc.profile 一个字不动
    const base = useProfileStore.getState().specs
    const builtin = base[0]
    const custom = {
      ...builtin,
      id: 'spec_custom',
      built_in: false,
      read_only: false,
      display_name: '我的规范',
      data: { ...builtin.data, profile_id: 'spec_custom' },
    }
    useProfileStore.setState({ specs: [...base, custom] })
    useDocumentStore.getState().commit(literal('绑规范'), (d) => {
      d.profile = { id: 'spec_custom', follow: true } as never
    })
    let release: (v: SpecFixResponse) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((r) => (release = r)))
    const pending = applyIssueFix(floorIssue())
    const before = JSON.stringify(useDocumentStore.getState().doc.profile)
    useProfileStore.setState({
      specs: [...base, { ...custom, data: { ...custom.data, absolute_min_font_size_pt: 9 } }],
    })
    release(passed())
    expect(await pending).toMatchObject({ ok: false, reason: 'stale' })
    expect(JSON.stringify(useDocumentStore.getState().doc.profile)).toBe(before)
    expect(overridesOf()).toEqual([])
    useProfileStore.setState({ specs: base })
  })

  it('混合批量等后端时改了标注字号：那条画布层计划丢弃，面板那张照写', async () => {
    await seed()
    useDocumentStore.getState().commit(literal('加标注'), (d) => {
      d.objects.push({
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
      } as never)
    })
    let release: (v: SpecFixResponse) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((r) => (release = r)))
    const all = issuesNow()
    const text = all.find((i) => i.objectRef.objectId === 't1')!
    const pending = applyIssueFixes([text, floorIssue()])
    useDocumentStore.getState().commit(literal('用户改字号'), (d) => {
      ;(d.objects.find((o) => o.id === 't1') as { sizePt: number }).sizePt = 12
    })
    release(passed())
    const res = await pending
    expect(res.ok).toBe(true)
    expect(res.failed).toEqual([{ reason: 'stale', count: 1 }])
    const t = useDocumentStore.getState().doc.objects.find((o) => o.id === 't1') as {
      sizePt: number
    }
    expect(t.sizePt).toBe(12)
    expect(overridesOf()).toEqual(FIXED)
  })

  it('上一轮还没回来时再点一次：busy，不叠第二轮', async () => {
    await seed()
    let release: (v: SpecFixResponse) => void = () => {}
    engineSpecfix.mockReturnValue(new Promise<SpecFixResponse>((r) => (release = r)))
    const issue = floorIssue()
    const first = applyIssueFix(issue)
    expect(await applyIssueFix(issue)).toMatchObject({ ok: false, reason: 'busy' })
    release(passed())
    expect((await first).ok).toBe(true)
    expect(engineSpecfix).toHaveBeenCalledTimes(1)
  })

  it('内嵌画布 / playground（装了替代传输）里没有后端事务：说清楚，不改图', async () => {
    await seed()
    setEngineTransport({ render: vi.fn() } as never)
    const res = await applyIssueFix(floorIssue())
    expect(res).toMatchObject({ ok: false, reason: 'unavailable' })
    expect(engineSpecfix).not.toHaveBeenCalled()
  })
})

describe('「全部处理」的集合', () => {
  it('不含建议档：轴标题加不加粗是口味，只在逐条点时才改', async () => {
    await seed()
    const all = issuesNow()
    const weight = all.find((i) => i.ruleCode === 'text-weight-policy')
    expect(weight?.severity).toBe('suggestion')
    expect(weight?.fixKind).toBe('safe_auto')
    const batch = batchable(all, useDocumentStore.getState().activeCanvasId)
    expect(batch.some((i) => i.ruleCode === 'text-weight-policy')).toBe(false)
    engineSpecfix.mockResolvedValue(passed())
    await applyIssueFixes(all)
    const only = engineSpecfix.mock.calls[0][4] as { rule: string }[]
    expect(only.some((o) => o.rule === 'text-weight-policy')).toBe(false)
    expect(only.length).toBe(batch.length)
  })

  it('逐条点建议档：照修', async () => {
    await seed()
    engineSpecfix.mockResolvedValue(passed())
    const weight = issuesNow().find((i) => i.ruleCode === 'text-weight-policy')!
    await applyIssueFix(weight)
    expect(engineSpecfix.mock.calls[0][4]).toEqual([
      { rule: 'text-weight-policy', gid: 'axes_0.xlabel' },
    ])
  })

  it('只动**本画布**——撤销栈是按画布换入换出的', async () => {
    await seed()
    const issue = issuesNow().find((i) => i.fixKind === 'safe_auto')!
    const elsewhere = { ...issue, objectRef: { ...issue.objectRef, canvasId: 'c_other' } }
    expect(await applyIssueFixes([elsewhere])).toMatchObject({
      ok: false,
      reason: 'no_plan',
    })
    expect(engineSpecfix).not.toHaveBeenCalled()
  })
})

describe('画布层仍在前端', () => {
  it('画布标注的字号是页面上的绝对 pt，不乘缩放，也不发后端', async () => {
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
    await applyIssueFix(issue)
    const t = useDocumentStore.getState().doc.objects[0] as { sizePt: number }
    expect(t.sizePt).toBeGreaterThan(profile.absolute_min_font_size_pt)
    expect(engineSpecfix).not.toHaveBeenCalled()
  })

  it('页宽给出单栏 / 双栏两个选项，没选之前修不了', async () => {
    await seed([panel({ w: 77, h: 60 })])
    useDocumentStore.getState().commit(literal('改页宽'), (d) => {
      d.page = { w: 77, h: 60 }
    })
    const issue = issuesNow().find((i) => i.ruleCode === 'page-width')!
    expect(issue.fixKind).toBe('user_choice')
    expect(fixOptions(issue, profile).map((o) => o.choice)).toEqual(['single', 'double'])
    // 纯计算这一层自己也要守住：没给 choice 就算不出计划
    expect(planFix(issue, profile, useDocumentStore.getState().doc)).toBeNull()
    expect(await applyIssueFix(issue)).toMatchObject({
      ok: false,
      reason: 'needs_choice',
    })
    expect(await applyIssueFix(issue, 'single')).toMatchObject({ ok: true, applied: 1 })
    expect(useDocumentStore.getState().doc.page.w).toBe(profile.widths_mm.single)
  })
})

describe('跨画布', () => {
  it('修另一张画布上的问题时先切过去', async () => {
    await seed()
    engineSpecfix.mockResolvedValue(passed())
    const first = useDocumentStore.getState().activeCanvasId
    const issue = floorIssue()
    useDocumentStore.getState().addCanvas('画布 2')
    expect(useDocumentStore.getState().activeCanvasId).not.toBe(first)
    expect((await applyIssueFix(issue)).ok).toBe(true)
    expect(useDocumentStore.getState().activeCanvasId).toBe(first)
  })

  it('修另一张画布上的问题：按**那张画布**的规范修，不是切画布之前那张的', async () => {
    await seed()
    const target = useDocumentStore.getState().activeCanvasId
    // 问题所在的画布绑 free-form-v1，另一张（切过去之前激活的那张）用默认规范
    useDocumentStore.getState().commit(literal('绑规范'), (d) => {
      d.profile = { id: 'free-form-v1' } as never
    })
    const issue = floorIssue()
    useDocumentStore.getState().addCanvas('画布 2')
    expect(useDocumentStore.getState().activeCanvasId).not.toBe(target)
    engineSpecfix.mockResolvedValue(passed())
    await applyIssueFix(issue)
    expect(useDocumentStore.getState().activeCanvasId).toBe(target)
    const sent = engineSpecfix.mock.calls[0][3] as { profile_id: string }
    expect(sent.profile_id).toBe('free-form-v1')
  })

  it('对象已经不在了就如实回 object_missing', async () => {
    await seed()
    const issue = issuesNow().find((i) => i.fixKind === 'safe_auto')!
    useDocumentStore.getState().commit(literal('删'), (d) => {
      d.objects = []
    })
    expect(await applyIssueFix(issue)).toMatchObject({
      ok: false,
      reason: 'object_missing',
    })
  })
})
