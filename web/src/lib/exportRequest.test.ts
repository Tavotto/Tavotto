/**
 * 统一 ExportRequest 的构造（ADR 0031）。
 *
 * 三件事各一组：`scope=original` 里**没有布局**、PPI 只在位图时有意义、
 * 快照指纹量的是"会不会出来另一个文件"。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import {
  buildExportRequest,
  defaultScope,
  filenameProblem,
  originalAvailability,
  pixelPreview,
  snapshotRevision,
  type ExportRequestInput,
} from './exportRequest'
import { useDocumentStore } from '@/store/documentStore'
import { useAssetStore } from '@/store/assetStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { emptyProject, type FigureDocument, type PanelObject } from '@/types/document'
import { literal } from '@/i18n'

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
}

function inputOf(over: Partial<ExportRequestInput> = {}): ExportRequestInput {
  const doc = useDocumentStore.getState().doc
  return {
    scope: 'canvas',
    formats: ['pdf', 'png'],
    filename: 'Fig 1',
    ppi: 600,
    documentId: 'd1',
    doc,
    ...over,
  }
}

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_req')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.page = { w: 180, h: 120 }
    d.objects = [{ ...panel }]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
})

describe('scope', () => {
  it('默认跟着工作流走', () => {
    expect(defaultScope('fast_edit')).toBe('original')
    expect(defaultScope('layout')).toBe('canvas')
  })

  it('`original` 段里没有 x/y/w/h；尺寸来自 spec 而不是画布上的落位', () => {
    // **面板在画布上被缩到 40 × 30，而它自己是 80 × 60**：两个数字必须不同，
    // 否则"用的是 spec 还是用的是 w/h"这个问题在这条用例里根本量不出来
    // （变异反证当场抓到过：夹具让判据恒真）
    useDocumentStore.getState().commit(literal('缩小'), (d) => {
      const p0 = d.objects[0] as PanelObject
      p0.w = 40
      p0.h = 30
    })
    const doc = useDocumentStore.getState().doc
    const p = doc.objects[0] as PanelObject
    const { request } = buildExportRequest(
      inputOf({
        doc,
        scope: 'original',
        figureId: 'Fig1.pdf',
        panel: p,
        spec: {
          figureId: 'Fig1.pdf',
          sourceKind: 'vector',
          widthMm: 80,
          heightMm: 60,
          pixelWidth: null,
          pixelHeight: null,
          dpi: null,
          dpiSource: 'unknown',
          viewportPt: null,
          transparent: null,
          origin: 'document',
          stale: false,
          fallback: false,
          ignored: ['scale'],
        },
      }),
    )
    expect(request.canvas).toBeUndefined()
    const keys = Object.keys(request.original!)
    for (const forbidden of ['x_mm', 'y_mm', 'w', 'h', 'page_w_mm', 'page_h_mm', 'crop']) {
      expect(keys, `original 段里不该有 ${forbidden}`).not.toContain(forbidden)
    }
    // 被忽略的变换要说出来：忽略而不说等于骗人
    expect(request.original!.ignored).toEqual(['scale'])
    // 图幅是 80 × 60（它自己的），不是 40 × 30（画布上的落位）
    expect(p.w).toBe(40)
    expect(request.original!.w_mm).toBe(80)
    expect(request.original!.h_mm).toBe(60)
  })

  it('画布范围发的是页面 + z 序对象，隐藏对象不发', () => {
    useDocumentStore.getState().commit(literal('藏一个'), (d) => {
      d.objects = [
        { ...panel },
        { ...panel, id: 'p2', hidden: true },
      ]
    })
    const { request } = buildExportRequest(inputOf({ doc: useDocumentStore.getState().doc }))
    expect(request.original).toBeUndefined()
    expect(request.canvas!.page_w_mm).toBe(180)
    expect(request.canvas!.objects).toHaveLength(1)
  })

  it('原图不可用时说得出**为什么**，而不是回一个笼统的 false', () => {
    expect(originalAvailability(null)).toMatchObject({ ok: false, reason: 'no_figure' })
    expect(originalAvailability('不存在.pdf')).toMatchObject({
      ok: false,
      reason: 'unknown_figure',
    })
    expect(originalAvailability('Fig1.pdf').ok).toBe(true)
  })

  it('源文件不在素材清单里 → **不可用**（不是给一个按下去必然失败的按钮）', () => {
    // 面板还在文档里（规格取自它的 nativeW/H），但素材清单里没有这个 id：
    // 后端解析面板源的第一步就是 safe_resolve()，文件不在就 404 ——
    // 「引擎能重新画一张」这个指望在那条路上兑现不了
    useAssetStore.setState({ byId: {} } as never)
    const a = originalAvailability('Fig1.pdf')
    expect(a.spec).toBeTruthy() // 规格还在（上一次已知的那份），只是导不出来
    expect(a.ok).toBe(false)
    expect(a.reason).toBe('source_stale')
  })

  it('判据是「够不够得着」而不是 `spec.stale`', () => {
    // 刚渲染过的图：manifest 还在手上，`stale` 是 false，而磁盘文件可能早没了。
    // 拿 `stale` 当判据的话这张图会拿到一个按下去必然失败的按钮
    useRenderStore.setState({
      byKey: {
        [renderKey('Fig1.pdf', [])]: {
          fileId: 'Fig1.pdf',
          manifest: { stem: 'Fig1', size_mm: [80, 60], elements: [] },
          status: 'ready',
        },
      },
      latest: { 'Fig1.pdf': renderKey('Fig1.pdf', []) },
      tracked: {},
      building: {},
    } as never)
    useAssetStore.setState({ byId: {} } as never)
    const a = originalAvailability('Fig1.pdf')
    expect(a.spec?.stale, '这张图的规格是新鲜的（origin=render_metadata）').toBe(false)
    expect(a.ok, '规格新鲜 ≠ 源文件够得着').toBe(false)
    expect(a.reason).toBe('source_stale')
  })
})

describe('PPI 只在位图输出时有意义', () => {
  it('只出 PDF 时 ppi 是 null，不是一个不起作用的默认值', () => {
    expect(buildExportRequest(inputOf({ formats: ['pdf'] })).request.ppi).toBeNull()
    expect(buildExportRequest(inputOf({ formats: ['png'] })).request.ppi).toBe(600)
  })

  it('超出范围的 ppi 被夹住，不发一个后端要拒绝的数', () => {
    expect(buildExportRequest(inputOf({ ppi: 999999 })).request.ppi).toBe(1200)
    expect(buildExportRequest(inputOf({ ppi: 1 })).request.ppi).toBe(36)
    expect(buildExportRequest(inputOf({ ppi: Number.NaN })).request.ppi).toBe(600)
  })
})

describe('文件名', () => {
  it('顺手打上的扩展名被剥掉，非法字符当场报原因', () => {
    expect(buildExportRequest(inputOf({ filename: 'Fig 1.pdf' })).request.filename).toBe('Fig 1')
    expect(filenameProblem('Fig?1', ['pdf'])).toBe('illegal_char')
    expect(filenameProblem('Fig 1.pdf', ['pdf'])).toBeNull()
  })

  it('预览出这次会写哪几个文件', () => {
    expect(buildExportRequest(inputOf()).names).toEqual(['Fig 1.pdf', 'Fig 1.png'])
    expect(buildExportRequest(inputOf({ formats: ['png'] })).names).toEqual(['Fig 1.png'])
  })
})

describe('快照指纹', () => {
  it('量的是"会不会出来另一个文件"，不是"文档有没有被动过"', () => {
    const a = buildExportRequest(inputOf()).revision
    // 改画布名：导出结果一模一样 → 指纹不变（不然完成时会冒一句假的"被编辑过"）
    useDocumentStore.getState().renameProject('另一个名字')
    const b = buildExportRequest(inputOf({ doc: useDocumentStore.getState().doc })).revision
    expect(b).toBe(a)

    // 挪一个对象：出来的图不一样 → 指纹必须变
    useDocumentStore.getState().commit(literal('挪'), (d) => {
      d.objects[0].x = 33
    })
    const c = buildExportRequest(inputOf({ doc: useDocumentStore.getState().doc })).revision
    expect(c).not.toBe(a)
  })

  it('两个 scope 的指纹互不相干', () => {
    const doc = useDocumentStore.getState().doc as FigureDocument
    const canvas = snapshotRevision(buildExportRequest(inputOf({ doc })).request)
    const original = snapshotRevision(
      buildExportRequest(inputOf({ doc, scope: 'original', figureId: 'Fig1.pdf' })).request,
    )
    expect(original).not.toBe(canvas)
  })
})

describe('像素预览：照抄源文件才报源像素网格', () => {
  const page = { w: 180, h: 120 }
  const raster = {
    widthMm: 10.16,
    heightMm: 6.77,
    pixelWidth: 120,
    pixelHeight: 80,
    sourceKind: 'raster',
  } as never

  it('照抄源位图时报源像素网格，与 ppi 无关', () => {
    expect(pixelPreview('original', 600, page, raster, true)).toContain('120')
    expect(pixelPreview('original', 300, page, raster, true)).toContain('120')
  })

  it('**带 override 的位图会被引擎重画**：这时 ppi 说了算，不许报源像素网格', () => {
    const shown = pixelPreview('original', 600, page, raster, false)
    expect(shown, '对着一张即将被重画的图报源像素网格 = 界面与文件各说各的').not.toContain(
      '120 × 80',
    )
    // 10.16mm @ 600ppi ≈ 240px
    expect(shown).toContain('240')
  })
})
