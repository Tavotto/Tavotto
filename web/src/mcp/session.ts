import type { Manifest } from '@/lib/api'
import { setEngineTransport, type EngineTransport } from '@/lib/engineTransport'
import type { PreviewMetadata } from '@/lib/previewBudget'
import { EngineError } from '@/lib/api'
import { msg } from '@/i18n'
import { embeddedFileIdFor, seedEmbeddedSession } from '@/embedded/session'
import type { PanelOverride } from '@/types/document'
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

/**
 * `tavotto_open_figure` 的结构化返回（server.py 的 structuredContent）——
 * `tavotto_session_state` 回的也是这个形状（多一个 `patches`）。
 */
export interface OpenFigureResult {
  ok: boolean
  session_id: string
  project: string
  stem: string
  script: string
  cost?: string
  manifest: Manifest
  svg: string | null
  /**
   * 这份 manifest / svg 对应的那组 patches（全量列表）。open 的结果里没有它
   * （刚打开 = 空）；`tavotto_session_state` 一定带，画布据此种账本。
   */
  patches?: PanelOverride[]
  /** 这一版的预览表示法（ADR 0022）；老 server 不返回它。 */
  preview?: PreviewMetadata
  /** `preview.mode === 'raster'` 时**同一次响应**里带回的受控尺寸位图。 */
  preview_png_base64?: string
  patch_hash: string
  render_revision?: number
  warnings?: string[]
  registry?: { parameterizable?: boolean | null; conflicts?: string[]; stems?: string[] }
  profile: { profile_id: string; profile_version: string; label?: string }
  preflight?: PreflightPayload
  /** server 按宿主体积上限省略过字段时的说明；在 = 这份结果不完整，要去取件。 */
  elided?: ElidedNote
}

/**
 * server 把 open 结果压进宿主体积上限时留下的说明（issue #457）：省了哪些字段、
 * 去哪儿取。画布不读它——判「要不要取件」只看 `isOpenResult`——它是给模型和
 * 排障的人看的。
 */
export interface ElidedNote {
  fields: string[]
  reason: string
  inline_bytes: number
  budget_bytes: number
  fetch_with: string
}

/**
 * 只接受**完整的** open 结果。
 *
 * `tavotto_apply_overrides` 的响应也挂着同一份 widget 资源，也就能用来初始化
 * 一个新 iframe——而它带着 `session_id` 与 `manifest`，只看这两项的话会被
 * 当成 open 结果收下。可它没有 `profile` / `project` / `script`：`McpApp`
 * 一读 `open.profile.profile_id` 就当场崩掉。server 按宿主体积上限省略过字段的
 * open 结果同样不完整——**不管省的是什么**：只省了 svg 时 manifest 还在、六项齐全，
 * 种下去却是一张空画布（iframe 里没有可退的 HTTP 位图）。所以 `elided` 在就一律
 * 去取件；没有 `elided` 的老 server 结果再按形状判：矢量图必须带 svg 字符串，
 * raster 档 svg 才允许是 null。这两种都走 `sessionIdOf` → 取件那条路。
 */
export function isOpenResult(v: unknown): v is OpenFigureResult {
  const o = v as OpenFigureResult | null
  if (!o || typeof o !== 'object') return false
  if (o.elided) return false
  const svgOk = typeof o.svg === 'string' || (o.svg === null && o.preview?.mode === 'raster')
  return (
    typeof o.session_id === 'string' &&
    !!o.manifest &&
    svgOk &&
    typeof o.project === 'string' &&
    typeof o.stem === 'string' &&
    typeof o.script === 'string' &&
    !!o.profile &&
    typeof o.profile.profile_id === 'string'
  )
}

/** 负载里认得出会话的把手——不完整的结果也有它，就能去取件。 */
export function sessionIdOf(v: unknown): string | null {
  const o = v as { session_id?: unknown } | null
  return o && typeof o === 'object' && typeof o.session_id === 'string' && o.session_id
    ? o.session_id
    : null
}

/**
 * 画布的取件通道：按 session_id 把会话此刻的完整状态拉回来。
 *
 * 走的是画布自己发的 `tools/call`——宿主直接代理给 server、原样返回，不进模型
 * 上下文、也不受宿主对工具结果事件副本的体积上限约束（open 的结果超过 1 MiB
 * 时 Codex 会把 `structuredContent` 整个置空，见 server.py 的
 * `HOST_EVENT_RESULT_CAP_BYTES`）。回来的形状与 open 相同，多一个 `patches`。
 */
