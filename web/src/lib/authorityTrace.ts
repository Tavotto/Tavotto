/**
 * 几何权威与事务边界的**脱敏**追踪环（issue #131）。
 *
 * 诊断包里现在只有后端日志：`GET /api/render`、`引擎渲染: … 700ms`。issue #131
 * 那份 app.log 一行错误都没有——事故整个发生在前端的「哪个变体说了算」和
 * 「这次 commit 并进了谁的事务」上，后端看不见。这里补的就是那一层。
 *
 * 硬约束：
 *   - 有界（RING 条，环形覆盖），永远不增长；
 *   - **只记结构与身份，不记内容**。gid / prop / mode / 数量 / 布尔照记，
 *     用户图内文字、脚本、文件绝对路径、项目名、SVG 全文、override 的**值**
 *     一律不进来。变体键这种可能含文件名的东西先过不可逆短 hash。
 *   - 生产态也在记（环很小，代价是几 KB），导出与否由调用方决定。
 */

/** 环长：一次事故前后的关键事件绰绰有余，内存是常数 */
const RING = 100

export type TraceEvent =
  | 'gesture.begin'
  | 'gesture.finish'
  | 'align.request'
  | 'align.blocked'
  | 'align.commit'
  | 'authority.ready'
  | 'authority.unavailable'
  | 'display.source.changed'
  | 'version.restore'
  | 'invariant.violated'

export interface TraceRecord {
  /** 相对于本次会话开始的毫秒数——不落墙钟，免得把用户作息也带出去 */
  t: number
  ev: TraceEvent
  data: Record<string, string | number | boolean>
}

const t0 = Date.now()
const ring: TraceRecord[] = []
let cursor = 0

/**
 * 不可逆短 hash（FNV-1a 取 8 位十六进制）。
 *
 * 变体键 = `文件名 + JSON.stringify(overrides)`，两头都可能带用户内容，
 * 原样记就是泄漏。诊断真正要回答的问题只有「这两个键是不是同一个」，
 * 短 hash 足够，而且反推不回去。
 */
export function shortHash(input: string | null | undefined): string {
  if (input == null) return '-'
  let h = 0x811c9dc5
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}

/** 只让白名单形状的标量进来；其余一律先转成计数或 hash */
function sanitize(data: Record<string, unknown>): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {}
  for (const [k, v] of Object.entries(data)) {
    if (v == null) continue
    if (typeof v === 'boolean' || typeof v === 'number') {
      out[k] = v
      continue
    }
    if (Array.isArray(v)) {
      // 数组只留条数与逐条的技术标识（gid:prop 这类），不留值
      out[`${k}_n`] = v.length
      out[k] = v.map((x) => (typeof x === 'string' ? x : typeof x)).slice(0, 12).join(',')
      continue
    }
    if (typeof v === 'string') {
      // 变体键 / overrides 串一律 hash；短的技术枚举（mode、reason）原样留
      out[k] = k.endsWith('Key') || k.endsWith('Patches') || v.length > 48 ? shortHash(v) : v
      continue
    }
  }
  return out
}

/** 记一条。调用点遍布几何写路径，必须廉价且绝不抛。 */
export function traceGeometry(ev: TraceEvent, data: Record<string, unknown> = {}): void {
  const rec: TraceRecord = { t: Date.now() - t0, ev, data: sanitize(data) }
  if (ring.length < RING) ring.push(rec)
  else ring[cursor] = rec
  cursor = (cursor + 1) % RING
}

/** 按时间序读出来（诊断导出 / 测试断言用） */
export function readTrace(): TraceRecord[] {
  if (ring.length < RING) return [...ring]
  return [...ring.slice(cursor), ...ring.slice(0, cursor)]
}

export function clearTrace(): void {
  ring.length = 0
  cursor = 0
}

/**
 * 几何写操作的前置不变式：**动手那一刻的权威键必须就是当前面板的变体键**。
 *
 * 违反 = 有人绕过了 `exactPanelRender` 直接拿显示 manifest 写文档。开发态当场
 * 喊出来（这类 bug 上一次是靠用户报 issue 才发现的），生产态只留一条 trace
 * 并让调用方走「拒绝」分支——绝不带着错误几何继续写。
 */
export function assertGeometryAuthority(
  currentKey: string,
  authorityKey: string | null,
  where: string,
): boolean {
  if (authorityKey != null && authorityKey === currentKey) return true
  traceGeometry('invariant.violated', { where, currentKey, authorityKey })
  if (import.meta.env?.DEV) {
    console.error(
      `[几何不变式] ${where}：权威键与当前变体键不一致，本次写操作已拒绝`,
      { currentKey: shortHash(currentKey), authorityKey: shortHash(authorityKey) },
    )
  }
  return false
}
