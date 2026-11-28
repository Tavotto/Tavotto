/**
 * 「把 Agent 加的并进来」（ADR 0208 §五）：后端说「磁盘多出来的只有 Agent 追加的对象」时，桌面一次 commit 并进当前画布
 * （一步可撤销）、把项目文件的基线推到磁盘那一份。维护者 2026-11-25：没有要用户裁决的事就**不提示**——⌘S 撞上冲突时直接
 * 并进来再保存，回到前台时直接并进画布。
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAgentAdditions: vi.fn(),
  fetchLayoutList: vi.fn(async () => ({ names: [], modified: {} })),
  saveLayout: vi.fn(),
}))

import { ApiError, fetchAgentAdditions, saveLayout, type AgentCanvasDiff } from '@/lib/api'
import { literal } from '@/i18n'
import { mergeAgentAdditions, mergeAndReport, mergeableAgentAdditions, planAgentMerge, watchAgentAdditions } from '@/lib/agentMerge'
import { writeBoundProjectFile } from '@/store/projectSave'
import { captureSaveContext } from '@/store/saveContext'
import { setCurrentProjectId } from '@/lib/session'
import { activeProjectFile, setProjectFile, useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type ArrowObject, type CanvasObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockAdd = vi.mocked(fetchAgentAdditions)
const mockSave = vi.mocked(saveLayout)

const arrow = (id: string): ArrowObject => ({
  id,
  type: 'arrow',
  x: 10,
  y: 10,
  w: 20,
  h: 5,
  start: { rx: 0, ry: 0 },
  end: { rx: 1, ry: 1 },
  color: '#000000',
  strokePt: 0.75,
  head: 'end',
  anchor: { v: 1, by: 'agent', anchors: [{ role: 'to', panel: { id: 'figures/a.pdf', point: 'n' } }] },
})


beforeEach(async () => {
  vi.clearAllMocks()
  useProjectStore.setState({ project: { open: true, id: 'p1', document_dir: '/figs/a/tavottofile' } })
  setCurrentProjectId('p1')
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_agentmerge')
  useDocumentStore.getState().commit(literal('已有的'), (d) => {
    d.objects.push(arrow('a_mine0001'))
  })
  setProjectFile({ projectId: 'p1', name: '组图', file: 'tavottofile/组图.json', revision: 'rev-base', dirty: false })
})

afterEach(async () => {
  document.body.innerHTML = ''
  useUiStore.setState({ layoutOpen: false, status: null })
})

const diff = (over: Partial<AgentCanvasDiff> = {}): AgentCanvasDiff => ({
  id: useDocumentStore.getState().activeCanvasId,
  added: [],
  changed: [],
  deleted: [],
  base_order: ['a_mine0001'],
  order: ['a_mine0001'],
  reordered: false,
  ...over,
})

const additions = (canvasId = useDocumentStore.getState().activeCanvasId, ids = ['a_agent001', 't_agent002']) => ({
  available: true,
  revision: 'rev-disk',
  count: ids.length,
  canvases: [diff({ id: canvasId, added: ids.map(arrow), order: ['a_mine0001', ...ids] })],
})

describe('能不能并', () => {
  it('后端给了、全在当前画布、id 不撞：可以并；问的是绑定的名字与基线', async () => {
    mockAdd.mockResolvedValue(additions())
    const m = await mergeableAgentAdditions()
    expect(mockAdd).toHaveBeenCalledWith('组图', 'rev-base')
    expect(m).toMatchObject({ name: '组图', revision: 'rev-disk', count: 2 })
  })

  it('加在别的画布上：不并（撤销栈只管当前画布）', async () => {
    mockAdd.mockResolvedValue(additions('c_other0001'))
    expect(await mergeableAgentAdditions()).toBeNull()
  })

  it('id 撞上内存里已有的对象：不并', async () => {
    mockAdd.mockResolvedValue(additions(undefined, ['a_mine0001']))
    expect(await mergeableAgentAdditions()).toBeNull()
  })

  it('后端说没有：不并', async () => {
    mockAdd.mockResolvedValue({ available: false })
    expect(await mergeableAgentAdditions()).toBeNull()
  })
})

describe('并进来', () => {
  it('一次 commit 加进当前画布、一步撤销拿掉；基线推到磁盘那一份、标为待保存', async () => {
    mockAdd.mockResolvedValue(additions())
    const m = (await mergeableAgentAdditions())!
    expect(mergeAgentAdditions(m)).toBe(true)
    const ids = () => useDocumentStore.getState().doc.objects.map((o) => o.id)
    expect(ids()).toEqual(['a_mine0001', 'a_agent001', 't_agent002'])
    expect(useDocumentStore.getState().doc.objects[1].anchor?.by).toBe('agent')
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-disk', dirty: true })
    useDocumentStore.getState().undo()
    expect(ids()).toEqual(['a_mine0001'])
  })
})

describe('不提示：⌘S 撞上只多了 Agent 加的标注时直接并进来再保存', () => {
  it('第一次写回 409（磁盘是 Agent 写过的那份）→ 并进来 → 以磁盘修订号再写一次；不弹对话框', async () => {
    mockAdd.mockResolvedValue(additions())
    mockSave
      .mockRejectedValueOnce(
        new ApiError('external_change', 409, { code: 'external_change', revision: 'rev-disk', summary: null }),
      )
      .mockResolvedValueOnce({ ok: true, revision: 'rev-after', name: '组图' })
    const ok = await writeBoundProjectFile(captureSaveContext())
    expect(ok).toBe(true)
    expect(mockSave).toHaveBeenCalledTimes(2)
    expect(mockSave.mock.calls[0][2]).toBe('rev-base')
    expect(mockSave.mock.calls[1][2]).toBe('rev-disk')
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['a_mine0001', 'a_agent001', 't_agent002'])
    expect(useUiStore.getState().layoutOpen).toBe(false)
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-after', dirty: false })
  })

  it('磁盘上还有别的改动（后端说不只是 Agent 加的）：照旧打开冲突对话框，什么都不并', async () => {
    mockAdd.mockResolvedValue({ available: false })
    mockSave.mockRejectedValueOnce(
      new ApiError('external_change', 409, { code: 'external_change', revision: 'rev-other', summary: null }),
    )
    const ok = await writeBoundProjectFile(captureSaveContext())
    expect(ok).toBe(false)
    expect(mockSave).toHaveBeenCalledTimes(1)
    expect(useUiStore.getState().layoutOpen).toBe(true)
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
  })

  it('并完再撞上冲突（这期间别处又改过）：只试一次，然后照旧打开冲突对话框', async () => {
    // 第二问后端又说「有一批新的」：仍然只并一次、写两次，不进循环
    mockAdd
      .mockResolvedValueOnce(additions())
      .mockResolvedValue({ ...additions(undefined, ['a_agent003']), revision: 'rev-again' })
    const conflict = (revision: string) =>
      new ApiError('external_change', 409, { code: 'external_change', revision, summary: null })
    mockSave.mockRejectedValueOnce(conflict('rev-disk')).mockRejectedValueOnce(conflict('rev-again'))
    const ok = await writeBoundProjectFile(captureSaveContext())
    expect(ok).toBe(false)
    expect(mockSave).toHaveBeenCalledTimes(2)
    expect(useUiStore.getState().layoutOpen).toBe(true)
    expect(useDocumentStore.getState().doc.objects).toHaveLength(3)
  })
})

describe('回到前台自动并进来', () => {
  it('窗口回到前台：有 Agent 加的标注就直接并进画布（可撤销），状态栏说一句，不挂要点的按钮', async () => {
    mockAdd.mockResolvedValue(additions())
    const stop = watchAgentAdditions(1_000_000)
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(useDocumentStore.getState().doc.objects).toHaveLength(3)
    const st = useUiStore.getState()
    expect(st.status?.key).toBe('agentMerge.done')
    expect(st.statusAction).toBeNull()
    stop()
  })
})

describe('Codex #2217：回答只对发请求时那一份成立、不进手势事务、没进文档就不推基线', () => {
  it('请求途中换成了项目副本（画布 id、排版名、基线都一样）：回答属于原来那份，作废，不并', async () => {
    let resolve!: (v: unknown) => void
    mockAdd.mockReturnValue(new Promise((r) => (resolve = r)) as never)
    const canvasId = useDocumentStore.getState().activeCanvasId
    const copy = useDocumentStore.getState().buildProject()
    const pending = mergeableAgentAdditions()
    await useDocumentStore.getState().switchDocument(structuredClone(copy), 'd_copy')
    setProjectFile({ projectId: 'p1', name: '组图', file: 'tavottofile/组图.json', revision: 'rev-base', dirty: false })
    expect(useDocumentStore.getState().activeCanvasId).toBe(canvasId)
    resolve(additions(canvasId))
    expect(await pending).toBeNull()
  })

  it('手势进行中：不并、基线不动（取消手势不会连标注一起撤掉）', async () => {
    mockAdd.mockResolvedValue(additions())
    const m = (await mergeableAgentAdditions())!
    useDocumentStore.getState().beginTxn(literal('拖动'))
    expect(mergeAgentAdditions(m)).toBe(false)
    useDocumentStore.getState().endTxn({ discard: true })
    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-base' })
  })

  it('写入被挡住（只读窗口）：commit 什么都没做 → 不推基线、不报成功', async () => {
    mockAdd.mockResolvedValue(additions())
    const m = (await mergeableAgentAdditions())!
    const real = useDocumentStore.getState().commit
    useDocumentStore.setState({ commit: () => {} })
    try {
      expect(mergeAgentAdditions(m)).toBe(false)
    } finally {
      useDocumentStore.setState({ commit: real })
    }
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-base' })
  })
})

describe('ADR 0212：Agent 改 / 删 / 重排过——按字段三方合并，桌面碰过同一处就不并', () => {
  const mine = () => arrow('a_mine0001')
  const edited = (patch: Partial<ArrowObject>) => ({ ...mine(), ...patch }) as ArrowObject
  const ids = (objs: readonly CanvasObject[]) => objs.map((o) => o.id)

  it('Agent 改了线宽、桌面改了位置：两样都留下', () => {
    const memory = [edited({ x: 40 })]
    const out = planAgentMerge(memory, null, diff({ changed: [{ before: mine(), after: edited({ strokePt: 1 }) }] }))!
    expect(out.objects[0]).toMatchObject({ x: 40, strokePt: 1 })
  })

  it('两边改了同一个字段：不并', () => {
    const memory = [edited({ strokePt: 0.5 })]
    expect(planAgentMerge(memory, null, diff({ changed: [{ before: mine(), after: edited({ strokePt: 1 }) }] }))).toBeNull()
  })

  it('Agent 去掉了一个字段（缺省不写）：桌面那份也去掉', () => {
    const before = edited({ name: '红箭头' })
    const after = { ...before } as Partial<ArrowObject>
    delete after.name
    const out = planAgentMerge([before], null, diff({ changed: [{ before, after: after as ArrowObject }] }))!
    expect('name' in out.objects[0]).toBe(false)
  })

  it('Agent 删了、桌面改过它：删 / 改冲突，不并；桌面没碰过：删掉；桌面也删了：一致', () => {
    const d = diff({ deleted: [mine()], order: [] })
    expect(planAgentMerge([edited({ x: 40 })], null, d)).toBeNull()
    expect(planAgentMerge([mine()], null, d)!.objects).toEqual([])
    expect(planAgentMerge([], null, d)!.objects).toEqual([])
  })

  it('Agent 改了、桌面删了：不并', () => {
    expect(planAgentMerge([], null, diff({ changed: [{ before: mine(), after: edited({ strokePt: 1 }) }] }))).toBeNull()
  })

  it('Agent 重排过：桌面没动顺序就照 Agent 的；桌面新建的对象跟着它在桌面里的前一个；桌面也重排过就不并', () => {
    const b = arrow('a_b0000001')
    const c = arrow('a_c0000001')
    const d = diff({ base_order: ['a_mine0001', b.id, c.id], order: [c.id, 'a_mine0001', b.id], reordered: true })
    const mineNew = arrow('a_new00001')
    expect(ids(planAgentMerge([mine(), b, mineNew, c], null, d)!.objects)).toEqual([
      c.id,
      'a_mine0001',
      b.id,
      mineNew.id,
    ])
    expect(planAgentMerge([b, mine(), c], null, d)).toBeNull()
  })

  it('新加的对象插在 Agent 那份里它前面那个之后（不总是追加到末尾）', () => {
    const b = arrow('a_b0000001')
    const out = planAgentMerge(
      [mine(), b],
      null,
      diff({ base_order: ['a_mine0001', b.id], order: ['a_mine0001', 'p_new00001', b.id], added: [arrow('p_new00001')] }),
    )!
    expect(ids(out.objects)).toEqual(['a_mine0001', 'p_new00001', b.id])
  })

  it('布局组：桌面还是基线那份才换成 Agent 的；空数组与没有同义', () => {
    const g = { id: 'g1', kind: 'row' as const, order: ['a_mine0001'], gap: 4, align: 'start' as const }
    const d = diff({ layout_groups: { before: [g], after: null } })
    expect(planAgentMerge([mine()], [g], d)!.layoutGroups).toBeNull()
    expect(planAgentMerge([mine()], [{ ...g, gap: 8 }], d)).toBeNull()
    expect(planAgentMerge([mine()], [], diff({ layout_groups: { before: null, after: [g] } }))!.layoutGroups).toEqual([g])
  })

  it('整条路：回到前台把改动并进来，一步撤销回去；状态栏说「改动」', async () => {
    mockAdd.mockResolvedValue({
      available: true,
      revision: 'rev-disk',
      count: 1,
      canvases: [diff({ changed: [{ before: mine(), after: edited({ strokePt: 1 }) }] })],
    })
    const m = (await mergeableAgentAdditions())!
    expect(m).toMatchObject({ onlyAdded: false, count: 1 })
    expect(mergeAgentAdditions(m)).toBe(true)
    expect((useDocumentStore.getState().doc.objects[0] as ArrowObject).strokePt).toBe(1)
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-disk', dirty: true })
    useDocumentStore.getState().undo()
    expect((useDocumentStore.getState().doc.objects[0] as ArrowObject).strokePt).toBe(0.75)
  })

  it('问完之后桌面又改了同一字段：commit 那一刻重算，不并、基线不动', async () => {
    mockAdd.mockResolvedValue({
      available: true,
      revision: 'rev-disk',
      count: 1,
      canvases: [diff({ changed: [{ before: mine(), after: edited({ strokePt: 1 }) }] })],
    })
    const m = (await mergeableAgentAdditions())!
    useDocumentStore.getState().commit(literal('改线宽'), (d) => {
      ;(d.objects[0] as ArrowObject).strokePt = 0.5
    })
    expect(mergeAgentAdditions(m)).toBe(false)
    expect(activeProjectFile()).toMatchObject({ revision: 'rev-base' })
  })
})

describe('ADR 0217 §四：子图 overrides 按 (gid, prop) 逐条三方合并，冲突桌面赢、不挡并入', () => {
  type Ovr = { gid: string; prop: string; value: unknown; identity?: string }
  const o = (gid: string, prop: string, value: unknown, identity?: string): Ovr => ({ gid, prop, value, ...(identity ? { identity } : {}) })
  const panel = (overrides: Ovr[], extra: Record<string, unknown> = {}) =>
    ({ id: 'p_fig00001', type: 'panel', x: 0, y: 0, w: 80, h: 60, fileId: 'f1', fileKind: 'pdf', nativeW: 80, nativeH: 60, script: 'fig.py', overrides, ...extra }) as unknown as CanvasObject
  const run = (base: Ovr[], theirs: Ovr[], mine: Ovr[], extra: { base?: Record<string, unknown>; theirs?: Record<string, unknown>; mine?: Record<string, unknown> } = {}) => {
    const d = diff({
      base_order: ['p_fig00001'],
      order: ['p_fig00001'],
      changed: [{ before: panel(base, extra.base), after: panel(theirs, extra.theirs) }],
    })
    const out = planAgentMerge([panel(mine, extra.mine)], null, d)
    return out && { ovr: (out.objects[0] as unknown as { overrides: Ovr[] }).overrides, conflicts: out.overrideConflicts, panel: out.objects[0] }
  }

  it('Agent 改的几条桌面没动过：换成 Agent 的，桌面独有的保留', () => {
    const got = run([o('g1', 'x', 1)], [o('g1', 'x', 2, 'id-a'), o('g2', 'y', 5, 'id-b')], [o('g1', 'x', 1), o('g9', 'z', 7)])!
    expect(got.ovr).toEqual([o('g1', 'x', 2, 'id-a'), o('g9', 'z', 7), o('g2', 'y', 5, 'id-b')])
    expect(got.conflicts).toEqual([])
  })

  it('同一条两边都改了：桌面赢、其余照并，不挡并入；冲突键记下来', () => {
    const got = run([o('g1', 'x', 1), o('g2', 'y', 1)], [o('g1', 'x', 2), o('g2', 'y', 2)], [o('g1', 'x', 9, 'id-d'), o('g2', 'y', 1)])!
    expect(got.ovr).toEqual([o('g1', 'x', 9, 'id-d'), o('g2', 'y', 2)])
    expect(got.conflicts).toEqual([{ panel: 'p_fig00001', keys: ['g1|x'] }])
  })

  it('Agent 去掉一条：桌面没动才去掉；桌面改过就留桌面的', () => {
    expect(run([o('g1', 'x', 1)], [], [o('g1', 'x', 1)])!.ovr).toEqual([])
    const kept = run([o('g1', 'x', 1)], [], [o('g1', 'x', 3)])!
    expect(kept.ovr).toEqual([o('g1', 'x', 3)])
    expect(kept.conflicts[0].keys).toEqual(['g1|x'])
  })

  it('桌面去掉了、Agent 改了同一条：桌面赢（仍是去掉）', () => {
    expect(run([o('g1', 'x', 1)], [o('g1', 'x', 2)], [])!.ovr).toEqual([])
  })

  it('两边加了同一条且值相同：不算冲突', () => {
    const got = run([], [o('g1', 'x', 2)], [o('g1', 'x', 2)])!
    expect(got.ovr).toEqual([o('g1', 'x', 2)])
    expect(got.conflicts).toEqual([])
  })

  it('子图的别的字段照旧：桌面没碰就并，碰过同一字段整体不并', () => {
    const ok = run([o('g1', 'x', 1)], [o('g1', 'x', 2)], [o('g1', 'x', 1)], { theirs: { w: 90 } })!
    expect(ok.panel).toMatchObject({ w: 90 })
    expect(run([o('g1', 'x', 1)], [o('g1', 'x', 2)], [o('g1', 'x', 1)], { theirs: { w: 90 }, mine: { w: 70 } })).toBeNull()
  })
})

describe('维护者 2026-11-26：含删除的那次并入，状态栏那句话旁边挂「撤销」', () => {
  const deleting = () => ({
    available: true,
    revision: 'rev-disk',
    count: 1,
    canvases: [diff({ deleted: [arrow('a_mine0001')], order: [] })],
  })

  it('删了东西：带「撤销」，点了把这次并入撤回、东西回来', async () => {
    mockAdd.mockResolvedValue(deleting())
    const m = (await mergeableAgentAdditions())!
    expect(mergeAndReport(m)).toBe(true)
    expect(useDocumentStore.getState().doc.objects).toHaveLength(0)
    const st = useUiStore.getState()
    expect(st.status?.key).toBe('agentMerge.doneDeleted')
    expect(st.statusAction?.label.key).toBe('agentMerge.undo')
    st.statusAction!.run()
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['a_mine0001'])
  })

  it('之后又改了别的：点「撤销」不去撤别人的那一步，只收起提示', async () => {
    mockAdd.mockResolvedValue(deleting())
    const m = (await mergeableAgentAdditions())!
    mergeAndReport(m)
    const action = useUiStore.getState().statusAction!
    useDocumentStore.getState().commit(literal('又加一个'), (d) => {
      d.objects.push(arrow('a_later001'))
    })
    action.run()
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['a_later001'])
    expect(useUiStore.getState().status).toBeNull()
  })

  it('没删东西（只改 / 只加）：不挂按钮，照旧一句话', async () => {
    mockAdd.mockResolvedValue({
      available: true,
      revision: 'rev-disk',
      count: 1,
      canvases: [diff({ changed: [{ before: arrow('a_mine0001'), after: { ...arrow('a_mine0001'), strokePt: 1 } }] })],
    })
    mergeAndReport((await mergeableAgentAdditions())!)
    expect(useUiStore.getState().statusAction).toBeNull()
  })
})

