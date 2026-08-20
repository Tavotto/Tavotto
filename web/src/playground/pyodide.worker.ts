/// <reference lib="webworker" />
/**
 * 浏览器 playground 的 Pyodide Worker。
 *
 * 主线程绝不跑用户 Python——Pyodide 的初始化、包加载、脚本执行、live Figure、
 * manifest / override 全部在这个 Dedicated Worker 里。协议见 `protocol.ts`。
 *
 * Python 侧只有一扇门：`browser.handle(json) -> json`（引擎仓库的
 * `src/tavotto/engine/browser.py`，随 `engine.zip` 挂进 Pyodide FS），
 * 外加分类专用的 `browser_imports.classify_json`——它是纯标准库，
 * 让「脚本要 rdkit」在下载 matplotlib 那十几 MB **之前**就能拒绝。
 *
 * 超时与取消不在这里：任意同步 Python 没有可靠的协作取消，主线程的
 * PlaygroundClient 到点直接 `worker.terminate()`（ADR 0007）。
 */
import type { PlaygroundFailure, PlaygroundPhase, WorkerRequest } from './protocol'

interface PyodideLike {
  loadPackage(names: string[]): Promise<void>
  unpackArchive(buf: ArrayBuffer, format: string, opts?: { extractDir?: string }): void
  runPython(code: string): unknown
  pyimport(name: string): { handle?: (s: string) => string; classify_json?: (s: string) => string }
}

let pyodide: PyodideLike | null = null
let handleFn: ((s: string) => string) | null = null

const post = (m: unknown) => (self as unknown as DedicatedWorkerGlobalScope).postMessage(m)
const progress = (id: number, phase: PlaygroundPhase) => post({ id, progress: phase })

/** 结构化失败：Python 侧的 `{ok:false}` 响应原样透出，别的异常折成 code。 */
class Failure extends Error {
  detail: PlaygroundFailure
  constructor(detail: PlaygroundFailure) {
    super(detail.message)
    this.detail = detail
  }
}

const fail = (code: string, message: string, extra?: Partial<PlaygroundFailure>): never => {
  throw new Failure({ code, message, ...extra })
}

/** Python 边界调用：解析 JSON、`ok:false` 变异常，别让「失败」长得像「成功」。 */
function callPython(req: Record<string, unknown>): Record<string, unknown> {
  if (!handleFn) return fail('worker_crashed', 'Python 引擎还没就绪')
  const out = JSON.parse(handleFn(JSON.stringify(req))) as Record<string, unknown>
  if (out.ok !== true) {
    throw new Failure({
      code: typeof out.code === 'string' ? out.code : 'internal_error',
      message: typeof out.message === 'string' ? out.message : '引擎调用失败',
      traceback: typeof out.traceback === 'string' ? out.traceback : undefined,
      log: typeof out.log === 'string' ? out.log : undefined,
      modules: Array.isArray(out.modules) ? (out.modules as string[]) : undefined,
      filename: typeof out.filename === 'string' ? out.filename : undefined,
      line: typeof out.line === 'number' ? out.line : undefined,
    })
  }
  return out
}

async function init(id: number, pyodideBaseUrl: string, engineZipUrl: string): Promise<void> {
  progress(id, 'runtime')
  // 版本钉死在 packaging/playground-runtime.json（URL 由主线程传入）。
  // @vite-ignore：这是**有意的**运行时外部地址——Pyodide 不打进 bundle。
  const mod = (await import(/* @vite-ignore */ `${pyodideBaseUrl}pyodide.mjs`)) as {
    loadPyodide(opts: { indexURL: string }): Promise<PyodideLike>
  }
  pyodide = await mod.loadPyodide({ indexURL: pyodideBaseUrl })

  progress(id, 'engine')
  const res = await fetch(engineZipUrl)
  if (!res.ok) fail('runtime_failure', `engine.zip 下载失败（HTTP ${res.status}）`)
  pyodide!.unpackArchive(await res.arrayBuffer(), 'zip', { extractDir: '/engine' })
  // 与桌面 worker 同一纪律：engine 目录整个进 sys.path，模块之间平铺 import
  pyodide!.runPython(`import sys\nsys.path.insert(0, "/engine")`)
}

async function load(
  id: number,
  filename: string,
  source: string,
  supportedRoots: Record<string, string>,
): Promise<Record<string, unknown>> {
  if (!pyodide) return fail('worker_crashed', 'Pyodide 还没初始化')

  // 1. 纯标准库分类：不支持的依赖在下载任何科学栈之前就拒绝
  const imports = pyodide.pyimport('browser_imports')
  const cls = JSON.parse(
    imports.classify_json!(JSON.stringify({ source, supported_roots: supportedRoots })),
  ) as Record<string, unknown>
  if (cls.ok !== true) {
    throw new Failure({
      code: typeof cls.code === 'string' ? cls.code : 'internal_error',
      message: typeof cls.message === 'string' ? cls.message : 'import 分类失败',
      line: typeof cls.line === 'number' ? cls.line : undefined,
    })
  }
  const unsupported = (cls.unsupported as string[]) ?? []
  if (unsupported.length) {
    fail('unsupported_import', `浏览器环境里没有: ${unsupported.join(', ')}`, {
      modules: unsupported,
    })
  }

  // 2. 按需加载受支持的包。matplotlib/numpy 是引擎自己的依赖，恒在。
  progress(id, 'packages')
  const packages = new Set(['matplotlib', 'numpy', ...((cls.packages as string[]) ?? [])])
  try {
    await pyodide.loadPackage([...packages])
  } catch (err) {
    fail('runtime_failure', `Python 包加载失败: ${err instanceof Error ? err.message : err}`)
  }

  // 3. 引擎适配层就位（首次 import matplotlib 就发生在这里），再跑脚本
  progress(id, 'script')
  const browser = pyodide.pyimport('browser')
  handleFn = (s: string) => browser.handle!(s)
  const out = callPython({ cmd: 'load', filename, source })
  progress(id, 'figures')
  return out
}

async function dispatch(req: WorkerRequest): Promise<unknown> {
  switch (req.type) {
    case 'init':
      return init(req.id, req.pyodideBaseUrl, req.engineZipUrl)
    case 'load':
      return load(req.id, req.filename, req.source, req.supportedRoots)
    case 'open':
      return callPython({ cmd: 'open', stem: req.stem })
    case 'render':
      return callPython({
        cmd: 'render',
        stem: req.stem,
        patches: req.patches,
        ...(req.previewDpi ? { preview_dpi: req.previewDpi } : {}),
      })
    case 'previewPng':
      return callPython({
        cmd: 'preview_png',
        stem: req.stem,
        patches: req.patches,
        width: req.width,
      })
  }
}

self.onmessage = (ev: MessageEvent) => {
  const req = ev.data as WorkerRequest
  if (!req || typeof req !== 'object' || typeof req.id !== 'number' || typeof req.type !== 'string')
    return
  void (async () => {
    try {
      const result = (await dispatch(req)) ?? {}
      post({ id: req.id, ok: true, result })
    } catch (err) {
      if (err instanceof Failure) post({ id: req.id, ok: false, ...err.detail })
      else
        post({
          id: req.id,
          ok: false,
          code: 'runtime_failure',
          message: err instanceof Error ? err.message : String(err),
          traceback: err instanceof Error ? (err.stack ?? '') : '',
        })
    }
  })()
}
