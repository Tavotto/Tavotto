/**
 * 右键菜单背后的 action 层（Prompt 18）：
 *   - 批量锁定 / 隐藏：一条历史、只动需要动的、选区不动；
 *   - 「恢复图内修改」：先问再清，同属性页的 resetOverrides，一条历史可撤销，
 *     不误清同文件的另一个实例，写回过的面板换一句话；
 *   - 「重新构建」：作废热会话 + markStale + 按当前 overrides 重画；文档一个字节不动、
 *     不进历史；作废不了 / 失败都要说出来。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import type { EngineRenderOptions, PanelInfo } from '@/lib/api'
import { setEngineTransport } from '@/lib/engineTransport'
import {
  rebuildPanel,
  resetOverridesConfirmed,
  setObjectsHidden,
  setObjectsLocked,
  triStateOf,
} from '@/store/actions'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type CanvasObject, type PanelObject, type TextObject } from '@/types/document'

const engineRender = vi.fn()
const engineInvalidate = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
  engineInvalidate: (id: string) => engineInvalidate(id),
}))

const text = (id: string, over: Partial<TextObject> = {}): TextObject =>
  ({
    id,
    type: 'text',
    text: id,
    sizePt: 10,
    bold: false,
    color: '#000000',
    align: 'left',
    x: 0,
    y: 0,
    w: 10,
    h: 5,
    ...over,
  }) as TextObject

const panel = (id: string, over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id,
    type: 'panel',
    fileId: 'Fig1.pdf',
    fileKind: 'pdf',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    nativeW: 40,
    nativeH: 30,
    script: 'fig.py',
    overrides: [],
    ...over,
  }) as PanelObject

const objs = () => useDocumentStore.getState().doc.objects
const byId = <T extends CanvasObject = CanvasObject>(id: string) => objs().find((o) => o.id === id) as T
const past = () => useDocumentStore.getState().past

async function seed(items: CanvasObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_qeact_' + Math.random())
  useDocumentStore.getState().commit(literal('放对象'), (d) => {
    d.objects.push(...items)
  })
  useDocumentStore.setState({ past: [], future: [] })
}

const answerConfirm = (ok: boolean) => {
  const req = useUiStore.getState().confirm
  if (!req) throw new Error('没有弹确认框')
  useUiStore.getState().setConfirm(null)
  req.resolve(ok)
}

const flush = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  engineRender.mockReset()
  engineInvalidate.mockReset()
  useRenderStore.getState().clear()
  useUiStore.setState({ status: null, confirm: null })
  useAssetStore.setState({ byId: {} })
})

afterEach(() => {
  useSelectionStore.getState().clear()
  setEngineTransport(null)
})

/* -------------------------------------------------------------------------- */
/*  批量锁定 / 隐藏                                                             */
/* -------------------------------------------------------------------------- */

describe('setObjectsLocked：一条历史，只动要动的，选区不动', () => {
  beforeEach(async () => {
    await seed([text('a'), text('b', { locked: true }), text('c')])
    useSelectionStore.getState().set(['a', 'b', 'c'])
  })

  it('混合 → 全锁：只改 a / c，一条历史，标签带数量', () => {
    setObjectsLocked(['a', 'b', 'c'], true)
    expect(objs().map((o) => !!o.locked)).toEqual([true, true, true])
    expect(past()).toHaveLength(1)
    expect(past()[0].label).toMatchObject({ key: 'history.lockObjects', values: { count: 2 } })
    // 选区一个字不动：锁定改的是能不能挪，不是选没选中
    expect(useSelectionStore.getState().ids).toEqual(['a', 'b', 'c'])
  })

  it('全锁 → 全解：一条历史；已经是目标状态的什么都不写', () => {
    setObjectsLocked(['a', 'b', 'c'], true)
    setObjectsLocked(['a', 'b', 'c'], false)
    expect(objs().map((o) => !!o.locked)).toEqual([false, false, false])
    expect(past()).toHaveLength(2)
    setObjectsLocked(['a', 'b', 'c'], false)
    expect(past()).toHaveLength(2)
  })

  it('只有一个真的要动时用单数那句（中文没有单数档，必须自己分 key）', () => {
    setObjectsLocked(['b', 'c'], true)
    expect(past()[0].label.key).toBe('history.lockObject')
  })

  it('撤销一次整批回来', () => {
    setObjectsLocked(['a', 'b', 'c'], true)
    useDocumentStore.getState().undo()
    expect(objs().map((o) => !!o.locked)).toEqual([false, true, false])
  })

  it('triStateOf：全 / 无 / 混合', () => {
    expect(triStateOf(objs(), (o) => !!o.locked)).toBe('mixed')
    expect(triStateOf([byId('b')], (o) => !!o.locked)).toBe('all')
    expect(triStateOf([byId('a'), byId('c')], (o) => !!o.locked)).toBe('none')
  })
})

