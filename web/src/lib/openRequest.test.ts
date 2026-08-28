import { formatMessage } from '@/i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { applyOpenRequest, readOpenRequestFromUrl, stemOf } from '@/lib/openRequest'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useFigurePickerStore } from '@/store/figurePickerStore'
import { useNativeSessionStore } from '@/store/nativeSessionStore'
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
  // native 会话的确认队列：这一层只关心「交接 ID 有没有被送过去」，
  // 取 descriptor / 批准是 nativeSessionStore 自己的用例
  receive = vi.fn(async () => {})
  useNativeSessionStore.setState({ receive } as never)
  window.history.replaceState(null, '', '/')
})

/** 32 位小写十六进制——与 `nativehandoff._ID_RE` / 壳的过滤同源 */
const NATIVE_ID = '0123456789abcdef0123456789abcdef'

/** nativeSessionStore.receive 的替身；每个用例重置一次 */
let receive: ReturnType<typeof vi.fn>

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

  /**
   * `?native=` 是 `tavotto run` **首启**这条路（壳的 `landing_query()`）。
   * 它曾经在这一层整个不存在：壳解析了、`tavotto:open` 事件也带着，只有
   * 落地 URL 与这边没接上——CLI 于是挂到 attach 超时，两边都不报错。
   */
  it('认下 ?native=（tavotto run 的交接 ID）并抹掉', () => {
    window.history.replaceState(null, '', `/?native=${NATIVE_ID}&pj=abc`)
    expect(readOpenRequestFromUrl()).toEqual({ native: NATIVE_ID })
    expect(window.location.search).toBe('?pj=abc')
  })

  it('native 与 stem **不互斥**：一次交接可以既开图又带一条待确认的会话', () => {
    window.history.replaceState(null, '', `/?open=Fig1&native=${NATIVE_ID}`)
    expect(readOpenRequestFromUrl()).toEqual({ stem: 'Fig1', native: NATIVE_ID })
    expect(window.location.search).toBe('')
  })

  it('native 与 pick 同样不互斥', () => {
    window.history.replaceState(null, '', `/?pick=plot.py&native=${NATIVE_ID}`)
    expect(readOpenRequestFromUrl()).toEqual({ pick: 'plot.py', native: NATIVE_ID })
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

  it('同名旧文件在磁盘上：pyplot 捕获的 runtime 素材优先（不打开陈旧文件）', async () => {
    // Codex 评审 P1：pyplot 捕获从来没有原件，同 stem 的磁盘文件只是旧样本
    useAssetStore.setState({
      load: vi.fn(async () => setPanels([panel('show.pdf', { script: 'show.py' })])),
    } as never)
    setRuntimeAssets([runtimeAsset('show.py', 'show')])

    const out = await applyOpenRequest({ stem: 'show' })

    expect(out).toBe('placed')
    const obj = useDocumentStore.getState().doc.objects[0] as { fileId: string }
    expect(obj.fileId).toBe('runtime:show.py#show')
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

  /**
   * `tavotto run` 的交接。CLI 此刻正阻塞在「Waiting for Tavotto desktop…」
   * 上，用户的 Python 一行都还没跑——所以**每条出口都要把它排进确认队列**，
   * 包括项目没打开 / 打不开的那两条。漏掉哪一条，那个终端就一直挂到
   * attach 超时，而界面上什么都没发生过。
   */
  describe('tavotto run 的交接 ID', () => {
    it('只有 native：排进确认队列，没有面板要落', async () => {
      expect(await applyOpenRequest({ native: NATIVE_ID })).toBe('native-pending')
      expect(receive).toHaveBeenCalledWith(NATIVE_ID)
    })

    it('native 与 stem 同时来：图照常落地，确认队列也照常排', async () => {
      const load = vi.fn(async () => setPanels([panel('Fig1.pdf')]))
      useAssetStore.setState({ load } as never)

      expect(await applyOpenRequest({ stem: 'Fig1', native: NATIVE_ID })).toBe('placed')
      expect(receive).toHaveBeenCalledWith(NATIVE_ID)
      expect(useDocumentStore.getState().doc.objects).toHaveLength(1)
    })

    it('项目还没打开也要排——确认屏自带项目路径，attach 不依赖界面开着谁', async () => {
      useProjectStore.setState({ phase: 'none', project: null } as never)
      expect(await applyOpenRequest({ native: NATIVE_ID })).toBe('native-pending')
      expect(receive).toHaveBeenCalledWith(NATIVE_ID)
    })

    it('项目打不开也要排', async () => {
      useProjectStore.setState({
        open: vi.fn(async () => {
          throw new Error('目录不存在')
        }),
      } as never)
      expect(await applyOpenRequest({ project: '/gone', native: NATIVE_ID })).toBe('failed')
      expect(receive).toHaveBeenCalledWith(NATIVE_ID)
    })

    it('**必须在换项目之后排**：换项目会把 native 会话状态整个换代掉', async () => {
      const order: string[] = []
      useProjectStore.setState({
        open: vi.fn(async () => {
          order.push('open')
          return {} as never
        }),
      } as never)
      receive.mockImplementation(async () => {
        order.push('receive')
      })
      await applyOpenRequest({ project: '/other/figures', native: NATIVE_ID })
      expect(order).toEqual(['open', 'receive'])
    })

    it('没有 native 时一次都不碰确认队列', async () => {
      const load = vi.fn(async () => setPanels([panel('Fig1.pdf')]))
      useAssetStore.setState({ load } as never)
      await applyOpenRequest({ stem: 'Fig1' })
      expect(receive).not.toHaveBeenCalled()
    })
  })
})
