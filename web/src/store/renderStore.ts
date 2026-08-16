import { create } from 'zustand'
import { EngineError, engineRender, engineSvg, type Manifest } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'

export type RenderStatus = 'idle' | 'rendering' | 'ready' | 'error'

export interface PanelRender {
  rev: number
  manifest: Manifest | null
  /** 已处理好的 SVG 文本（去掉 width/height，铺满容器） */
  svg: string | null
  status: RenderStatus
  /** 本次渲染是否为冷启动（worker 尚未 build） */
  cold: boolean
  cost: string
  error: string | null
  traceback: string
  warnings: string[]
  /** 脚本文件变了，当前 SVG 已过期 */
  stale: boolean
  /**
   * 引擎跟踪态：脚本已经领先于磁盘上的 PDF/PNG，显示必须走引擎而不是
   * /api/render，否则画布上看到的还是旧图。AI 改完脚本后置位。
   */
  tracked: boolean
  /** 最近一次成功渲染所用的 patches，用于判断是否需要重渲染 */
  lastPatches: string | null
  /** 已排队但尚未完成的 patches，避免同一批改动被重复排期 */
  wantPatches: string | null
}

const EMPTY: PanelRender = {
  rev: 0,
  manifest: null,
  svg: null,
  status: 'idle',
  cold: false,
  cost: '',
  error: null,
  traceback: '',
  warnings: [],
  stale: false,
  tracked: false,
  lastPatches: null,
  wantPatches: null,
}

/**
 * matplotlib 的 SVG 自带 pt 单位的 width/height，去掉后配合
 * preserveAspectRatio=none 才能精确铺满面板框（面板宽高由文档决定）。
 */
function prepareSvg(text: string): string {
  return text.replace(/<svg([^>]*)>/, (_m, attrs: string) => {
    const cleaned = attrs.replace(/\s(?:width|height)="[^"]*"/g, '')
    return `<svg${cleaned} preserveAspectRatio="none" style="width:100%;height:100%;display:block">`
  })
}

interface RenderState {
  byFile: Record<string, PanelRender>
  get: (fileId: string) => PanelRender
  patch: (fileId: string, next: Partial<PanelRender>) => void
  /** 渲染并取回 SVG；渲染中重复调用只保留最后一次待办 */
  render: (fileId: string, patches: unknown[]) => Promise<void>
  /** 脚本变更：转入引擎跟踪并清掉 lastPatches，让同步器立刻重建 */
  markStale: (fileIds: string[]) => void
  reset: (fileId: string) => void
}

/** 每个文件一份在途状态：busy 时只记最后一次待办，避免连发把 worker 淹没 */
const inflight = new Map<string, { busy: boolean; queued: unknown[] | null }>()

/**
 * 渲染请求看门狗：fetch 永不 settle（服务重启留下的半开连接、代理悬挂）时
 * busy 永远不释放，该面板从此渲染不动。阈值只兜连接悬挂，不是性能预算，
 * 按脚本 cost 取得刻意宽松——heavy 冷启动本身就是分钟级。
 */
const WATCHDOG_MS: Record<string, number> = {
  light: 2 * 60_000,
  medium: 5 * 60_000,
  heavy: 15 * 60_000,
}

function watchdogMs(fileId: string): number {
  // 调试后门：agent-browser 实测超时链路时把阈值压到秒级
  const dev = (window as { __MM_RENDER_TIMEOUT_MS__?: unknown }).__MM_RENDER_TIMEOUT_MS__
  if (typeof dev === 'number' && dev > 0) return dev
  const cost = useAssetStore.getState().byId[fileId]?.cost ?? ''
  return WATCHDOG_MS[cost] ?? WATCHDOG_MS.medium
}

export const useRenderStore = create<RenderState>((set, get) => ({
  byFile: {},

  get: (fileId) => get().byFile[fileId] ?? EMPTY,

  patch: (fileId, next) =>
    set((s) => ({
      byFile: { ...s.byFile, [fileId]: { ...(s.byFile[fileId] ?? EMPTY), ...next } },
    })),

  render: async (fileId, patches) => {
    const slot = inflight.get(fileId) ?? { busy: false, queued: null }
    inflight.set(fileId, slot)
    if (slot.busy) {
      slot.queued = patches
      return
    }
    slot.busy = true
    const patch = get().patch

    try {
      let current = patches
      for (;;) {
        patch(fileId, { status: 'rendering', error: null, traceback: '' })
        const ctrl = new AbortController()
        const timeoutMs = watchdogMs(fileId)
        let timedOut = false
        const watchdog = window.setTimeout(() => {
          timedOut = true
          ctrl.abort()
        }, timeoutMs)
        try {
          const res = await engineRender(fileId, current, ctrl.signal)
          const svgText = await engineSvg(fileId, res.rev, ctrl.signal)
          patch(fileId, {
            rev: res.rev,
            manifest: res.manifest,
            svg: prepareSvg(svgText),
            status: 'ready',
            cold: false,
            error: null,
            traceback: '',
            warnings: res.warnings ?? [],
            stale: false,
            lastPatches: JSON.stringify(current),
          })
        } catch (err) {
          // 在途期间又排了新改动：直接渲染最新的，别停在旧请求的错误上
          // （否则 wantPatches 已等于新改动，同步器会永远跳过它）
          if (slot.queued != null) {
            current = slot.queued
            slot.queued = null
            continue
          }
          // 失败时保留旧 SVG，用户还能看到上一版
          patch(fileId, {
            status: 'error',
            cold: false,
            error: timedOut
              ? `渲染超过 ${Math.round(timeoutMs / 60_000)} 分钟无响应，已断开请求；服务可能仍在后台运行，可稍后重试`
              : err instanceof Error
                ? err.message
                : String(err),
            traceback: err instanceof EngineError ? err.traceback : '',
          })
          return
        } finally {
          window.clearTimeout(watchdog)
        }
        if (slot.queued == null) break
        current = slot.queued
        slot.queued = null
      }
    } finally {
      slot.busy = false
    }
  },

  markStale: (fileIds) =>
    set((s) => {
      const byFile = { ...s.byFile }
      for (const id of fileIds) {
        // 面板可能还没渲染过（EMPTY），也要建条目，否则同步器看不到它
        const cur = byFile[id] ?? EMPTY
        byFile[id] = {
          ...cur,
          stale: true,
          tracked: true,
          lastPatches: null,
          wantPatches: null,
        }
      }
      return { byFile }
    }),


  reset: (fileId) =>
    set((s) => {
      const byFile = { ...s.byFile }
      delete byFile[fileId]
      inflight.delete(fileId)
      return { byFile }
    }),
}))

export const emptyRender = EMPTY
