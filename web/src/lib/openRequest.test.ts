import { formatMessage } from '@/i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { applyOpenRequest, readOpenRequestFromUrl, stemOf } from '@/lib/openRequest'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { useProjectStore } from '@/store/projectStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { CapturedFigureDescriptor, PanelInfo, RuntimeAssetInfo } from '@/lib/api'

function panel(id: string, extra: Partial<PanelInfo> = {}): PanelInfo {
  return {
    id,
    name: id,
    folder: '.',
    kind: id.endsWith('.pdf') ? 'pdf' : 'raster',
    native_w_mm: 80,
    native_h_mm: 60,
    mtime: 1,
    script: 'fig1.py',
    ...extra,
  }
}

function setPanels(panels: PanelInfo[]) {
  useAssetStore.setState({
    panels,
    byId: Object.fromEntries(panels.map((p) => [p.id, p])),
    loaded: true,
  })
}

function descriptor(script: string, stem: string): CapturedFigureDescriptor {
  return {
    asset_id: `runtime:${script}#${stem}`,
    script,
    entry: '__main__',
    stem,
    capture_source: 'pyplot',
    execution_profile: 'safe',
    original_artifact: null,
    size_mm: [80, 60],
    source_fingerprint: 'sha256:x',
    can_writeback_artifact: false,
    can_writeback_source: false,
  }
}

function runtimeAsset(script: string, stem: string, cached = true): RuntimeAssetInfo {
  return {
    id: `runtime:${script}#${stem}`,
    script,
    stem,
    entry: '__main__',
    status: cached ? 'fresh' : 'needs_rerun',
    cached,
    size_mm: cached ? [80, 60] : null,
    capture_source: cached ? 'pyplot' : null,
    descriptor: cached ? descriptor(script, stem) : null,
  }
}

function setRuntimeAssets(assets: RuntimeAssetInfo[]) {
  useRuntimeAssetStore.setState({
    assets,
    loadAssets: vi.fn(async () => {}),
  } as never)
}

beforeEach(() => {
  useAssetStore.setState({ panels: [], byId: {}, loaded: false })
  useRuntimeAssetStore.setState({ assets: [], loadAssets: vi.fn(async () => {}) } as never)
  useFigurePickerStore.setState({ script: null })
  useSelectionStore.getState().set([])
  useProjectStore.setState({
    phase: 'open',
    project: { open: true, id: 'p1', figures_dir: '/proj/figures', name: 'figures' } as never,
  })
  useDocumentStore.setState({
    doc: { schema: 2, name: 't', page: { w: 210, h: 297 }, objects: [], guides: [] },
  } as never)
  window.history.replaceState(null, '', '/')
})

describe('stemOf', () => {
  it('去目录与扩展名', () => {
    expect(stemOf('Fig1_kinetics.pdf')).toBe('Fig1_kinetics')
    expect(stemOf('panels/Fig2.png')).toBe('Fig2')
    expect(stemOf('sub\\Fig3.pdf')).toBe('Fig3')
    expect(stemOf('no_ext')).toBe('no_ext')
  })
})

describe('readOpenRequestFromUrl', () => {
  it('认下 ?open= 并立刻从地址栏抹掉', () => {
    window.history.replaceState(null, '', '/?open=Fig1_kinetics&pj=abc')
    expect(readOpenRequestFromUrl()).toEqual({ stem: 'Fig1_kinetics' })
    // pj 不属于本模块，必须原样留给 lib/session.ts
    expect(window.location.search).toBe('?pj=abc')
  })

  it('没有参数就是 null', () => {
    expect(readOpenRequestFromUrl()).toBeNull()
  })

  it('认下 ?pick=（多 Figure 选择器）并抹掉；stem 在时以 stem 为准', () => {
    window.history.replaceState(null, '', '/?pick=sub%2Fplot.py')
    expect(readOpenRequestFromUrl()).toEqual({ pick: 'sub/plot.py' })
    expect(window.location.search).toBe('')

    window.history.replaceState(null, '', '/?open=Fig1&pick=plot.py')
    expect(readOpenRequestFromUrl()).toEqual({ stem: 'Fig1' })
    expect(window.location.search).toBe('')
  })
})

