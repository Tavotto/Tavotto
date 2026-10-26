/**
 * AI 改名之后，画布上已有的编辑跟着换身份（ADR 0083 §五）。
 *
 * 后端报 `task.renamed = [{stem, gid, from_identity, to_identity}]`（数据逐部件没动、只是显式 label 变了），
 * 前端把该 stem 的面板上、该 gid 处旧身份的 override 改写成新身份，**一条历史**；撤销这次 AI 修改时（回应的 `renamed`）
 * 反向改回。两个方向都幂等：只改身份恰好等于起点的条目。
 */
import { enablePatches } from 'immer'
import { beforeEach, describe, expect, it } from 'vitest'

import { literal } from '@/i18n'
import { handleServerEvent } from '@/hooks/useServerEvents'
import { setCurrentProjectId } from '@/lib/session'
import {
  parseRenames,
  rebindOverrideIdentities,
  type IdentityRename,
} from '@/lib/overrideIdentity'
import { emptyProject, type PanelObject, type PanelOverride } from '@/types/document'
import { rebindRenamedIdentities } from './aiRenameRebind'
import { useAiStore } from './aiStore'
import { useAssetStore } from './assetStore'
import { useDocumentStore } from './documentStore'

enablePatches()

const OLD = 'l1:1111111111111111'
const NEW = 'l1:2222222222222222'
const OTHER = 'l1:3333333333333333'
const RENAME: IdentityRename = { stem: 'Fig1', gid: 'axes_0.lines_1', from_identity: OLD, to_identity: NEW }

const panel = (id: string, fileId: string, overrides: PanelOverride[]): PanelObject =>
  ({
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 80,
    h: 60,
    fileId,
    fileKind: 'pdf',
    nativeW: 80,
    nativeH: 60,
    script: 'fig1.py',
    overrides,
    figureFrame: 1,
  }) as PanelObject

const EDITS = (): PanelOverride[] => [
  { gid: 'axes_0.lines_1', prop: 'color', value: '#f0f', identity: OLD },
  { gid: 'axes_0.lines_1', prop: 'linewidth', value: 3, identity: OLD },
  { gid: 'axes_0.lines_0', prop: 'color', value: '#0ff', identity: OTHER }, // 别的线
  { gid: 'axes_0.lines_2', prop: 'color', value: '#00f' }, // 没有身份（按位置）
  { gid: 'axes_0.lines_1', prop: 'alpha', value: 0.5, identity: OTHER }, // 同 gid 但带着别的身份
]

const ids = (p: PanelObject) => p.overrides.map((o) => o.identity ?? null)
const active = () => useDocumentStore.getState().doc.objects as PanelObject[]

let seq = 0
/** 激活画布放 `objects`；`extraCanvas` 给了就再建一张画布放它，然后切回第一张。 */
async function load(objects: PanelObject[], extraCanvas?: PanelObject[]) {
  const store = useDocumentStore.getState()
  await store.switchDocument(emptyProject(), `d_rebind_${++seq}`)
  const first = useDocumentStore.getState().activeCanvasId
  if (extraCanvas) {
    useDocumentStore.getState().addCanvas('c2')
    useDocumentStore.getState().commit(literal('准备：第二张画布'), (d) => {
      d.objects = structuredClone(extraCanvas)
    })
    useDocumentStore.getState().switchCanvas(first)
  }
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.objects = structuredClone(objects)
  })
}

beforeEach(() => {
  setCurrentProjectId('rb-a')
  useAiStore.setState({ sessions: [] })
})

