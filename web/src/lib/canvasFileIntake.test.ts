/**
 * 拖 / 粘贴文件进画布（ADR 0176）的前端判据。主语：**发给后端的上传（名字 / 项目）、放上画布的次数与落点、说给用户的那一句
 * （问题是否带「发送问题反馈」主按钮）**。上传与素材清单用桩，`addPanelToCanvas` 走真的（恢复门因此真的生效）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import golden from '../../../tests/golden/asset_import_extensions.json'
import { formatMessage } from '@/i18n'
import { ApiError, type ImportedAsset, type PanelInfo } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import { useAssetStore } from '@/store/assetStore'
import { registerGesture } from '@/store/gestureCoordinator'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { canvasToDoc, emptyProject } from '@/types/document'

const imports = vi.hoisted(() => ({ importAsset: vi.fn() }))
vi.mock('@/lib/api', async (orig) => ({ ...(await orig<typeof import('@/lib/api')>()), importAsset: imports.importAsset }))
const gate = vi.hoisted(() => ({ deferUserEntry: vi.fn(() => 'run' as 'run' | 'deferred' | 'blocked') }))
vi.mock('@/store/documentRestore', async (orig) => ({ ...(await orig<typeof import('@/store/documentRestore')>()), deferUserEntry: gate.deferUserEntry }))

import {
  ASSET_EXTENSIONS,
  feedbackAction,
  importAndPlace,
  intakeKindOf,
  pastedAssetFiles,
  pastedImageName,
  planIntake,
  reportImageResult,
  reportProblems,
} from './canvasFileIntake'

const file = (name: string, type = '') => new File([new Uint8Array([1])], name, { type })
const info = (id: string): PanelInfo =>
  ({ id, name: id.replace(/\.\w+$/, ''), folder: '.', mtime: 1, kind: 'raster', native_w_mm: 40, native_h_mm: 30, px_w: 400, px_h: 300 }) as PanelInfo
const asset = (name: string, over: Partial<ImportedAsset> = {}): ImportedAsset => ({
  id: name,
  name: name.replace(/\.\w+$/, ''),
  file: name,
  kind: 'raster',
  reused: false,
  renamed: false,
  bytes: 1,
  ...over,
})
const objects = () => useDocumentStore.getState().doc.objects
const say = () => formatMessage(useUiStore.getState().status)

beforeEach(() => {
  vi.clearAllMocks()
  gate.deferUserEntry.mockReturnValue('run')
  setCurrentProjectId('pj_a')
  const base = canvasToDoc(emptyProject().canvases[0])
  useDocumentStore.setState({ doc: { ...base, objects: [] }, past: [], future: [], txn: null })
  useUiStore.getState().setStatus(null)
  useDiagSendStore.setState({ capability: { enabled: true } as never, open: false })
  useAssetStore.setState({ panels: [], byId: {}, unsupported: [], loaded: true } as never)
  // 重扫：把「刚导入的」素材装进清单
  vi.spyOn(useAssetStore.getState(), 'load').mockImplementation(async () => {
    const ids = imports.importAsset.mock.results.map((r) => (r.value as Promise<ImportedAsset>))
    const got = await Promise.all(ids.map((p) => p.catch(() => null)))
    const panels = got.filter((a): a is ImportedAsset => !!a).map((a) => info(a.id))
    useAssetStore.setState({ panels, byId: Object.fromEntries(panels.map((p) => [p.id, p])) } as never)
    return { panels } as never
  })
})

describe('分类', () => {
  it('后缀表与后端 golden 同源', () => {
    expect([...ASSET_EXTENSIONS].sort()).toEqual(golden.extensions)
  })

  it.each([
    ['fig.png', '', 'asset'],
    ['FIG.JPG', '', 'asset'],
    ['a.tiff', '', 'asset'],
    ['a.pdf', '', 'asset'],
    ['a.svg', '', 'asset'],
    ['plot.py', '', 'script'],
    ['plot.R', '', 'foreign'],
    ['plot.m', '', 'foreign'],
    ['p.tavotto', '', 'package'],
    ['p.zip', '', 'package'],
    ['photo.webp', '', 'otherImage'],
    ['x.gif', '', 'otherImage'],
    ['data.csv', '', 'other'],
    ['nb.ipynb', '', 'notebook'],
    ['NB.IPYNB', '', 'other'],
    ['.ipynb', '', 'other'],
    ['noext', '', 'other'],
    ['image', 'image/png', 'asset'],
    ['blob', 'application/pdf', 'asset'],
    ['clip', 'image/webp', 'otherImage'],
  ])('%s (%s) → %s', (name, type, kind) => {
    expect(intakeKindOf({ name, type })).toBe(kind)
  })

  it('一批文件按类型分开，没有哪个被丢', () => {
    const plan = planIntake([file('a.png'), file('b.py'), file('c.R'), file('d.tavotto'), file('e.csv'), file('f.webp')])
    expect(plan.assets.map((f) => f.name)).toEqual(['a.png'])
    expect(plan.scripts.map((f) => f.name)).toEqual(['b.py'])
    expect(plan.foreign.map((f) => f.name)).toEqual(['c.R'])
    expect(plan.packages.map((f) => f.name)).toEqual(['d.tavotto'])
    expect(plan.unsupported).toEqual([
      { name: 'e.csv', image: false },
      { name: 'f.webp', image: true },
    ])
  })
})

describe('importAndPlace', () => {
  it('一张图：按文件名上传、成为素材、放在落点；发起项目随请求带走', async () => {
    imports.importAsset.mockImplementation(async (_f: Blob, name: string) => asset(name))
    const canvasId = useDocumentStore.getState().activeCanvasId
    const res = await importAndPlace([file('fig.png')], { place: { canvasId, at: { x: 50, y: 40 } } })
    expect(imports.importAsset).toHaveBeenCalledWith(expect.any(File), 'fig.png', 'pj_a')
    expect(res).toMatchObject({ placed: ['fig'], problems: [], aborted: false })
    expect(objects()).toHaveLength(1)
    const [p] = objects()
    // 以落点为中心（与素材卡拖入同一个 `placePanelInPage`）
    expect(p.x + p.w / 2).toBeCloseTo(50, 0)
    expect(p.y + p.h / 2).toBeCloseTo(40, 0)
  })

  it('多张图：全部放上去，只有第一张用落点，其余自然错开不叠在一起', async () => {
    imports.importAsset.mockImplementation(async (_f: Blob, name: string) => asset(name))
    const canvasId = useDocumentStore.getState().activeCanvasId
    const res = await importAndPlace([file('a.png'), file('b.png'), file('c.png')], { place: { canvasId, at: { x: 60, y: 40 } } })
    expect(res.placed).toEqual(['a', 'b', 'c'])
    expect(objects()).toHaveLength(3)
    const boxes = objects().map((o) => `${o.x},${o.y}`)
    expect(new Set(boxes).size).toBe(3)
  })

  it('没有项目（playground / MCP 画布）：不发请求，说清没有项目文件夹可放', async () => {
    setCurrentProjectId(null)
    const res = await importAndPlace([file('a.png'), file('b.png')])
    expect(imports.importAsset).not.toHaveBeenCalled()
    expect(res.placed).toEqual([])
    expect(res.problems).toHaveLength(2)
    expect(formatMessage(res.problems[0].reason)).toContain('项目文件夹')
    reportImageResult(res)
    expect(say()).toContain('2')
  })

  it('上传期间切到了别的画布：不带落点（旧画布的页面坐标放到别处会落在页外）', async () => {
    imports.importAsset.mockImplementation(async (_f: Blob, name: string) => asset(name))
    const res = await importAndPlace([file('a.png')], { place: { canvasId: 'c_gone', at: { x: 9999, y: 9999 } } })
    expect(res.placed).toEqual(['a'])
    const [p] = objects()
    expect(p.x).toBeLessThan(500)
  })

  it('有的成、有的不成：成的照放，不成的带着后端原因', async () => {
    imports.importAsset.mockImplementation(async (_f: Blob, name: string) => {
      if (name === 'bad.png') throw new ApiError('x', 415, { code: 'asset_import_unsupported_type', params: {} })
      return asset(name)
    })
    const res = await importAndPlace([file('ok.png'), file('bad.png')])
    expect(res.placed).toEqual(['ok'])
    expect(res.problems).toHaveLength(1)
    expect(res.problems[0].name).toBe('bad.png')
    expect(formatMessage(res.problems[0].reason)).toContain('PNG')
  })

  it('上传完项目换了：整批作废，什么都不放、什么都不说', async () => {
    imports.importAsset.mockImplementation(async (_f: Blob, name: string) => {
      setCurrentProjectId('pj_b')
      return asset(name)
    })
    const res = await importAndPlace([file('a.png')])
    expect(res).toMatchObject({ aborted: true, placed: [], problems: [] })
    expect(objects()).toHaveLength(0)
    reportImageResult(res)
    expect(useUiStore.getState().status).toBeNull()
  })

  it('重扫素材清单期间项目换了：同样整批作废，A 项目的素材绝不放进 B 项目的画布', async () => {
    imports.importAsset.mockImplementation(async (_f: Blob, name: string) => asset(name))
    vi.spyOn(useAssetStore.getState(), 'load').mockImplementation(async () => {
      setCurrentProjectId('pj_b')
      useAssetStore.setState({ byId: { 'a.png': info('a.png') } } as never)
      return null as never
    })
    const res = await importAndPlace([file('a.png')])
    expect(res.aborted).toBe(true)
    expect(objects()).toHaveLength(0)
  })

  it('后端说放进去了、重扫的清单里却没有：算没成，不静默', async () => {
    imports.importAsset.mockImplementation(async (_f: Blob, name: string) => asset(name))
    vi.spyOn(useAssetStore.getState(), 'load').mockImplementation(async () => ({ panels: [] }) as never)
    const res = await importAndPlace([file('ghost.png')])
    expect(res.placed).toEqual([])
    expect(res.problems).toHaveLength(1)
    expect(objects()).toHaveLength(0)
  })

  it('恢复门关着：不丢，排到文档就绪再放（经 addPanelToCanvas 的 deferUserEntry）', async () => {
    imports.importAsset.mockImplementation(async (_f: Blob, name: string) => asset(name))
    let queued: (() => void) | null = null
    gate.deferUserEntry.mockImplementation(((run: () => void) => {
      queued = run
      gate.deferUserEntry.mockReturnValue('run')
      return 'deferred'
    }) as never)
    await importAndPlace([file('late.png')])
    expect(objects()).toHaveLength(0)
    expect(queued).not.toBeNull()
    queued!()
    expect(objects()).toHaveLength(1)
  })

  it('上传挂起期间用户开了手势（事务）：放置先收手势再放，取消手势不丢图、一次撤销只撤图', async () => {
    let release!: () => void
    imports.importAsset.mockImplementation(
      (_f: Blob, name: string) => new Promise<ImportedAsset>((r) => (release = () => r(asset(name)))),
    )
    const pending = importAndPlace([file('late.png')])
    // 用户在上传期间开始了一次拖动：事务开着，手势登记了自己的收尾
    const doc = useDocumentStore.getState()
    doc.beginTxn({ key: 'x' } as never)
    const unregister = registerGesture(() => useDocumentStore.getState().endTxn())
    release()
    await pending
    unregister()
    expect(useDocumentStore.getState().txn).toBeNull()
    // 手势已被收尾（提交），之后「取消」不会回滚图
    useDocumentStore.getState().endTxn({ discard: true })
    expect(objects()).toHaveLength(1)
    // 撤销一次：图作为独立一条历史被撤掉，且只撤了它
    const before = useDocumentStore.getState().past.length
    expect(before).toBeGreaterThanOrEqual(1)
    useDocumentStore.getState().undo()
    expect(objects()).toHaveLength(0)
  })

  it('同名同内容复用：照样放上画布（用户要的是放一张）', async () => {
    imports.importAsset.mockImplementation(async (_f: Blob, name: string) => asset(name, { reused: true }))
    const res = await importAndPlace([file('fig.png')])
    expect(res.placed).toEqual(['fig'])
    expect(objects()).toHaveLength(1)
  })
})

describe('说给用户的那一句', () => {
  it('放成了：静默做完并说一句（单张说名字，多张说张数），没有按钮', () => {
    reportImageResult({ placed: ['fig'], problems: [], aborted: false })
    expect(say()).toContain('fig')
    expect(useUiStore.getState().statusAction).toBeNull()
    reportImageResult({ placed: ['a', 'b', 'c'], problems: [], aborted: false })
    expect(say()).toContain('3')
  })

  it('有问题：一句话 + 「发送问题反馈」主按钮，错误语气；按钮打开反馈对话框', () => {
    reportProblems([{ name: 'a.csv' }])
    const s = useUiStore.getState()
    expect(s.statusTone).toBe('error')
    expect(s.statusAction).not.toBeNull()
    expect(formatMessage(s.statusAction!.label)).toBe('发送问题反馈')
    s.statusAction!.run()
    expect(useDiagSendStore.getState().open).toBe(true)
  })

  it('图片格式接不住的那一句点出能放的格式；别的类型点出能放图片、脚本和项目包', () => {
    reportProblems([{ name: 'p.webp', image: true }])
    expect(say()).toContain('p.webp')
    expect(say()).toContain('PNG')
    reportProblems([{ name: 'data.csv' }])
    expect(say()).toContain('data.csv')
    expect(say()).toContain('项目包')
    expect(say()).not.toContain('Python 脚本')
  })

  it('多个没成：汇成一句，仍带按钮；混着成功也是问题说话', () => {
    reportImageResult({ placed: ['ok'], problems: [{ name: 'a' }, { name: 'b' }], aborted: false })
    expect(say()).toContain('2')
    expect(useUiStore.getState().statusAction).not.toBeNull()
  })

  it('反馈入口被关掉（引擎说不可用）：不给按钮，句子照说', () => {
    useDiagSendStore.setState({ capability: null })
    expect(feedbackAction()).toBeUndefined()
    reportProblems([{ name: 'a.csv' }])
    expect(say()).toContain('a.csv')
    expect(useUiStore.getState().statusAction).toBeNull()
  })

  it('没有问题也没有成功：什么都不说', () => {
    expect(reportProblems([])).toBe(false)
    reportImageResult({ placed: [], problems: [], aborted: false })
    expect(useUiStore.getState().status).toBeNull()
  })
})

describe('剪贴板图片', () => {
  const at = new Date(2026, 10, 10, 21, 34, 55)
  it('截图的占位名换成 pasted-时间戳；真有名字的沿用；扩展名按媒体类型', () => {
    expect(pastedImageName({ name: 'image.png', type: 'image/png' }, at)).toBe('pasted-20261110-213455.png')
    expect(pastedImageName({ name: '', type: 'image/jpeg' }, at)).toBe('pasted-20261110-213455.jpg')
    expect(pastedImageName({ name: 'image.png', type: '' }, at, 1)).toBe('pasted-20261110-213455-2.png')
    expect(pastedImageName({ name: 'Fig 3.png', type: 'image/png' }, at)).toBe('Fig 3.png')
  })

  it('只取能放上画布的文件；files 空时看 items', () => {
    const png = file('image.png', 'image/png')
    expect(pastedAssetFiles({ files: [png, file('a.csv')] as never, items: [] as never })).toEqual([png])
    const item = { kind: 'file', getAsFile: () => png }
    expect(pastedAssetFiles({ files: [] as never, items: [item, { kind: 'string', getAsFile: () => null }] as never })).toEqual([png])
    expect(pastedAssetFiles(null)).toEqual([])
  })
})
