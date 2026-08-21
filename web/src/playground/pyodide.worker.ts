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
  /** Emscripten 虚拟 FS。完整性摘要经它读字节——**绕开 Python 解释器**。 */
  FS: { readFile(path: string, opts?: { encoding?: string }): Uint8Array }
}

let pyodide: PyodideLike | null = null
let handleFn: ((s: string) => string) | null = null
/** 用户脚本在虚拟 FS 里的绝对路径，`load` 成功时记下——**存在 JS 这一侧**。 */
let workspacePath = ''

/**
 * 工作区源文件的 SHA-256，**在 Python 之外算**。
 *
 * 为什么不能让 Python 自己算（codex 审查 P2，判断是对的）：用户脚本跑在
 * **同一个解释器**里，而且跑在这次核对**之前**。它完全可以在改掉自己的文件
 * 之后 monkeypatch `builtins.open` 或换掉 `hashlib.sha256`、甚至直接改
 * `sys.modules['browser']` 的全局，让 `source_status` 继续回报原来的摘要。
 * 那样界面会宣称「未改动」，而实际执行的文件已经变了——**一个能被它所校验的
 * 代码改写的校验，不叫校验**，而这条状态是当作独立验证展示给用户的。
 *
 * 所以字节由 `pyodide.FS.readFile` 直接从 Emscripten FS 取，摘要由 Worker 的
 * Web Crypto 算，全程不经过用户能触及的 Python 名字空间。
 * （`import js` 这类反向逃逸由 `browser_imports` 在执行前就拦掉了。）
 */
async function fsDigest(path: string): Promise<{ sha256: string; bytes: number }> {
  const data = pyodide!.FS.readFile(path, { encoding: 'binary' })
  // 复制进独立的 ArrayBuffer：FS 给的是 WASM 堆上的视图，堆一增长就失效
  const copy = new Uint8Array(data)
  const buf = await crypto.subtle.digest('SHA-256', copy)
  const sha256 = [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
  return { sha256, bytes: copy.byteLength }
}

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
  // 完整性摘要**以 FS 上的字节为准**，覆盖掉 Python 自己报的那个（理由见
  // fsDigest）。Python 那份仍然存在且被 CPython 测试盖着——它验的是引擎语义，
  // 这里验的是「实际躺在虚拟 FS 上的那个文件」，两者要的东西不同。
  workspacePath = `/workspace/${typeof out.script === 'string' ? out.script : 'figure.py'}`
  try {
    const d = await fsDigest(workspacePath)
    out.source_sha256 = d.sha256
    out.source_bytes = d.bytes
  } catch {
    // 拿不到 FS 或没有 Web Crypto：交空串，主线程据此显示「查不了」，
    // **绝不退回 Python 那份**——退回去就等于把刚拆掉的自证又装回来
    out.source_sha256 = ''
    out.source_bytes = 0
  }
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
    case 'sourceStatus': {
      // 每次都真的从虚拟 FS 读文件重算——缓存一个「上次算过的」哈希就等于
      // 又回到了「两个变量比自己」，那正是这条命令要取代的东西。
      // 读与算都在 Python 之外（见 fsDigest）。
      if (!pyodide || !workspacePath) return fail('bad_request', '还没有加载脚本')
      const script = workspacePath.slice(workspacePath.lastIndexOf('/') + 1)
      try {
        return { script, ...(await fsDigest(workspacePath)) }
      } catch (err) {
        return fail('source_unreadable',
          `读不到工作区里的源文件: ${err instanceof Error ? err.message : err}`)
      }
    }
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