describe('rebindOverrideIdentities：纯规则', () => {
  const match = (p: PanelObject, stem: string) => p.fileId === `${stem}.pdf`

  it('只改 gid 相同且身份恰好等于起点的条目；没身份的、别的身份的、别的 gid 的不动', () => {
    const objects = [panel('p1', 'Fig1.pdf', EDITS())]
    const n = rebindOverrideIdentities(objects, [RENAME], 'forward', match)
    expect(n).toBe(2)
    expect(ids(objects[0])).toEqual([NEW, NEW, OTHER, null, OTHER])
  })

  it('反向把新身份改回旧身份；重复调用是 no-op（幂等）', () => {
    const objects = [panel('p1', 'Fig1.pdf', EDITS())]
    rebindOverrideIdentities(objects, [RENAME], 'forward', match)
    expect(rebindOverrideIdentities(objects, [RENAME], 'forward', match)).toBe(0)
    expect(rebindOverrideIdentities(objects, [RENAME], 'back', match)).toBe(2)
    expect(ids(objects[0])).toEqual([OLD, OLD, OTHER, null, OTHER])
    expect(rebindOverrideIdentities(objects, [RENAME], 'back', match)).toBe(0)
  })

  it('别的 stem 的面板不动；同一张图摆了两份的都改', () => {
    const objects = [
      panel('p1', 'Fig1.pdf', EDITS()),
      panel('p2', 'Fig2.pdf', EDITS()),
      panel('p3', 'Fig1.pdf', EDITS()),
    ]
    expect(rebindOverrideIdentities(objects, [RENAME], 'forward', match)).toBe(4)
    expect(ids(objects[1])).toEqual([OLD, OLD, OTHER, null, OTHER])
  })

  it('apply=false 只数不改（可作用在冻结对象上）', () => {
    const objects = Object.freeze([panel('p1', 'Fig1.pdf', EDITS())]) as unknown as PanelObject[]
    expect(rebindOverrideIdentities(objects, [RENAME], 'forward', match, false)).toBe(2)
    expect(ids(objects[0])).toEqual([OLD, OLD, OTHER, null, OTHER])
  })

  it('parseRenames 只收闭集形状（脏数据 / 老后端没有这个键 → 空）', () => {
    expect(parseRenames([RENAME, { stem: 'x' }, null, { ...RENAME, to_identity: OLD }, 'x'])).toEqual([RENAME])
    expect(parseRenames(undefined)).toEqual([])
  })
})

describe('rebindRenamedIdentities：文档上一条历史', () => {
  beforeEach(async () => {
    await load([panel('p1', 'Fig1.pdf', EDITS()), panel('p2', 'Fig2.pdf', EDITS())])
  })

  it('改写旧 → 新，恰好一条历史；撤销 / 重做整体回去；身份不被抄写器重抄', () => {
    const before = useDocumentStore.getState().past.length
    expect(rebindRenamedIdentities([RENAME], 'forward')).toBe(2)
    expect(ids(active()[0])).toEqual([NEW, NEW, OTHER, null, OTHER])
    expect(ids(active()[1])).toEqual([OLD, OLD, OTHER, null, OTHER]) // 别的图不动
    expect(useDocumentStore.getState().past.length).toBe(before + 1)
    useDocumentStore.getState().undo()
    expect(ids(active()[0])).toEqual([OLD, OLD, OTHER, null, OTHER])
    useDocumentStore.getState().redo()
    expect(ids(active()[0])).toEqual([NEW, NEW, OTHER, null, OTHER])
  })

  it('幂等：第二次调用不改任何东西，也不多记一条历史', () => {
    rebindRenamedIdentities([RENAME], 'forward')
    const past = useDocumentStore.getState().past.length
    const doc = useDocumentStore.getState().doc
    expect(rebindRenamedIdentities([RENAME], 'forward')).toBe(0)
    expect(useDocumentStore.getState().past.length).toBe(past)
    expect(useDocumentStore.getState().doc).toBe(doc)
  })

  it('反向改回，同样一条历史；没有改名（空列表）什么都不做', () => {
    rebindRenamedIdentities([RENAME], 'forward')
    const past = useDocumentStore.getState().past.length
    expect(rebindRenamedIdentities([], 'back')).toBe(0)
    expect(rebindRenamedIdentities([RENAME], 'back')).toBe(2)
    expect(ids(active()[0])).toEqual([OLD, OLD, OTHER, null, OTHER])
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
  })

  it('用户在改名之后又写的新编辑（已带新身份）不受反向改写之外的影响：只有等于起点的条目动', () => {
    rebindRenamedIdentities([RENAME], 'forward')
    useDocumentStore.getState().commit(
      literal('用户后来的编辑'),
      (d) => {
        ;(d.objects[0] as PanelObject).overrides.push({ gid: 'axes_0.lines_1', prop: 'zorder', value: 5, identity: NEW })
      },
    )
    rebindRenamedIdentities([RENAME], 'back')
    expect((active()[0].overrides.at(-1) as PanelOverride).identity).toBe(OLD)
    // 前向再来一次：回到新身份，全部一致
    rebindRenamedIdentities([RENAME], 'forward')
    expect(active()[0].overrides.filter((o) => o.gid === 'axes_0.lines_1' && o.identity === NEW)).toHaveLength(3)
  })
})

