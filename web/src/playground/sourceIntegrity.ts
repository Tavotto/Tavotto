/**
 * 「源文件未被修改」的**可验证**形式。
 *
 * 以前这句话的根据是 `session.loadedSource === session.originalSource`
 * ——两个变量指向同一个 JS 字符串，恒真，什么也没证明。现在的根据是两个
 * 隔着 Worker 边界、由两套实现算出来的 sha256：
 *
 *   * 主线程：`crypto.subtle.digest('SHA-256', TextEncoder(原文))`——
 *     用户交出来的那份字节；
 *   * Worker：`engine/browser.py` 的 `source_status` 把
 *     `/workspace/<脚本>` **从虚拟 FS 读回来**再 `hashlib.sha256`——
 *     真正被 `runpy` 执行的那个文件。
 *
 * 两个数相等，才显示「未改动」。没验完不显示，验不了就说验不了
 * （`crypto.subtle` 只在安全上下文里有；这不是失败，是「查不了」）。
 * 不相等是**不变式失效**，按高严重度报，绝不轻描淡写。
 */

/** UI 只认这四种。`checking` 是初值——没验完就不许说「未改动」。 */
export type IntegrityVerdict = 'checking' | 'unchanged' | 'changed' | 'unavailable'

export interface SourceIntegrity {
  verdict: IntegrityVerdict
  /** 主线程按用户原文算的 sha256（hex，小写）；算不出为空串 */
  originalSha256: string
  /** Worker 从虚拟 FS 读回来算的 sha256；还没验过为空串 */
  workspaceSha256: string
  /** verdict === 'unavailable' 时的机器可读原因 */
  reason?: 'no_subtle_crypto' | 'worker_error'
  /** 上一次核对完成的时刻（ms）——「显示的状态对应一次真正完成过的验证」 */
  verifiedAt?: number
}

/** Web Crypto 在安全上下文之外没有 `subtle`（http:// 的局域网地址就是）。 */
export function canHashLocally(): boolean {
  return typeof globalThis.crypto?.subtle?.digest === 'function'
}

/** 文本的 UTF-8 字节的 SHA-256，hex 小写。拿不到 Web Crypto 时回空串。 */
export async function sha256Hex(text: string): Promise<string> {
  if (!canHashLocally()) return ''
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** 两个哈希 → 结论。任一侧缺失即「查不了」，绝不含糊成「未改动」。 */
export function compareHashes(
  originalSha256: string,
  workspaceSha256: string,
  now: number,
): SourceIntegrity {
  if (!originalSha256) {
    return { verdict: 'unavailable', originalSha256, workspaceSha256, reason: 'no_subtle_crypto' }
  }
  if (!workspaceSha256) {
    return { verdict: 'unavailable', originalSha256, workspaceSha256, reason: 'worker_error' }
  }
  return {
    verdict: originalSha256 === workspaceSha256 ? 'unchanged' : 'changed',
    originalSha256,
    workspaceSha256,
    verifiedAt: now,
  }
}

/** 界面上给人看的短哈希：`8b82c10…f327`。空串原样返回。 */
export function shortHash(hex: string): string {
  return hex.length > 16 ? `${hex.slice(0, 7)}…${hex.slice(-4)}` : hex
}
