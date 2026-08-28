import type { Manifest, PanelInfo } from '@/lib/api'
import { VECTOR_PREVIEW, type PreviewMetadata } from '@/lib/previewBudget'
import type { UiMessage } from '@/i18n'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { newId } from '@/lib/id'
import type { PanelObject } from '@/types/document'

/**
 * 内嵌会话的通用种子层：把「一张已经渲染好的图」灌进既有 stores，
 * 让画布把它当成一个普通面板。
 *
 * 两个消费方：Codex 的 MCP 画布（`mcp/session.ts`）与浏览器 playground
 * （`playground/`）。它们的区别只在「渲染请求怎么送出去」（tools/call vs
 * Pyodide Worker）——**种进 stores 的东西必须一模一样**，否则拖拽、命中、
 * undo 在两边就不再是同一件事。这层从 MCP 会话里抽出来，就是为了不让
 * 第二个消费方复制一份然后各自漂移。
 *
 * 灌的东西一件不多：assetStore 一条素材、documentStore 一个面板、renderStore
 * 一份「已经画好了」的渲染态。之后的拖拽、命中测试、属性编辑、undo/redo
 * 全部是既有代码在跑。
 */
export interface EmbeddedFigure {
  /** 引擎里的 stem；面板的 fileId 是 `${stem}.pdf`（`embeddedFileIdFor`） */
  stem: string
  /** 素材面板显示的目录/项目标识（playground 里是虚拟工作区） */
  project: string
  /** 产出这张图的脚本名 */
  script: string
  /** 渲染开销档位（渲染看门狗按它分级）；缺省 medium */
  cost?: string
  manifest: Manifest
  svg: string | null
  /**
   * 这一版的预览表示法（ADR 0021）。缺省按 `vector` 解读；`svg` 为 null 而
   * 引擎给出 `raster` 时，画布走位图显示——**编辑语义一个字都不变**。
   */
  preview?: PreviewMetadata
  renderRevision?: number
  warnings?: string[]
}

export const embeddedFileIdFor = (stem: string) => `${stem}.pdf`

export function seedEmbeddedSession(
  fig: EmbeddedFigure,
  historyLabel: UiMessage,
): { panelId: string; fileId: string } {
  const [wMm, hMm] = fig.manifest.size_mm
  const fileId = embeddedFileIdFor(fig.stem)

  const info: PanelInfo = {
    id: fileId,
    name: fig.stem,
    folder: fig.project,
    kind: 'pdf',
    native_w_mm: wMm,
    native_h_mm: hMm,
    mtime: 0,
    script: fig.script,
    cost: fig.cost ?? 'medium',
  }
  useAssetStore.setState({
    byId: { [fileId]: info },
    panels: [info],
    figuresDir: fig.project,
    loaded: true,
    loading: false,
    error: null,
  })

  const panelId = newId('o')
  const panel: PanelObject = {
    id: panelId,
    type: 'panel',
    x: 0,
    y: 0,
    w: wMm,
    h: hMm,
    fileId,
    fileKind: 'pdf',
    nativeW: wMm,
    nativeH: hMm,
    script: fig.script,
    cost: fig.cost,
    overrides: [],
  }

  const store = useDocumentStore.getState()
  store.commit(historyLabel, (d) => {
    d.name = fig.stem
    // 页面就是这张图自己的尺寸：内嵌画布编辑的是**一张图**，不是拼版
    d.page = { w: wMm, h: hMm }
    d.objects = [panel]
    d.guides = []
  })
  // 打开动作不该出现在撤销栈里（用户的第一次撤销要回到「刚打开的样子」）
  useDocumentStore.setState({ past: [], future: [], dirty: false })

  const key = renderKey(fileId, [])
  useRenderStore.setState({
    byKey: {
      [key]: {
        fileId,
        rev: fig.renderRevision ?? 1,
        manifest: fig.manifest,
        svg: fig.svg ? prepareEmbeddedSvg(fig.svg) : null,
        preview: fig.preview ?? VECTOR_PREVIEW,
        status: 'ready',
        error: null,
        code: '',
        module: '',
      projectEnv: null,
      dependencyRepair: null,
        traceback: '',
        warnings: fig.warnings ?? [],
        timings: {},
        stale: false,
        lastPatches: '[]',
        wantPatches: '[]',
        previewDpi: null,
      },
    },
    // 文件级跟踪位：显示必须走引擎产物，而不是并不存在的 /api/render
    tracked: { [fileId]: true },
    latest: { [fileId]: key },
    building: {},
  })

  // 直接进图内编辑态：内嵌画布存在的全部理由就是改图里的元素
  useUiStore.getState().setElementPanel(panelId)
  return { panelId, fileId }
}

/**
 * matplotlib 的 SVG 自带 pt 单位的 width/height，去掉后配合
 * preserveAspectRatio=none 才能精确铺满面板框。与 renderStore 里那份同源
 * ——种子数据也必须过同一道处理，否则第一帧与之后每一帧的尺寸口径不同。
 */
export function prepareEmbeddedSvg(text: string): string {
  return text.replace(/<svg([^>]*)>/, (_m, attrs: string) => {
    const cleaned = attrs.replace(/\s(?:width|height)="[^"]*"/g, '')
    return `<svg${cleaned} preserveAspectRatio="none" style="width:100%;height:100%;display:block">`
  })
}