describe('多画布：非激活画布里的同一张图也跟着改（直接写快照，不进历史）', () => {
  it('激活画布走历史，另一张画布直接改写', async () => {
    await load([panel('p1', 'Fig1.pdf', EDITS())], [panel('q1', 'Fig1.pdf', EDITS())])
    rebindRenamedIdentities([RENAME], 'forward')
    const other = useDocumentStore.getState().canvases.find((c) => c.name === 'c2')!
    expect(ids(other.objects[0] as PanelObject)).toEqual([NEW, NEW, OTHER, null, OTHER])
    expect(ids(active()[0])).toEqual([NEW, NEW, OTHER, null, OTHER])
    rebindRenamedIdentities([RENAME], 'back')
    const back = useDocumentStore.getState().canvases.find((c) => c.name === 'c2')!
    expect(ids(back.objects[0] as PanelObject)).toEqual([OLD, OLD, OTHER, null, OTHER])
  })
})

describe('接线：ai.done 前向改写 / 撤销回应反向改写', () => {
  const session = (id: string) => ({
    id,
    project: 'rb-a',
    agent: 'codex',
    agentLabel: 'Codex',
    prompt: '把 B 曲线的图例改成 B (n=6)',
    script: 'fig1.py',
    panelId: 'p1',
    fileId: 'Fig1.pdf',
    gid: null,
    scope: 'figure' as const,
    target: '整张图',
    entries: [],
    status: 'running' as const,
    changed: false,
    diff: '',
    startedAt: 1,
  })
  const done = (sid: string, over: Record<string, unknown> = {}) =>
    handleServerEvent({
      kind: 'ai.done',
      pj: 'rb-a',
      session: sid,
      status: 'done',
      changed: true,
      diff: '@@ d',
      script: 'fig1.py',
      task: { task_type: 'figure_edit', outcome: 'completed', renamed: [RENAME] },
      ...over,
    } as never)

  beforeEach(async () => {
    await load([panel('p1', 'Fig1.pdf', EDITS())])
  })

  it('ai.done（done + changed + task.renamed）：画布编辑改到新身份，一条历史', () => {
    useAiStore.setState({ sessions: [session('s1')] })
    const past = useDocumentStore.getState().past.length
    done('s1')
    expect(ids(active()[0])).toEqual([NEW, NEW, OTHER, null, OTHER])
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
    // 重复 / 迟到的 ai.done 不会再改（终态只认第一条；即使再来也幂等）
    done('s1')
    expect(useDocumentStore.getState().past.length).toBe(past + 1)
  })

  it('没有改名 / 状态不是 done / 没有改动：不碰画布', () => {
    useAiStore.setState({ sessions: [session('s2'), session('s3'), session('s4')] })
    done('s2', { task: { task_type: 'figure_edit', outcome: 'completed' } })
    done('s3', { status: 'failed' })
    done('s4', { changed: false })
    expect(ids(active()[0])).toEqual([OLD, OLD, OTHER, null, OTHER])
  })

  it('撤销：回应带 renamed → 反向改回，且先于调用方的重建（返回 true 时已经改完）', async () => {
    useAiStore.setState({ sessions: [session('s5')] })
    done('s5')
    expect(ids(active()[0])[0]).toBe(NEW)
    const prev = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ ok: true, script: 'fig1.py', renamed: [RENAME] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch
    try {
      expect(await useAiStore.getState().revert('s5')).toBe(true)
    } finally {
      globalThis.fetch = prev
    }
    expect(ids(active()[0])).toEqual([OLD, OLD, OTHER, null, OTHER])
  })

  it('撤销回应没有 renamed（老后端 / 没改名）：不碰画布', async () => {
    useAiStore.setState({ sessions: [session('s6')] })
    const prev = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ ok: true, script: 'fig1.py' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch
    try {
      await useAiStore.getState().revert('s6')
    } finally {
      globalThis.fetch = prev
    }
    expect(ids(active()[0])).toEqual([OLD, OLD, OTHER, null, OTHER])
  })
})

