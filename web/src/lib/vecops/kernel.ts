import { create } from 'zustand'

/**
 * 路径查找器内核（`vecops/`，ADR 0185 §五 M2）的**按需加载器**。
 *
 * - 不进首屏：`.wasm` 只在第一次用到路径查找器（或右键菜单 / 属性栏要显示那几个按钮时预取）才下载、编译；
 * - 加载不了（环境不让编译 WASM、网络拿不到、ABI 对不上、内嵌画布的单文件构建里没打包它）→ 状态变成
 *   `unavailable`，界面把这几个按钮灰掉、停上去说原因，**不报错、不卡住**；
 * - 内核跑崩了（WASM trap）→ 这一次回 null，实例扔掉，下次重新实例化。
 *
 * 编码格式与 `vecops/src/lib.rs` 开头那段同一套；这里只管搬数据，几何语义在 `lib/pathfinder.ts`。
 */

/** 与 `vecops/src/lib.rs` 的 `ABI_VERSION` 同步改 */
export const VECOPS_ABI = 1

export type VecopsStatus = 'idle' | 'loading' | 'ready' | 'unavailable'
/** 不可用的原因（决定灰掉时的那句说明） */
export type VecopsUnavailable = 'embedded' | 'failed'

interface Exports {
  memory: WebAssembly.Memory
  abi_version(): number
  alloc(n: number): number
  dealloc(p: number, n: number): void
  out_ptr(): number
  pathfinder(op: number, p: number, n: number): number
  offset(p: number, n: number, delta: number, join: number, miter: number): number
  outline(p: number, n: number, width: number, cap: number, join: number, miter: number): number
  simplify(p: number, n: number, tolerance: number): number
}

export interface VecopsKernel {
  pathfinder(op: number, input: readonly number[]): number[] | null
  offset(input: readonly number[], delta: number, join: number, miter: number): number[] | null
  outline(input: readonly number[], width: number, cap: number, join: number, miter: number): number[] | null
  simplify(input: readonly number[], tolerance: number): number[] | null
}

export const useVecopsStore = create<{ status: VecopsStatus; reason: VecopsUnavailable | null }>(() => ({
  status: 'idle',
  reason: null,
}))

type Source = () => Promise<ArrayBuffer | null>

/**
 * 缺省的字节来源。内嵌画布（Codex 的 MCP 画布）是单文件构建，宿主 CSP 不放行外部请求，`vite.mcp.config.ts`
 * 把 `VITE_VECOPS` 定成 `off`：这一支连同 `?url` 的导入一起被摇掉，单文件里没有这 600 多 KB。
 */
const defaultSource: Source = async () => {
  if (import.meta.env.VITE_VECOPS === 'off') return null
  const { default: url } = await import('./vecops.wasm?url')
  const res = await fetch(url)
  if (!res.ok) throw new Error(`vecops ${res.status}`)
  return res.arrayBuffer()
}

let source: Source = defaultSource
let bytes: ArrayBuffer | null = null
let instance: Exports | null = null
let pending: Promise<VecopsKernel | null> | null = null

/** 测试用：换字节来源并清掉已加载的实例 */
export function setVecopsSource(s: Source | null): void {
  source = s ?? defaultSource
  bytes = null
  instance = null
  pending = null
  useVecopsStore.setState({ status: 'idle', reason: null })
}

async function instantiate(): Promise<Exports> {
  const mod = await WebAssembly.instantiate(bytes!, {})
  const x = mod.instance.exports as unknown as Exports
  if (x.abi_version() !== VECOPS_ABI) throw new Error('vecops abi')
  return x
}

function call(fn: (x: Exports, p: number, n: number) => number, input: readonly number[]): number[] | null {
  const x = instance
  if (!x) return null
  let p = 0
  try {
    p = x.alloc(input.length)
    new Float64Array(x.memory.buffer, p, input.length).set(input)
    const n = fn(x, p, input.length)
    const out = n > 0 ? Array.from(new Float64Array(x.memory.buffer, x.out_ptr(), n)) : null
    x.dealloc(p, input.length)
    return out
  } catch {
    // trap 之后线性内存的状态不可信：扔掉实例，下次用到时重新实例化
    instance = null
    void instantiate().then(
      (fresh) => {
        instance = fresh
      },
      () => useVecopsStore.setState({ status: 'unavailable', reason: 'failed' }),
    )
    return null
  }
}

const kernel: VecopsKernel = {
  pathfinder: (op, input) => call((x, p, n) => x.pathfinder(op, p, n), input),
  offset: (input, delta, join, miter) => call((x, p, n) => x.offset(p, n, delta, join, miter), input),
  outline: (input, width, cap, join, miter) => call((x, p, n) => x.outline(p, n, width, cap, join, miter), input),
  simplify: (input, tolerance) => call((x, p, n) => x.simplify(p, n, tolerance), input),
}

/** 已经加载好就同步拿到（命令的 `available()` 用）；没加载 / 加载不了 = null */
export function vecopsReady(): VecopsKernel | null {
  return instance ? kernel : null
}

/** 加载内核（只加载一次；并发调用共用同一个 Promise）。加载不了 = null，状态里写明原因 */
export function loadVecops(): Promise<VecopsKernel | null> {
  if (instance) return Promise.resolve(kernel)
  if (useVecopsStore.getState().status === 'unavailable') return Promise.resolve(null)
  if (pending) return pending
  useVecopsStore.setState({ status: 'loading', reason: null })
  pending = (async () => {
    try {
      if (!bytes) bytes = await source()
      if (!bytes) {
        useVecopsStore.setState({ status: 'unavailable', reason: 'embedded' })
        return null
      }
      instance = await instantiate()
      useVecopsStore.setState({ status: 'ready', reason: null })
      return kernel
    } catch {
      useVecopsStore.setState({ status: 'unavailable', reason: 'failed' })
      return null
    } finally {
      pending = null
    }
  })()
  return pending
}
