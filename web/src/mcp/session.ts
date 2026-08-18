import type { Manifest, PanelInfo } from '@/lib/api'
import { setEngineTransport, type EngineTransport } from '@/lib/engineTransport'
import { EngineError } from '@/lib/api'
import { msg } from '@/i18n'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKey, useRenderStore } from '@/store/renderStore'
import { useUiStore } from '@/store/uiStore'
import { newId } from '@/lib/id'
import type { PanelObject } from '@/types/document'
import type { AppsBridge, ToolCallResult } from './appsBridge'

/**
 * MCP 会话 ↔ Magplot 前端的既有 store。
 *
 * **真相在服务端**：session_id、manifest、SVG、patch hash 全部来自
 * `magplot_open_figure` / `magplot_apply_overrides` 的响应。iframe 里只放
 * 「现在选中了哪个元素、面板摆在哪」这类临时 UI 状态；`localStorage` 与
 * `widgetState` **一个字节的业务数据都不存**——iframe 随时会被 host 重建，
 * 存在那里的东西就是随时会丢的东西。
 */

/** `magplot_open_figure` 的结构化返回（server.py 的 structuredContent）。 */
export interface OpenFigureResult {
  ok: boolean
  session_id: string
  project: string
  stem: string
  script: string
  cost?: string
  manifest: Manifest
  svg: string | null
  patch_hash: string
  render_revision?: number
  warnings?: string[]
  registry?: { parameterizable?: boolean | null; conflicts?: string[]; stems?: string[] }
  profile: { profile_id: string; profile_version: string; label?: string }
  preflight?: PreflightPayload
}

export interface PreflightIssuePayload {
  id: string
  severity: 'error' | 'warn' | 'not_verifiable' | 'suggestion'
  text: string
  object_ids: string[]
  gids: string[]
  detail?: Record<string, unknown>
}

export interface PreflightPayload {
  counts: Record<string, number>
  blocking: boolean
  errors: PreflightIssuePayload[]
  warnings: PreflightIssuePayload[]
  not_verifiable: PreflightIssuePayload[]
  suggestions: PreflightIssuePayload[]
}

/** 面板 id ↔ MCP 会话。一个 widget 目前只端一张图，留表是为了以后多图拼版。 */
const sessionOf = new Map<string, string>()

export const fileIdFor = (stem: string) => `${stem}.pdf`

export function sessionIdFor(fileId: string): string | null {
  return sessionOf.get(fileId) ?? null
}

/** 工具结果 → 结构化负载；`isError` 一律转成带原因的异常，绝不静默当成成功。 */
export function unwrap(res: ToolCallResult): Record<string, unknown> {
  const body = (res.structuredContent ?? {}) as Record<string, unknown>
  if (res.isError || body.ok === false) {
    const code = typeof body.code === 'string' ? body.code : ''
    const message =
      (typeof body.error === 'string' && body.error) ||
      res.content?.map((c) => c.text).join('\n') ||
      '工具调用失败'
    throw new EngineError(message, '', code, '')
  }
  return body
}

/**
 * 装一条走 MCP `tools/call` 的引擎传输。
 *
 * 这**不是第二套渲染路径**：`magplot_apply_overrides` 落到的是同一个
 * `pool.EngineWorker.override`。换掉的只是「消息怎么送过去」——
 * iframe 里没有可连的 HTTP 服务（sidecar 端口是动态的，MCP Apps 的 CSP
 * 也不允许连），所以走 host 的 JSON-RPC。
 */
export function installMcpTransport(bridge: AppsBridge): () => void {
  const transport: EngineTransport = {
    async render(id, patches, opts) {
      const sid = sessionIdFor(id)
      if (!sid) throw new EngineError(`没有这个面板的 MCP 会话: ${id}`, '', 'no_session', '')
      const res = await bridge.callTool('magplot_apply_overrides', {
        session_id: sid,
        patches,
        ...(opts?.previewDpi ? { preview_dpi: opts.previewDpi } : {}),
      })
      const body = unwrap(res)
      return {
        rev: Number(body.render_revision ?? 0),
        manifest: body.manifest as Manifest,
        svg: (body.svg as string) ?? undefined,
        warnings: (body.warnings as string[]) ?? [],
        timings: (body.timings as Record<string, number>) ?? {},
      }
    },
    // 位图预览：MCP 那侧回 base64，转成 data URL 就是 `<img src>`。
    // 纯矢量的图根本不会走到这里（显示用 SVG）。
    async previewPngUrl() {
      throw new EngineError(
        'MCP 画布不取位图预览（显示走引擎 SVG）', '', 'not_supported', '')
    },
    // iframe 里没有可寻址的 HTTP 资源：回 null，PanelView 退回 SVG 显示。
    panelSrc: () => null,
  }
  return setEngineTransport(transport)
}

/**
 * 把 `magplot_open_figure` 的结果灌进既有 stores，让画布把它当成一个普通面板。
 *
 * 灌的东西一件不多：assetStore 一条素材、documentStore 一个面板、renderStore
 * 一份「已经画好了」的渲染态。之后的拖拽、命中测试、属性编辑、undo/redo
 * 全部是既有代码在跑。
 */
export function seedSession(open: OpenFigureResult): { panelId: string; fileId: string } {
  const [wMm, hMm] = open.manifest.size_mm
  const fileId = fileIdFor(open.stem)
  sessionOf.set(fileId, open.session_id)

  const info: PanelInfo = {
    id: fileId,
    name: open.stem,
    folder: open.project,
    kind: 'pdf',
    native_w_mm: wMm,
    native_h_mm: hMm,
    mtime: 0,
    script: open.script,
    cost: open.cost ?? 'medium',
  }
  useAssetStore.setState({
    byId: { [fileId]: info },
    panels: [info],
    figuresDir: open.project,
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
    script: open.script,
    cost: open.cost,
    overrides: [],
  }

  const store = useDocumentStore.getState()
  store.commit(msg('history.mcpOpenFigure', undefined, 'workspace'), (d) => {
    d.name = open.stem
    // 页面就是这张图自己的尺寸：MCP 画布编辑的是**一张图**，不是拼版
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
        rev: open.render_revision ?? 1,
        manifest: open.manifest,
        svg: open.svg ? prepareSvg(open.svg) : null,
        status: 'ready',
        error: null,
        code: '',
        module: '',
        traceback: '',
        warnings: open.warnings ?? [],
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

  // 直接进图内编辑态：这块画布存在的全部理由就是改图里的元素
  useUiStore.getState().setElementPanel(panelId)
  return { panelId, fileId }
}

/**
 * matplotlib 的 SVG 自带 pt 单位的 width/height，去掉后配合
 * preserveAspectRatio=none 才能精确铺满面板框。与 renderStore 里那份同源
 * ——种子数据也必须过同一道处理，否则第一帧与之后每一帧的尺寸口径不同。
 */
function prepareSvg(text: string): string {
  return text.replace(/<svg([^>]*)>/, (_m, attrs: string) => {
    const cleaned = attrs.replace(/\s(?:width|height)="[^"]*"/g, '')
    return `<svg${cleaned} preserveAspectRatio="none" style="width:100%;height:100%;display:block">`
  })
}
