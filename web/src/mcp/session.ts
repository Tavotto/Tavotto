import type { Manifest } from '@/lib/api'
import { setEngineTransport, type EngineTransport } from '@/lib/engineTransport'
import { EngineError } from '@/lib/api'
import { msg } from '@/i18n'
import { embeddedFileIdFor, seedEmbeddedSession } from '@/embedded/session'
import type { AppsBridge, ToolCallResult } from './appsBridge'

/**
 * MCP 会话 ↔ Tavotto 前端的既有 store。
 *
 * **真相在服务端**：session_id、manifest、SVG、patch hash 全部来自
 * `tavotto_open_figure` / `tavotto_apply_overrides` 的响应。iframe 里只放
 * 「现在选中了哪个元素、面板摆在哪」这类临时 UI 状态；`localStorage` 与
 * `widgetState` **一个字节的业务数据都不存**——iframe 随时会被 host 重建，
 * 存在那里的东西就是随时会丢的东西。
 */

/** `tavotto_open_figure` 的结构化返回（server.py 的 structuredContent）。 */
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
  /** Python 侧渲染好的成文——只作没有 message（旧引擎）时的回退 */
  text: string
  /**
   * 可翻译描述符（issue #30）：宿主 webview 按自己的 locale 用
   * `errors:preflight.<key>` 渲染。key/params 与前端求值器逐字对齐
   * （golden vectors 看护）。
   */
  message?: { key: string; params: Record<string, unknown> }
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

export const fileIdFor = embeddedFileIdFor

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
    // traceback / module 要穿透：BridgeError.payload 带着它们，丢掉的话
    // 内嵌画布上 script_error 的本地化包装拿不到诊断末行、ErrorBlock 也
    // 没有可展开的 traceback（HTTP 那条路一直有）
    throw new EngineError(
      message,
      typeof body.traceback === 'string' ? body.traceback : '',
      code,
      typeof body.module === 'string' ? body.module : '',
    )
  }
  return body
}

/**
 * 装一条走 MCP `tools/call` 的引擎传输。
 *
 * 这**不是第二套渲染路径**：`tavotto_apply_overrides` 落到的是同一个
 * `pool.EngineWorker.override`。换掉的只是「消息怎么送过去」——
 * iframe 里没有可连的 HTTP 服务（sidecar 端口是动态的，MCP Apps 的 CSP
 * 也不允许连），所以走 host 的 JSON-RPC。
 */
export function installMcpTransport(bridge: AppsBridge): () => void {
  const transport: EngineTransport = {
    async render(id, patches, opts) {
      const sid = sessionIdFor(id)
      if (!sid) throw new EngineError(`没有这个面板的 MCP 会话: ${id}`, '', 'no_session', '')
      // signal 必须转下去：renderStore 的看门狗（按脚本 cost 分 2/5/15 分钟）
      // 就靠它取消，丢掉的话内嵌画布里一次卡死的渲染永远转下去
      const res = await bridge.callTool(
        'tavotto_apply_overrides',
        {
          session_id: sid,
          patches,
          ...(opts?.previewDpi ? { preview_dpi: opts.previewDpi } : {}),
        },
        undefined,
        opts?.signal,
      )
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
 * 把 `tavotto_open_figure` 的结果灌进既有 stores，让画布把它当成一个普通面板。
 *
 * 种子逻辑在 `embedded/session.ts`（浏览器 playground 与这里共用同一份，
 * 不许各自复制然后漂移）；MCP 特有的只有「fileId ↔ session_id」这张表。
 */
export function seedSession(open: OpenFigureResult): { panelId: string; fileId: string } {
  sessionOf.set(fileIdFor(open.stem), open.session_id)
  return seedEmbeddedSession(
    {
      stem: open.stem,
      project: open.project,
      script: open.script,
      cost: open.cost,
      manifest: open.manifest,
      svg: open.svg,
      renderRevision: open.render_revision,
      warnings: open.warnings,
    },
    msg('history.mcpOpenFigure', undefined, 'workspace'),
  )
}
