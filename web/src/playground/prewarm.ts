/**
 * `/try` 的 Pyodide 预热：一台**至多一个**的暖机账本。
 *
 * 为什么只在 `/try` 上做：访客打开这个地址时意图已经很明确，闲置的那几秒
 * 拿来把 Pyodide 核心装起来是纯赚。营销首页（`/`、`/zh/`）**一个字节的
 * Pyodide 都不加载**——那是另一个仓库里的静态页面，与本模块没有任何连接。
 *
 * 边界（ADR 0007 的 §边界 一条都没松）：
 *   * 预热只到「核心 + engine.zip」为止。**不预下科学栈**——matplotlib
 *     那十几 MB 要等 import 分类说了算，见 `pyodide.worker.ts` 的 load；
 *   * 一个源文件 = 一个 Worker = 一个 Pyodide 会话仍然成立：暖着的 Worker
 *     **还没跑过任何用户代码**，所以它可以成为第一个会话；一旦它跑过脚本，
 *     换文件照旧 terminate + 新建，绝不复用被用过的解释器；
 *   * 预热是优化不是依赖：失败就悄悄退回 cold，用户真开会话时按正常路径
 *     重来一遍，不弹任何错误。
 *
 * 状态机（同一时刻只有一条初始化路径在跑）：
 *
 *     cold ──schedule/warm──▶ warming ──init ok──▶ ready
 *       ▲                        │                   │
 *       └────init 失败 / 被取走──┴───────────────────┘
 *
 * 「被取走」（consumed）不单列成状态：`take()` 把 client 交出去并把账本清回
 * cold，之后这个 client 的生死归会话管，账本不再持有它——两处都能 terminate
 * 同一个 Worker 才是真正的坑。
 */
import { PlaygroundClient } from './pyodideClient'
import { ENGINE_ZIP_NAME, PYODIDE_BASE_URL } from './runtime'

export type WarmState = 'cold' | 'warming' | 'ready'

/** `navigator.connection` 的可选形状——Safari / Firefox 里整个不存在。 */
interface NetworkInformationLike {
  saveData?: boolean
  effectiveType?: string
}

/** 明确「太慢，别在背景里替用户花流量」的取值。3g 不在此列（还够用）。 */
const TOO_SLOW = new Set(['slow-2g', '2g'])

/**
 * 现在适合预热吗？保守默认：**拿不到任何信息就预热**（多数浏览器如此），
 * 只有明确说了「省流量」或「非常慢」才不做。
 *
 * 一律特性检测：Network Information API 是 Chromium 专有的，直接读
 * `navigator.connection.saveData` 会在 Safari/Firefox 上抛。
 */
export function shouldPrewarm(nav: Navigator = navigator): boolean {
  const conn = (nav as Navigator & { connection?: NetworkInformationLike }).connection
  if (!conn || typeof conn !== 'object') return true
  if (conn.saveData === true) return false
  if (typeof conn.effectiveType === 'string' && TOO_SLOW.has(conn.effectiveType)) return false
  return true
}

/** engine.zip 与页面同目录（构建脚本放在 dist 根），按页面地址解析。 */
export function engineZipUrl(): string {
  return new URL(ENGINE_ZIP_NAME, document.baseURI).href
}

interface Idle {
  requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number
  cancelIdleCallback?: (h: number) => void
}

/**
 * 空闲时跑一次 `fn`。有 `requestIdleCallback` 用它（带 timeout 兜底，
 * 一直不空闲也要跑到），否则退回一个短 `setTimeout`。
 * 返回取消函数——用户在预热开始前就动手时，这次预热就不必发生了。
 */
export function onIdle(fn: () => void, delayMs = 400): () => void {
  const w = globalThis as unknown as Idle
  if (typeof w.requestIdleCallback === 'function') {
    const h = w.requestIdleCallback(fn, { timeout: 3_000 })
    return () => w.cancelIdleCallback?.(h)
  }
  const t = setTimeout(fn, delayMs)
  return () => clearTimeout(t)
}

let state: WarmState = 'cold'
let warm: PlaygroundClient | null = null

/** 当前暖机状态（测试与 UI 用；`take()` 之后回到 cold）。 */
export const warmState = (): WarmState => state

/**
 * 起一次预热。已经在暖或已暖好就什么都不做——**只有一条初始化路径**，
 * 「预热中又点了示例」不会变成两个 Worker。
 */
export function prewarm(): void {
  if (state !== 'cold' || !shouldPrewarm()) return
  const client = new PlaygroundClient()
  warm = client
  state = 'warming'
  client.start()
  client
    .init(PYODIDE_BASE_URL, engineZipUrl())
    .then(() => {
      // 期间被取走了：那个 client 归会话管，账本不许再动它
      if (warm !== client) return
      state = 'ready'
    })
    .catch(() => {
      // 预热失败对用户不可见：退回 cold，真开会话时按正常路径重来。
      // 这里必须自己收尾——半死的 Worker 留着只会泄漏一个子进程。
      if (warm !== client) return
      client.dispose()
      warm = null
      state = 'cold'
    })
}

/**
 * 取一个可用的 client：暖着的（含**正在暖**的——调用方 `await init()` 会
 * 接上同一个在途 Promise）优先，没有就现起一个。取完账本清空。
 */
export function takeWarmClient(): { client: PlaygroundClient; wasWarm: boolean } {
  const candidate = warm
  warm = null
  state = 'cold'
  if (candidate && !candidate.terminated) {
    return { client: candidate, wasWarm: candidate.ready }
  }
  candidate?.dispose()
  const client = new PlaygroundClient()
  client.start()
  return { client, wasWarm: false }
}

/**
 * 回到空状态时排一次预热。返回取消函数（组件卸载 / 又离开空状态时调用）。
 *
 * `afterCancel` 是**唯一**的行为差别：用户刚按过「取消」时，这次回到空状态
 * 不是「闲下来了」，是「别再下载了」。取消刚 dispose 掉一个在途 Worker，
 * 紧接着的空闲回调若照常预热，后台立刻又起一个继续下载 Pyodide——按钮上写着
 * 取消，机器上什么都没停。
 *
 * 这种情况下改成等一次**明确的意图**（指针按下 / 按键）再排预热。刻意不用
 * hover：鼠标扫过页面不是意图，那会让「取消」在几十毫秒后就失效。
 *
 * 这里只决定**什么时候排**；起不起、起几个仍然全由上面那本账本
 * （cold / warming / ready）与 `shouldPrewarm()` 说了算——不引入第二套账本。
 */
export function schedulePrewarm({ afterCancel = false } = {}): () => void {
  if (!afterCancel) return onIdle(() => prewarm())

  let cancelIdle: (() => void) | null = null
  const rearm = () => {
    detach()
    cancelIdle = onIdle(() => prewarm())
  }
  const detach = () => {
    globalThis.removeEventListener('pointerdown', rearm)
    globalThis.removeEventListener('keydown', rearm)
  }
  globalThis.addEventListener('pointerdown', rearm)
  globalThis.addEventListener('keydown', rearm)
  return () => {
    detach()
    cancelIdle?.()
  }
}

/** 丢掉暖着的那个（页面卸载）。幂等。 */
export function discardWarmClient(): void {
  warm?.dispose()
  warm = null
  state = 'cold'
}