describe('手势进行中到达的改名：改写自成一条历史、不被手势取消回滚（Codex #1425 P1）', () => {
  beforeEach(async () => {
    await load([panel('p1', 'Fig1.pdf', EDITS())])
  })
  const nudge = () =>
    useDocumentStore.getState().txnUpdate((d) => {
      ;(d.objects[0] as PanelObject).x = 33
    })

  it('手势开着 → 改名到达 → 手势取消：改写仍在，手势的位移回滚', () => {
    const st = useDocumentStore.getState()
    st.beginTxn(literal('拖动'))
    nudge()
    rebindRenamedIdentities([RENAME], 'forward')
    useDocumentStore.getState().endTxn({ discard: true })
    expect(ids(active()[0])).toEqual([NEW, NEW, OTHER, null, OTHER])
    expect(active()[0].x).toBe(0)
  })

  it('手势开着 → 改名到达 → 手势提交：两条独立历史，各自可撤销', () => {
    const before = useDocumentStore.getState().past.length
    useDocumentStore.getState().beginTxn(literal('拖动'))
    nudge()
    rebindRenamedIdentities([RENAME], 'forward')
    useDocumentStore.getState().endTxn()
    expect(useDocumentStore.getState().past.length).toBe(before + 2)
    expect(ids(active()[0])).toEqual([NEW, NEW, OTHER, null, OTHER])
    useDocumentStore.getState().undo() // 撤销改写
    expect(ids(active()[0])).toEqual([OLD, OLD, OTHER, null, OTHER])
    expect(active()[0].x).toBe(33)
    useDocumentStore.getState().undo() // 撤销拖动
    expect(active()[0].x).toBe(0)
  })

  it('手势开着 → 改名到达 → 手势结束前换了文档：排队的改写丢掉，不落到另一份文档上', () => {
    useDocumentStore.getState().beginTxn(literal('拖动'))
    nudge()
    rebindRenamedIdentities([RENAME], 'forward')
    useDocumentStore.setState({ documentId: 'd_other', txn: null } as never)
    expect(ids(active()[0])).toEqual([OLD, OLD, OTHER, null, OTHER])
  })
})

describe('磁盘面板的 stem：有权威 stem 就只认它（Codex #1425 P1）', () => {
  it('panel.png 注册为 panel、panel.pdf 注册为 panel-2：panel 的改名不碰 panel-2', async () => {
    const info = (id: string, stem: string) => ({ id, stem }) as never
    useAssetStore.setState({
      byId: { 'panel.png': info('panel.png', 'panel'), 'panel.pdf': info('panel.pdf', 'panel-2') },
    })
    await load([panel('a', 'panel.png', EDITS()), panel('b', 'panel.pdf', EDITS())])
    rebindRenamedIdentities([{ ...RENAME, stem: 'panel' }], 'forward')
    expect(ids(active()[0])).toEqual([NEW, NEW, OTHER, null, OTHER])
    expect(ids(active()[1])).toEqual([OLD, OLD, OTHER, null, OTHER])
    useAssetStore.setState({ byId: {} })
  })
})

describe('运行配置：只改被验证的那一种配置的面板（Codex #1425 P1）', () => {
  const runtime = (id: string, runConfig?: string): PanelObject =>
    ({
      ...panel(id, `runtime:${id}`, EDITS()),
      fileKind: 'runtime',
      source: { script: 'fig1.py', entry: 'main', stem: 'Fig1', captureSource: 'savefig', fingerprint: 'f', sizeMm: [80, 60], ...(runConfig ? { runConfig } : {}) },
    }) as PanelObject
  const OLDIDS = [OLD, OLD, OTHER, null, OTHER]
  const NEWIDS = [NEW, NEW, OTHER, null, OTHER]

  beforeEach(async () => {
    await load([panel('disk', 'Fig1.pdf', EDITS()), runtime('rt_a', 'rc_a'), runtime('rt_b', 'rc_b'), runtime('rt_none')])
  })

  it('验证用 rc_a：磁盘面板（跟默认配置）与 rc_a 的运行时面板改，rc_b / 无配置的不动；撤销方向同理', () => {
    const r = { ...RENAME, run_config: 'rc_a' }
    rebindRenamedIdentities([r], 'forward')
    expect(active().map(ids)).toEqual([NEWIDS, NEWIDS, OLDIDS, OLDIDS])
    rebindRenamedIdentities([r], 'back')
    expect(active().map(ids)).toEqual([OLDIDS, OLDIDS, OLDIDS, OLDIDS])
  })

  it('验证没带配置（无参数运行）：只改磁盘面板与无配置的运行时面板', () => {
    rebindRenamedIdentities([RENAME], 'forward')
    expect(active().map(ids)).toEqual([NEWIDS, OLDIDS, OLDIDS, NEWIDS])
  })

  it('parseRenames 保留闭集的 run_config', () => {
    expect(parseRenames([{ ...RENAME, run_config: 'rc_a' }])).toEqual([{ ...RENAME, run_config: 'rc_a' }])
    expect(parseRenames([{ ...RENAME, run_config: 5 }])).toEqual([RENAME])
  })
})