describe('setObjectsHidden', () => {
  it('一条历史隐藏整批；撤销整批回来', async () => {
    await seed([text('a'), text('b'), text('c')])
    setObjectsHidden(['a', 'c'], true)
    expect(objs().map((o) => !!o.hidden)).toEqual([true, false, true])
    expect(past()).toHaveLength(1)
    expect(past()[0].label).toMatchObject({ key: 'history.hideObjects', values: { count: 2 } })
    useDocumentStore.getState().undo()
    expect(objs().map((o) => !!o.hidden)).toEqual([false, false, false])
  })
})

/* -------------------------------------------------------------------------- */
/*  恢复图内修改                                                                */
/* -------------------------------------------------------------------------- */

describe('resetOverridesConfirmed：先问再清，同属性页的 resetOverrides', () => {
  const ov = [{ gid: 'axes_0.title', prop: 'text', value: 'A' }]

  beforeEach(async () => {
    await seed([
      panel('p1', { overrides: [...ov] }),
      // 同一文件的另一个实例：绝不能被顺手清掉
      panel('p2', { overrides: [{ gid: 'axes_0.title', prop: 'text', value: 'B' }] }),
    ])
    engineRender.mockResolvedValue({ rev: 1, manifest: { elements: [] }, warnings: [] })
  })

  it('取消：一个字不动、不进历史', async () => {
    const p = resetOverridesConfirmed('p1')
    await flush()
    expect(useUiStore.getState().confirm).not.toBeNull()
    expect(useUiStore.getState().confirm!.body.key).toBe('confirm.resetOverridesBody')
    expect(useUiStore.getState().confirm!.body.values).toEqual({ count: 1 })
    answerConfirm(false)
    expect(await p).toBe(false)
    expect(byId<PanelObject>('p1').overrides).toEqual(ov)
    expect(past()).toHaveLength(0)
  })

  it('确认：只清这个实例、一条历史、可撤销、触发重渲染', async () => {
    const p = resetOverridesConfirmed('p1')
    await flush()
    answerConfirm(true)
    expect(await p).toBe(true)
    expect(byId<PanelObject>('p1').overrides).toEqual([])
    expect(byId<PanelObject>('p2').overrides).toHaveLength(1)
    expect(past()).toHaveLength(1)
    expect(past()[0].label.key).toBe('history.resetOverrides')
    // 清空之后的那个变体（overrides = []）才是要渲染的
    await flush()
    expect(engineRender).toHaveBeenCalledWith('Fig1.pdf', [], expect.anything())
    useDocumentStore.getState().undo()
    expect(byId<PanelObject>('p1').overrides).toEqual(ov)
  })

  it('写回过的面板（override == 磁盘基线）换一句话：文件不会被改回去', async () => {
    useAssetStore.setState({
      byId: {
        'Fig1.pdf': {
          id: 'Fig1.pdf',
          name: 'Fig1',
          folder: '.',
          kind: 'pdf',
          native_w_mm: 40,
          native_h_mm: 30,
          mtime: 1,
          baked_overrides: [...ov],
        } as PanelInfo,
      },
    })
    const p = resetOverridesConfirmed('p1')
    await flush()
    expect(useUiStore.getState().confirm!.body.key).toBe('confirm.resetOverridesBodyBaked')
    answerConfirm(true)
    expect(await p).toBe(true)
    expect(byId<PanelObject>('p1').overrides).toEqual([])
  })

  it('没有 override 的面板不问也不做', async () => {
    useDocumentStore.getState().commit(literal('清'), (d) => {
      const o = d.objects.find((x) => x.id === 'p1') as PanelObject
      o.overrides = []
    })
    useDocumentStore.setState({ past: [], future: [] })
    expect(await resetOverridesConfirmed('p1')).toBe(false)
    expect(useUiStore.getState().confirm).toBeNull()
    expect(past()).toHaveLength(0)
  })
})

/* -------------------------------------------------------------------------- */
/*  重新构建                                                                    */
/* -------------------------------------------------------------------------- */