describe('applyOpenRequest', () => {
  it('把面板加入画布并选中', async () => {
    const load = vi.fn(async () => setPanels([panel('Fig1.pdf')]))
    useAssetStore.setState({ load } as never)

    const out = await applyOpenRequest({ stem: 'Fig1' })

    expect(out).toBe('placed')
    expect(load).toHaveBeenCalled() // 刚写出来的图必须重扫，否则永远「找不到」
    const objects = useDocumentStore.getState().doc.objects
    expect(objects).toHaveLength(1)
    expect(useSelectionStore.getState().ids).toEqual([objects[0].id])
  })

  it('同一张图再交接一次只选中，不再叠一份', async () => {
    setPanels([panel('Fig1.pdf')])
    useAssetStore.setState({ load: vi.fn(async () => {}) } as never)
    await applyOpenRequest({ stem: 'Fig1' })
    await applyOpenRequest({ stem: 'Fig1' })

    expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
  })

  it('同 stem 的 PDF 与 PNG 都在时取矢量那份', async () => {
    useAssetStore.setState({
      load: vi.fn(async () => setPanels([panel('Fig1.png'), panel('Fig1.pdf')])),
    } as never)

    await applyOpenRequest({ stem: 'Fig1' })

    const obj = useDocumentStore.getState().doc.objects[0] as { fileId: string }
    expect(obj.fileId).toBe('Fig1.pdf')
  })

  it('项目不同才切项目——同项目绝不调 open（会把画布清空）', async () => {
    const open = vi.fn(async () => ({}) as never)
    const load = vi.fn(async () => setPanels([panel('Fig1.pdf')]))
    useProjectStore.setState({ open } as never)
    useAssetStore.setState({ load } as never)

    await applyOpenRequest({ project: '/proj/figures/', stem: 'Fig1' })

    expect(open).not.toHaveBeenCalled() // 尾部斜杠不算不同
    expect(load).toHaveBeenCalled()
  })

  it('另一个项目：走 projectStore.open', async () => {
    const open = vi.fn(async () => {
      setPanels([panel('Fig9.pdf')])
      return {} as never
    })
    useProjectStore.setState({ open } as never)
    useAssetStore.setState({ load: vi.fn(async () => {}) } as never)

    const out = await applyOpenRequest({ project: '/other/figures', stem: 'Fig9' })

    expect(open).toHaveBeenCalledWith('/other/figures')
    expect(out).toBe('placed')
  })

  it('磁盘上没有的 stem 落到 runtime 素材：按描述符加运行时面板', async () => {
    useAssetStore.setState({ load: vi.fn(async () => setPanels([])) } as never)
    setRuntimeAssets([runtimeAsset('show.py', 'show')])

    const out = await applyOpenRequest({ stem: 'show' })

    expect(out).toBe('placed')
    const obj = useDocumentStore.getState().doc.objects[0] as {
      fileId: string
      fileKind: string
    }
    expect(obj.fileId).toBe('runtime:show.py#show')
    expect(obj.fileKind).toBe('runtime')
  })

  it('runtime 素材已登记但没有描述符：如实引导，不造假面板', async () => {
    useAssetStore.setState({ load: vi.fn(async () => setPanels([])) } as never)
    setRuntimeAssets([runtimeAsset('show.py', 'show', false)])

    const out = await applyOpenRequest({ stem: 'show' })

    expect(out).toBe('runtime-uncached')
    expect(useDocumentStore.getState().doc.objects).toHaveLength(0)
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('多 Figure（pick）：打开 Figure 选择器，绝不静默选第一张', async () => {
    useAssetStore.setState({ load: vi.fn(async () => setPanels([])) } as never)
    setRuntimeAssets([runtimeAsset('multi.py', 'FigA'), runtimeAsset('multi.py', 'FigB')])

    const out = await applyOpenRequest({ pick: 'multi.py' })

    expect(out).toBe('picker')
    expect(useFigurePickerStore.getState().script).toBe('multi.py')
    expect(useDocumentStore.getState().doc.objects).toHaveLength(0)
  })

  it('找不到 stem 就说找不到，绝不退而求其次选别的面板', async () => {
    useAssetStore.setState({
      load: vi.fn(async () => setPanels([panel('Other.pdf')])),
    } as never)

    const out = await applyOpenRequest({ stem: 'Fig1' })

    expect(out).toBe('missing')
    expect(useDocumentStore.getState().doc.objects).toHaveLength(0)
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('只给目录（无 stem）= 只切项目', async () => {
    const open = vi.fn(async () => ({}) as never)
    useProjectStore.setState({ open } as never)

    expect(await applyOpenRequest({ project: '/other/figures' })).toBe('project-only')
    expect(open).toHaveBeenCalled()
  })

  it('没有项目也没给项目路径：报错而不是静默无事发生', async () => {
    useProjectStore.setState({ phase: 'none', project: null } as never)
    expect(await applyOpenRequest({ stem: 'Fig1' })).toBe('no-project')
    expect(useUiStore.getState().statusTone).toBe('error')
  })

  it('打开项目失败：报错并停下，不去猜面板', async () => {
    useProjectStore.setState({
      open: vi.fn(async () => {
        throw new Error('目录不存在')
      }),
    } as never)

    expect(await applyOpenRequest({ project: '/gone', stem: 'Fig1' })).toBe('failed')
    expect(formatMessage(useUiStore.getState().status)).toContain('目录不存在')
  })
})
