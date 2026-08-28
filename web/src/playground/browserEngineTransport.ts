/**
 * EngineTransport 的浏览器 playground 实现。
 *
 * 与 MCP 那条（`mcp/session.ts` 的 installMcpTransport）同一地位：**不是
 * 第二套渲染路径**，落到的是同一份 `overrides.apply` + `build_manifest`
 * ——只是它们跑在本机 Pyodide 里而不是桌面 worker 子进程里。画布、stores、
 * useEngineSync 对此一无所知。
 */
import type { EngineTransport } from '@/lib/engineTransport'
import { setEngineTransport } from '@/lib/engineTransport'
import { EngineError } from '@/lib/api'
import { PlaygroundClient, PlaygroundError } from './pyodideClient'

/** 面板 fileId（`${stem}.pdf`，embeddedFileIdFor 的约定）→ 引擎 stem */
const stemOf = (fileId: string) => fileId.replace(/\.pdf$/, '')

const rethrow = (err: unknown): never => {
  if (err instanceof PlaygroundError) throw err.toEngineError()
  throw err instanceof EngineError
    ? err
    : new EngineError(err instanceof Error ? err.message : String(err))
}

export function installBrowserTransport(client: PlaygroundClient): () => void {
  const transport: EngineTransport = {
    async render(id, patches, opts) {
      const r = await client
        .render(stemOf(id), patches, opts?.previewDpi, opts?.signal)
        .catch(rethrow)
      return {
        rev: r.render_revision,
        manifest: r.manifest,
        // raster 档下引擎不把 SVG 交出 Worker 边界（ADR 0022）：画布改用
        // 位图，而位图这条路 playground 本来就有（下面的 previewPngUrl）。
        svg: r.svg ?? undefined,
        preview: r.preview,
        warnings: r.warnings ?? [],
        timings: {},
      }
    },
    async previewPngUrl(id, patches, bucket, signal) {
      const b64 = await client.previewPng(stemOf(id), patches, bucket, signal).catch(rethrow)
      if (!b64) throw new EngineError('位图预览为空', '', 'render_error', '')
      return `data:image/png;base64,${b64}`
    },
    // Worker 里没有可寻址的 HTTP 资源：回 null，PanelView 退回 SVG 显示
    panelSrc: () => null,
  }
  return setEngineTransport(transport)
}