describe('rebuildPanel：作废热会话 + 按当前 overrides 重画', () => {
  const ov = [{ gid: 'axes_0.title', prop: 'text', value: 'A' }]

  beforeEach(async () => {
    await seed([panel('p1', { overrides: [...ov] })])
    engineRender.mockResolvedValue({ rev: 2, manifest: { elements: [] }, warnings: [] })
    engineInvalidate.mockResolvedValue({ invalidated: true })
  })

  it('先作废（按文件 id）、再按当前 overrides 渲染；文档与历史一个字不动', async () => {
    const before = JSON.stringify(objs())
    // 先画好一版，证明重建之后旧的权威被清掉过（stale）而画面仍在
    await useRenderStore.getState().render('Fig1.pdf', ov)
    engineRender.mockClear()

    expect(await rebuildPanel('p1')).toBe('rebuilt')

    expect(engineInvalidate).toHaveBeenCalledTimes(1)
    expect(engineInvalidate).toHaveBeenCalledWith('Fig1.pdf')
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(engineRender.mock.calls[0][0]).toBe('Fig1.pdf')
    expect(engineRender.mock.calls[0][1]).toEqual(ov) // 不清 override
    expect(JSON.stringify(objs())).toBe(before) // 不改文档
    expect(past()).toHaveLength(0) // 不进历史
    expect(byId<PanelObject>('p1').overrides).toEqual(ov)
    const entry = useRenderStore.getState().get(renderKeyOf(byId<PanelObject>('p1')))
    expect(entry.status).toBe('ready')
    expect(entry.rev).toBe(2)
    expect(useUiStore.getState().status?.key).toBe('status.panelRebuilt')
  })

  it('上一次渲染还在飞时点「重新构建」：等排队的那次真画完再判，不把 rendering 当 failed', async () => {
    // 第一次渲染卡在半路（刚打开的图、慢机器）——重新构建的那次只能排队
    let releaseFirst!: () => void
    const first = new Promise<{ rev: number; manifest: { elements: never[] }; warnings: never[] }>((r) => {
      releaseFirst = () => r({ rev: 1, manifest: { elements: [] }, warnings: [] })
    })
    engineRender.mockReset()
    engineRender.mockReturnValueOnce(first)
    engineRender.mockResolvedValue({ rev: 2, manifest: { elements: [] }, warnings: [] })
    const inFlight = useRenderStore.getState().render('Fig1.pdf', ov, undefined, 'immediate')
    await flush()
    const key = renderKeyOf(byId<PanelObject>('p1'))
    expect(useRenderStore.getState().get(key).status).toBe('rendering')

    const outcome = rebuildPanel('p1')
    await flush()
    releaseFirst()
    await inFlight
    // 在飞的结束 → 排队的（作废后的那次）开始 → 画完：结果必须是 rebuilt 且提示到位
    expect(await outcome).toBe('rebuilt')
    expect(useRenderStore.getState().get(key).status).toBe('ready')
    expect(useRenderStore.getState().get(key).rev).toBe(2)
    expect(engineRender).toHaveBeenCalledTimes(2)
    expect(useUiStore.getState().status?.key).toBe('status.panelRebuilt')
  })

  it('作废先于渲染：渲染那一刻会话已经过期', async () => {
    const order: string[] = []
    engineInvalidate.mockImplementation(async () => {
      order.push('invalidate')
      return { invalidated: true }
    })
    engineRender.mockImplementation(async () => {
      order.push('render')
      return { rev: 3, manifest: { elements: [] }, warnings: [] }
    })
    await rebuildPanel('p1')
    expect(order).toEqual(['invalidate', 'render'])
  })

  it('后端说作废不了（native 会话）：照常重画，但要说源脚本没有重跑', async () => {
    engineInvalidate.mockResolvedValue({ invalidated: false, reason: 'native_session' })
    expect(await rebuildPanel('p1')).toBe('rerendered')
    expect(engineRender).toHaveBeenCalledTimes(1)
    expect(useUiStore.getState().status?.key).toBe('status.panelRerenderedNoRerun')
  })

  it('作废请求失败：不渲染、报错不吞', async () => {
    engineInvalidate.mockRejectedValue(new Error('boom'))
    expect(await rebuildPanel('p1')).toBe('failed')
    expect(engineRender).not.toHaveBeenCalled()
    expect(useUiStore.getState().status).toMatchObject({
      key: 'status.rebuildFailed',
      values: { error: 'boom' },
    })
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('渲染失败：错误落在该变体上（画布 / 属性页已显示），这里不叠 toast', async () => {
    engineRender.mockRejectedValue(new Error('script exploded'))
    expect(await rebuildPanel('p1')).toBe('failed')
    const entry = useRenderStore.getState().get(renderKeyOf(byId<PanelObject>('p1')))
    expect(entry.status).toBe('error')
    expect(useUiStore.getState().status).toBeNull()
  })

  it('装了替代传输（内嵌画布 / playground）：没有作废通道，只重画并说明', async () => {
    setEngineTransport({
      render: async () => ({ rev: 9, manifest: { elements: [] }, warnings: [] }) as never,
      previewPngUrl: async () => '',
      panelSrc: () => null,
    })
    expect(await rebuildPanel('p1')).toBe('rerendered')
    expect(engineInvalidate).not.toHaveBeenCalled()
    expect(useUiStore.getState().status?.key).toBe('status.panelRerenderedNoRerun')
  })

  it('不是可编辑面板：跳过，什么都不发', async () => {
    await seed([panel('p9', { script: null }), text('t')])
    expect(await rebuildPanel('p9')).toBe('skipped')
    expect(await rebuildPanel('t')).toBe('skipped')
    expect(engineInvalidate).not.toHaveBeenCalled()
    expect(engineRender).not.toHaveBeenCalled()
  })

  it('同文件的另一个实例也被 markStale 转入跟踪（会话是共享的）', async () => {
    await seed([panel('p1', { overrides: [...ov] }), panel('p2')])
    await useRenderStore.getState().render('Fig1.pdf', [])
    expect(useRenderStore.getState().get(renderKeyOf(byId<PanelObject>('p2'))).lastPatches).toBe('[]')
    await rebuildPanel('p1')
    const other = useRenderStore.getState().get(renderKeyOf(byId<PanelObject>('p2')))
    expect(other.stale).toBe(true)
    expect(other.lastPatches).toBeNull()
    expect(useRenderStore.getState().tracked['Fig1.pdf']).toBe(true)
  })
})
