/**
 * 开局读当前项目（`GET /api/project`）的连接层有界重试（#1175）。
 *
 * 冻结 exe 刚起时端口可能还没接受连接：fetch 直接 reject（没有 HTTP 状态）。那不是
 * 「没有项目」，一次失败就落到 Picker 会让页面永远停在「选择项目」。这里只重试**没有响应**
 * 的失败；有 HTTP 状态的（调用方用 `hasResponse` 判：4xx / 5xx）原样抛出、不重试。
 * 总时长约 12 s，用完抛最后一次错误。等待可被 `cancelStartupProbe()` 取消（组件卸载、
 * 测试的全局 afterEach），取消后在等的调用抛 `STARTUP_PROBE_CANCELLED`。
 *
 * 本文件刻意不依赖任何 store / api：测试的 setup 要 import 它。
 */
export const PROJECT_CONNECT_RETRY_DELAYS_MS: readonly number[] = [500, 1000, 1500, 2000, 3000, 4000]
export const STARTUP_PROBE_CANCELLED = new Error('startup probe cancelled')

/**
 * 真正的连接层失败：浏览器 `fetch` 没拿到任何响应时 reject 的是 `TypeError`。
 * 收到了响应但体不是 JSON（代理回 HTML → `SyntaxError`）、或带 HTTP 状态的 ApiError 都不算。
 */
export const isConnectFailure = (e: unknown): boolean => e instanceof TypeError

/** 在等的重试循环各登记一个唤醒函数（并发两个循环互不覆盖）；取消时全部清掉。 */
const waiters = new Set<() => void>()
let epoch = 0

/** 当前探测代次：调用方开头捕获，之后每个 await 回来核对，不一致 = 已被取消 / 取代。 */
export function startupProbeEpoch(): number {
  return epoch
}

export function cancelStartupProbe(): void {
  epoch += 1
  for (const w of [...waiters]) w()
}

export async function retryOnConnectFailure<T>(
  fn: () => Promise<T>,
  hasResponse: (e: unknown) => boolean,
  delays: readonly number[] = PROJECT_CONNECT_RETRY_DELAYS_MS,
): Promise<T> {
  const mine = epoch
  for (let attempt = 0; ; attempt += 1) {
    try {
      const value = await fn()
      // 请求在飞时被取消（卸载 / 被替换挂载取代）：成功也不能再往下走，否则会覆盖新状态
      if (mine !== epoch) throw STARTUP_PROBE_CANCELLED
      return value
    } catch (e) {
      if (e === STARTUP_PROBE_CANCELLED) throw e
      if (mine !== epoch) throw STARTUP_PROBE_CANCELLED
      if (hasResponse(e) || attempt >= delays.length) throw e
      await new Promise<void>((resolve) => {
        const done = (): void => {
          clearTimeout(t)
          waiters.delete(done)
          resolve()
        }
        const t = setTimeout(done, delays[attempt])
        waiters.add(done)
      })
      if (mine !== epoch) throw STARTUP_PROBE_CANCELLED
    }
  }
}