export async function fetchSessionState(
  bridge: Pick<AppsBridge, 'callTool'>,
  sessionId: string,
): Promise<OpenFigureResult> {
  const body = unwrap(await bridge.callTool('tavotto_session_state', { session_id: sessionId }))
  if (!isOpenResult(body)) {
    throw new EngineError(
      `tavotto_session_state 回的不是完整的会话状态（有: ${Object.keys(body).join(', ')}）`,
      '',
      'bad_session_state',
      '',
    )
  }
  return body
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

/**
 * raster 档下最近一次渲染带回来的位图（ADR 0022）。
 *
 * **按变体存**，不是「最后一张」：同文件多变体时拿错一张就是「一个面板显示
 * 了另一个面板的图」——HTTP 那条路上正是为了这个才把 `/api/engine/png` 换成
 * 按 patches 出图的 `preview_png`。这里靠「与 manifest 同一次响应」天然配对，
 * 键只是把配对关系记下来。
 *
 * 一个会话只留最近一版：这是画布**此刻**要显示的东西，不是缓存。
 */
const rasterPngOf = new Map<string, { variant: string; url: string }>()

/** 拿到的是不是这一组 patches 自己的位图；不是就宁可没有。 */
function rasterPngFor(sessionId: string, patches: unknown[]): string | null {
  const hit = rasterPngOf.get(sessionId)
  return hit && hit.variant === JSON.stringify(patches) ? hit.url : null
}

function rememberRasterPng(sessionId: string, patches: unknown[], base64: unknown): void {
  if (typeof base64 !== 'string' || !base64) {
    rasterPngOf.delete(sessionId)
    return
  }
  rasterPngOf.set(sessionId, {
    variant: JSON.stringify(patches),
    // data: URL 而不是 blob:——base64 是从 JSON-RPC 里拿的，转成 blob 只是
    // 多复制一份，还多一条要人记得 revoke 的生命周期。
    url: `data:image/png;base64,${base64}`,
  })
}

export const fileIdFor = embeddedFileIdFor

export function sessionIdFor(fileId: string): string | null {
  return sessionOf.get(fileId) ?? null
}

/**
 * 工具结果 → 结构化负载；`isError` 一律转成带原因的异常，绝不静默当成成功。
 *
 * 成功的判据是**正面的** `ok === true`（Tavotto 每个工具的成功结果都带它）。宿主可能在
 * server 之前就把调用拦下、回一段不带 `isError` 的纯文字——WorkBuddy 5.6.2 的出口审查
 * 就是这样回的（`Sensitive MCP egress review is unavailable.`）。只认 `isError` /
 * `ok === false` 的话，那段文字会被当成一次成功的 apply，空结果顶掉画布上的图。
 * 没有结构化内容的失败带 `host_unstructured_result`，宿主原话作为诊断材料原样显示。
 */
export function unwrap(res: ToolCallResult): Record<string, unknown> {
  const body = (res.structuredContent ?? {}) as Record<string, unknown>
  if (res.isError || body.ok !== true) {
    const code =
      typeof body.code === 'string'
        ? body.code
        : res.structuredContent == null && !res.isError
          ? 'host_unstructured_result'
          : ''
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
      // raster 档的位图与 manifest 在**同一次响应**里（bridge `_render`）：
      // 另开一跳去取，取回来的可能已经是另一组 patches 的像素。
      rememberRasterPng(sid, patches, body.preview_png_base64)
      return {
        rev: Number(body.render_revision ?? 0),
        manifest: body.manifest as Manifest,
        svg: (body.svg as string) ?? undefined,
        preview: (body.preview as PreviewMetadata) ?? undefined,
        warnings: (body.warnings as string[]) ?? [],
        timings: (body.timings as Record<string, number>) ?? {},
      }
    },
    // 位图预览：MCP 那侧回 base64，转成 data URL 就是 `<img src>`。
    // 纯矢量的图不会走到这里（显示用 SVG），**raster 档非走不可**——
    // iframe 里没有可连的 HTTP 服务，拿不到位图就是整张图空白。
    async previewPngUrl(id, patches) {
      const sid = sessionIdFor(id)
      const url = sid ? rasterPngFor(sid, patches) : null
      if (url) return url
      throw new EngineError(
        'MCP 画布这一版没有位图预览（矢量图显示走引擎 SVG）', '', 'not_supported', '')
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
  const overrides = open.patches ?? []
  // 打开就是 raster 的图（#181 那一类）：第一帧的位图也在这次响应里。
  // 不记下来的话画布要等到用户改第一个值才有东西可显示。
  rememberRasterPng(open.session_id, overrides, open.preview_png_base64)
  return seedEmbeddedSession(
    {
      stem: open.stem,
      project: open.project,
      script: open.script,
      cost: open.cost,
      manifest: open.manifest,
      svg: open.svg,
      preview: open.preview,
      renderRevision: open.render_revision,
      warnings: open.warnings,
      overrides,
    },
    msg('history.mcpOpenFigure', undefined, 'workspace'),
  )
}
