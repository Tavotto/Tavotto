import type { EngineRenderOptions, EngineRenderResponse } from './api'

/**
 * 引擎往来的**可选替换传输层**。
 *
 * 画布这一整套（`canvas/*` + stores）只关心「发一组全量 patches，拿回 manifest
 * 与 SVG」。它跑在两个地方：
 *
 *   * Tavotto 自己的界面 —— 走 HTTP 打到本机 Flask（`lib/api.ts` 的
 *     `engineRender` / `enginePreviewPng` / `panelSrc`，行为一字未改）；
 *   * Codex 内嵌的 MCP App 画布 —— iframe 里**没有**可连的 HTTP 服务（sidecar
 *     端口是动态的，MCP Apps 的 CSP 也不许连），所有往来经 `tools/call` 回到
 *     Tavotto 的 MCP server。
 *
 * **这不是第二套渲染路径**：两侧最终都落到同一个 `pool.EngineWorker.override`，
 * 只是消息怎么送过去不同。拖拽、命中测试、吸附、undo、patch 状态一行都没有
 * 第二份实现。
 *
 * 设计上刻意做成「**没装就走默认，装了才改道**」而不是「默认实现也放在这里」：
 * 后者要 `import` 回 `lib/api`，而 `lib/api` 与 store 之间已经有依赖，绕成环
 * 之后模块初始化顺序一变就是 TDZ 崩溃；而且既有单测大量 `vi.mock('@/lib/api')`
 * 打桩 `engineRender`，把默认实现搬进闭包会让那些桩全部失效（实测炸了 7 个
 * 文件）。所以本模块**不 import 任何东西**，只存一个可选覆盖。
 */
export interface EngineTransport {
  /** 应用全量 patches 并重渲染；SVG 必须与 manifest 在同一次响应里 */
  render(id: string, patches: unknown[], opts?: EngineRenderOptions): Promise<EngineRenderResponse>
  /**
   * 按 patches 出高清位图（状态中立）。返回可直接给 `<img src>` 的地址
   * （blob URL 或 data URL）。
   */
  previewPngUrl(
    id: string,
    patches: unknown[],
    bucket: number,
    signal?: AbortSignal,
  ): Promise<string>
  /**
   * 素材/原图的显示地址。iframe 里没有可寻址的 HTTP 资源时返回 null，
   * 调用方退回 SVG 显示——绝不留一个连不上的 URL 让画布挂个碎图标。
   */
  panelSrc(id: string, kind: 'pdf' | 'raster', bucket: number, mtime?: number): string | null
}

let override: EngineTransport | null = null

/**
 * 安装一条替代传输（MCP widget 在挂载前调一次）。返回还原用的函数。
 * 传 null 即卸载，回到默认的 HTTP 路径。
 */
export function setEngineTransport(next: EngineTransport | null): () => void {
  const prev = override
  override = next
  return () => {
    override = prev
  }
}

/** 当前的替代传输；`null` = 走 `lib/api` 的默认 HTTP 实现。 */
export function engineTransport(): EngineTransport | null {
  return override
}
