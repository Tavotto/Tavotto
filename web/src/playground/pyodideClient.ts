/**
 * Pyodide Worker 的主线程客户端：请求配对、按阶段计时的硬超时、Worker 生死。
 *
 * 超时模型（ADR 0007）：任意同步 Python 没有可靠的协作取消，所以超时 =
 * `worker.terminate()` = **整个会话作废**。到点后所有在途请求一起被拒，
 * UI 转入失败态并提供「重新运行 / 换文件 / 去桌面版」——绝不假装会话还能用。
 *
 * 计时按**阶段**分：下载 Pyodide 核心与科学栈是网络受限的（慢网络下几分钟
 * 都正常），用户脚本执行才是要掐死的那段。Worker 每进一个阶段发一条 progress，
 * 客户端据此重置计时器并换上该阶段的限额。
 */
import { EngineError } from '@/lib/api'
import {
  isWorkerResponse,
  type DistributiveOmit,
  type FigureChoice,
  type LoadResult,
  type OpenResult,
  type PlaygroundFailure,
  type PlaygroundPhase,
  type RenderResult,
  type WorkerRequest,
} from './protocol'

/** 各阶段的硬超时（毫秒）。网络阶段宽、执行阶段紧。 */
export const PHASE_TIMEOUT_MS: Record<PlaygroundPhase, number> = {
  runtime: 180_000, // Pyodide 核心下载 + 实例化（冷缓存慢网络也得活）
  engine: 60_000, // engine.zip（几十 KiB）
  packages: 300_000, // matplotlib + 科学栈下载
  script: 20_000, // **用户脚本执行**——死循环在这里被掐死
  figures: 30_000, // 捕获 + 缩略图
}
/** 无阶段请求（open/render/preview）的硬超时。 */
export const REQUEST_TIMEOUT_MS = 30_000

export class PlaygroundError extends Error {
  failure: PlaygroundFailure
  constructor(failure: PlaygroundFailure) {
    super(failure.message)
    this.failure = failure
  }
  /** 渲染链路（renderStore）认的是 EngineError——按同一形状转出去。 */
  toEngineError(): EngineError {
    return new EngineError(
      this.failure.message,
      this.failure.traceback ?? '',
      this.failure.code,
      this.failure.modules?.[0] ?? '',
    )
  }
}

interface Pending {
  resolve: (v: unknown) => void
  reject: (e: PlaygroundError) => void
  timer: number
  phased: boolean
}

export class PlaygroundClient {
  private worker: Worker | null = null
  private pending = new Map<number, Pending>()
  private seq = 0
  private dead: PlaygroundFailure | null = null
  /** 加载进度（UI 的阶段列表跟它走） */
  onProgress: ((phase: PlaygroundPhase) => void) | null = null

  start(): void {
    if (this.worker) return
    this.worker = new Worker(new URL('./pyodide.worker.ts', import.meta.url), {
      type: 'module',
    })
    this.worker.onmessage = (ev) => this.onMessage(ev)
    // Worker 自身崩溃（脚本加载失败、OOM 被杀）：会话一次性作废
    this.worker.onerror = () =>
      this.kill({ code: 'worker_crashed', message: 'Python Worker 崩溃了' })
  }

  /** 会话是否已被终止（超时 / 崩溃 / dispose）。 */
  get terminated(): boolean {
    return this.dead != null
  }

  init(pyodideBaseUrl: string, engineZipUrl: string): Promise<void> {
    return this.request({ type: 'init', pyodideBaseUrl, engineZipUrl }, true).then(() => undefined)
  }

  async load(
    filename: string,
    source: string,
    supportedRoots: Record<string, string>,
  ): Promise<LoadResult> {
    const r = (await this.request(
      { type: 'load', filename, source, supportedRoots },
      true,
    )) as Record<string, unknown>
    return {
      figures: (r.figures as FigureChoice[]) ?? [],
      log: typeof r.log === 'string' ? r.log : '',
      truncated_figures: typeof r.truncated_figures === 'number' ? r.truncated_figures : 0,
    }
  }

  async open(stem: string): Promise<OpenResult> {
    return (await this.request({ type: 'open', stem }, false)) as OpenResult
  }

  async render(
    stem: string,
    patches: unknown[],
    previewDpi?: number,
    signal?: AbortSignal,
  ): Promise<RenderResult> {
    return (await this.request({ type: 'render', stem, patches, previewDpi }, false, signal)) as RenderResult
  }

  async previewPng(
    stem: string,
    patches: unknown[],
    width: number,
    signal?: AbortSignal,
  ): Promise<string> {
    const r = (await this.request({ type: 'previewPng', stem, patches, width }, false, signal)) as {
      png?: string
    }
    return typeof r.png === 'string' ? r.png : ''
  }

  /** 主动收尾（换文件 / 组件卸载）。幂等。 */
  dispose(): void {
    this.kill({ code: 'disposed', message: 'playground 会话已结束' })
  }

  // ---------------- 内部 ----------------

  private request(
    msg: DistributiveOmit<WorkerRequest, 'id'>,
    phased: boolean,
    signal?: AbortSignal,
  ): Promise<unknown> {
    if (this.dead) return Promise.reject(new PlaygroundError(this.dead))
    if (!this.worker) this.start()
    const id = ++this.seq
    return new Promise((resolve, reject) => {
      const entry: Pending = {
        resolve,
        reject,
        phased,
        timer: this.arm(phased ? PHASE_TIMEOUT_MS.runtime : REQUEST_TIMEOUT_MS),
      }
      this.pending.set(id, entry)
      // 取消（渲染看门狗）只放弃**这一条**的结果；Python 没有协作中断，
      // 真正的硬取消是整个会话的超时 terminate
      signal?.addEventListener('abort', () => {
        const p = this.pending.get(id)
        if (!p) return
        this.pending.delete(id)
        window.clearTimeout(p.timer)
        p.reject(new PlaygroundError({ code: 'aborted', message: '请求已取消' }))
      })
      this.worker!.postMessage({ id, ...msg })
    })
  }

  private arm(ms: number): number {
    return window.setTimeout(() => {
      // 超时 = 会话作废。UI 拿到 code 之后提供重来/换文件/桌面版的出口。
      this.kill({ code: 'timeout', message: `playground 请求超过 ${Math.round(ms / 1000)}s 上限` })
    }, ms)
  }

  private onMessage(ev: MessageEvent): void {
    const data: unknown = ev.data
    // 形状闸门：Worker 里跑的是访客自己的 Python，postMessage 它也摸得到。
    // id 对不上号、形状不合法的一律丢弃，绝不解释成任何东西。
    if (!isWorkerResponse(data)) return
    const p = this.pending.get(data.id)
    if (!p) return
    if ('progress' in data) {
      if (p.phased) {
        window.clearTimeout(p.timer)
        p.timer = this.arm(PHASE_TIMEOUT_MS[data.progress])
        this.onProgress?.(data.progress)
      }
      return
    }
    this.pending.delete(data.id)
    window.clearTimeout(p.timer)
    if (data.ok) p.resolve(data.result)
    else p.reject(new PlaygroundError(data))
  }

  private kill(failure: PlaygroundFailure): void {
    if (this.dead) return
    this.dead = failure
    this.worker?.terminate()
    this.worker = null
    for (const [, p] of this.pending) {
      window.clearTimeout(p.timer)
      p.reject(new PlaygroundError(failure))
    }
    this.pending.clear()
  }
}
