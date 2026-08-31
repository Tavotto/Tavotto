/**
 * 检查编排的看护（ADR 0030）：**什么时候跑、跑哪几片、上一次的结果怎么留**。
 *
 * 判据全部围绕四条纪律：防抖 + 代次、增量、失败不清空、不改文档。
 */
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import * as validation from '@/lib/validation'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { seedExactRender } from '@/test/renderFixtures'
import {
  cancelScheduled,
  collectCanvases,
  getValidationSummary,
  listIssues,
  rawIssuesFor,
  runValidation,
  schedule,
  startValidation,
  useValidationStore,
  VALIDATION_DEBOUNCE_MS,
} from './validationStore'
import { emptyProject, type PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel: PanelObject = {
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
}

const manifest = (pt: number) => ({
  stem: 'Fig1',
  size_mm: [80, 60],
  elements: [
    {
      gid: 'axes_0.xticks',
      role: 'ticks',
      label: 'X 刻度文字',
      bbox: [0.1, 0.9, 0.8, 0.05],
      draggable: false,
      editable: [{ prop: 'fontsize', type: 'number', value: pt }],
    },
  ],
})

async function seedProject(pt = 6) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_validation')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 80, h: 60 }
    d.objects = [{ ...panel }]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  seedExactRender(panel, manifest(pt) as never)
}

beforeEach(() => {
  vi.useFakeTimers()
  cancelScheduled()
  useValidationStore.setState({
    results: [],
    issues: [],
    ready: false,
    failed: false,
    running: false,
    lastDurationMs: null,
  })
})

afterEach(() => {
  cancelScheduled()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('防抖与代次', () => {
  it('一串连续修改只跑一遍', async () => {
    await seedProject()
    const spy = vi.spyOn(validation, 'validateCanvas')
    schedule()
    schedule()
    schedule()
    expect(spy).not.toHaveBeenCalled()
    act(() => void vi.advanceTimersByTime(VALIDATION_DEBOUNCE_MS))
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('每次排队都推进代次——还在飞的那一轮回来时会被丢掉', async () => {
    await seedProject()
    const gen0 = useValidationStore.getState().generation
    schedule()
    expect(useValidationStore.getState().generation).toBe(gen0 + 1)
    schedule()
    expect(useValidationStore.getState().generation).toBe(gen0 + 2)
  })

  it('代次变了的那一轮结果整份丢掉，不半新半旧地写进去', async () => {
    await seedProject()
    // 求值中途"用户又改了"：代次一推进，这一轮算出来的东西一个字都不许落地
    vi.spyOn(validation, 'validateCanvas').mockImplementation((() => {
      useValidationStore.setState((s) => ({ generation: s.generation + 1 }))
      return { canvasId: 'c', canvasName: 'x', issues: [], raw: [] } as never
    }) as never)
    runValidation()
    expect(useValidationStore.getState().ready).toBe(false)
    expect(useValidationStore.getState().running).toBe(false)
  })
})

describe('增量', () => {
  it('只重算点名的那张画布，别的沿用上一次的分片', async () => {
    await seedProject()
    useDocumentStore.getState().addCanvas('画布 2')
    runValidation()
    const before = useValidationStore.getState().results
    expect(before.length).toBe(2)
    const spy = vi.spyOn(validation, 'validateCanvas')
    const active = useDocumentStore.getState().activeCanvasId
    runValidation(new Set([active]))
    expect(spy).toHaveBeenCalledTimes(1)
    const after = useValidationStore.getState().results
    const untouched = after.find((r) => r.canvasId !== active)!
    // 沿用 = **同一个对象引用**，不是"又算了一遍算出一样的东西"
    expect(untouched).toBe(before.find((r) => r.canvasId !== active))
  })

  it('画布改了名字时分片沿用、标题跟上', async () => {
    await seedProject()
    const id = useDocumentStore.getState().addCanvas('画布 2')
    runValidation()
    useDocumentStore.getState().renameCanvas(id, '新名字')
    runValidation(new Set(['nobody']))
    const row = useValidationStore.getState().results.find((r) => r.canvasId === id)!
    expect(row.canvasName).toBe('新名字')
  })
})

describe('失败不清空', () => {
  it('这一次没查成时保留上一次的结果，并把"没查成"单独说出来', async () => {
    await seedProject()
    runValidation()
    const good = useValidationStore.getState().issues
    expect(good.length).toBeGreaterThan(0)

    vi.spyOn(validation, 'validateCanvas').mockImplementation(() => {
      throw new Error('boom')
    })
    runValidation()
    const s = useValidationStore.getState()
    expect(s.failed).toBe(true)
    expect(s.issues).toEqual(good) // 上一次的真话仍然在
    expect(s.running).toBe(false)
    // 「查不了」不是「没问题」
    expect(getValidationSummary('project').failed).toBe(true)
  })

  it('换项目才清空——上一份项目的问题挂在新项目上是彻头彻尾的假话', async () => {
    await seedProject()
    runValidation()
    expect(useValidationStore.getState().issues.length).toBeGreaterThan(0)
    const stop = startValidation()
    await act(async () => {
      await useDocumentStore.getState().switchDocument(emptyProject(), 'd_other')
    })
    expect(useValidationStore.getState().ready).toBe(false)
    expect(useValidationStore.getState().issues).toEqual([])
    stop()
  })
})

describe('检查不写文档', () => {
  it('跑完之后文档、dirty、撤销栈一个字节没动', async () => {
    await seedProject()
    const before = useDocumentStore.getState()
    const doc = before.doc
    const dirty = before.dirty
    const past = before.past.length
    runValidation()
    const after = useDocumentStore.getState()
    expect(after.doc).toBe(doc)
    expect(after.dirty).toBe(dirty)
    expect(after.past.length).toBe(past)
  })
})

describe('对外 API', () => {
  it('摘要按范围取；导出上下文那几条补进来且去重', async () => {
    await seedProject()
    runValidation()
    const active = useDocumentStore.getState().activeCanvasId
    const extra = validation.exportContextIssues(
      { formats: ['png'], dpi: 1 },
      collectCanvases()[0].profile,
      { documentId: 'd_validation', canvasId: active },
    )
    const merged = getValidationSummary('activeCanvas', extra)
    expect(merged.total).toBe(
      getValidationSummary('activeCanvas').total + extra.length,
    )
    // 同一条补两遍只算一条
    expect(getValidationSummary('activeCanvas', [...extra, ...extra]).total).toBe(merged.total)
  })

  it('聚合投影按画布取得到（proof 留档用的就是它）', async () => {
    await seedProject()
    runValidation()
    const active = useDocumentStore.getState().activeCanvasId
    expect(rawIssuesFor(active).length).toBeGreaterThan(0)
    expect(rawIssuesFor('nope')).toEqual([])
  })

  it('listIssues 按筛选取', async () => {
    await seedProject()
    runValidation()
    expect(listIssues({ ruleCode: 'zzz' })).toEqual([])
    expect(listIssues().length).toBe(useValidationStore.getState().issues.length)
  })
})

describe('订阅', () => {
  it('装配之后排一次；卸载之后不再排', async () => {
    await seedProject()
    const stop = startValidation()
    act(() => void vi.advanceTimersByTime(VALIDATION_DEBOUNCE_MS))
    expect(useValidationStore.getState().ready).toBe(true)

    stop()
    const gen = useValidationStore.getState().generation
    useRenderStore.setState((s) => ({ byKey: { ...s.byKey } }))
    expect(useValidationStore.getState().generation).toBe(gen)
  })
})
