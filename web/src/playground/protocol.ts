/**
 * 浏览器 playground 的 Worker RPC 协议（主线程 ↔ Pyodide Worker）。
 *
 * 纪律（ADR 0007）：
 *   * 每条请求带自增 id，响应必须回显同一个 id；
 *   * 主线程**只接受**「id 对得上 + 形状合法」的消息——Worker 里跑的是
 *     访客自己的 Python，它拿得到 Worker 全局的 postMessage，所以任何
 *     来路不明的消息都当不存在；
 *   * 失败一律是结构化的 `{ code, message, ... }`，code 稳定、文案随意。
 */

/** 加载阶段（进度条按它逐步点亮；不假造百分比） */
export const PHASES = ['runtime', 'engine', 'packages', 'script', 'figures'] as const
export type PlaygroundPhase = (typeof PHASES)[number]

export interface PlaygroundFailure {
  code: string
  message: string
  traceback?: string
  log?: string
  /** code === 'unsupported_import' 时缺的模块名列表 */
  modules?: string[]
  /** code === 'missing_file' 时脚本要读的那个文件 */
  filename?: string
  line?: number
}

export type WorkerRequest =
  | { id: number; type: 'init'; pyodideBaseUrl: string; engineZipUrl: string }
  | {
      id: number
      type: 'load'
      filename: string
      source: string
      /** import 根名 → Pyodide 包名（权威在 packaging/playground-runtime.json） */
      supportedRoots: Record<string, string>
    }
  | { id: number; type: 'open'; stem: string }
  | { id: number; type: 'render'; stem: string; patches: unknown[]; previewDpi?: number }
  | { id: number; type: 'previewPng'; stem: string; patches: unknown[]; width: number }

/** 联合类型上的分配式 Omit（普通 Omit 会把联合坍成公共字段） */
export type DistributiveOmit<T, K extends keyof never> = T extends unknown ? Omit<T, K> : never

export type WorkerResponse =
  | { id: number; ok: true; result: unknown }
  | ({ id: number; ok: false } & PlaygroundFailure)
  | { id: number; progress: PlaygroundPhase }

export interface FigureChoice {
  stem: string
  size_mm: [number, number]
  /** 选择器缩略图（base64 PNG；生成失败为空串，选择器退回文字条目） */
  preview: string
}

export interface LoadResult {
  figures: FigureChoice[]
  log: string
  truncated_figures: number
}

export interface OpenResult {
  stem: string
  script: string
  manifest: import('@/lib/api').Manifest
  svg: string
  patch_hash: string
  render_revision: number
  warnings: string[]
}

export interface RenderResult {
  manifest: import('@/lib/api').Manifest
  svg: string
  warnings: string[]
  patch_hash: string
  render_revision: number
}

/** 主线程收消息时的形状闸门——不合形状的一律丢弃，绝不解释成别的。 */
export function isWorkerResponse(v: unknown): v is WorkerResponse {
  if (typeof v !== 'object' || v === null) return false
  const m = v as Record<string, unknown>
  if (typeof m.id !== 'number') return false
  if (typeof m.progress === 'string') {
    return (PHASES as readonly string[]).includes(m.progress)
  }
  if (m.ok === true) return 'result' in m
  if (m.ok === false) return typeof m.code === 'string' && typeof m.message === 'string'
  return false
}
