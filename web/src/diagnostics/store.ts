/**
 * 诊断事件的**定长环形缓冲**（ADR 0016 §7）。
 *
 * 三条硬约束，缺一条这个模块就不该存在：
 *   * **有界**。固定 240 条，满了覆盖最旧的。没有任何一条路径能让它增长。
 *   * **写入即脱敏**。事件在进环之前就过了 `serializeEvent`——缓冲区里
 *     物理上不存在未脱敏的数据，不是「导出时再洗一遍」。这条是隐私设计的
 *     核心：就算进程被 dump、就算将来有人加了别的读取口，读到的也只有
 *     allowlist 允许的那些字段。
 *   * **绝不抛异常**。调用点在 commit / undo / 拖动收尾 / 渲染回调里。
 *     诊断出错导致用户丢一次编辑，是比没有诊断坏得多的结果。
 *
 * **纯内存**：不写磁盘、不发网络、不进 telemetry。只有用户点「导出诊断包」
 * 时它才会出现在一个 zip 里，然后由用户自己决定发不发。
 */
import { serializeEvent } from './sanitize'
import type { DiagnosticEvent, RecordedEvent } from './types'

/**
 * 环长。要求区间是 150–300，取中位 240：
 * 一条事件序列化后中位数约 200–300 字节（最大的是带 32 条 input_geometry 的
 * align.commit，约 2 KB），240 条的常态占用在 60–90 KB，最坏约 250 KB——
 * 正好落在 100–300 KB 的预算里。
 */
export const RING_CAPACITY = 240

const ring: RecordedEvent[] = []
let cursor = 0
let seq = 0
let sessionStart = Date.now()

/** `recordIfChanged` 的去重台账：键 → 上一次记录的规范化载荷 */
const lastByKey = new Map<string, string>()

function now(): number {
  return Date.now()
}

/**
 * 记一条诊断事件。**永不抛、永不 await、不碰 DOM、不碰网络。**
 *
 * 返回值只给测试与 `recordIfChanged` 用；业务调用点一律忽略它——诊断成没成功
 * 不该影响任何一条业务分支。
 */
export function recordDiagnosticEvent(ev: DiagnosticEvent): RecordedEvent | null {
  try {
    const body = serializeEvent(ev)
    if (!body) return null
    const ts = now()
    const rec: RecordedEvent = {
      seq: ++seq,
      ts,
      t_ms: ts - sessionStart,
      ...body,
    } as RecordedEvent
    if (ring.length < RING_CAPACITY) ring.push(rec)
    else {
      ring[cursor] = rec
      cursor = (cursor + 1) % RING_CAPACITY
    }
    return rec
  } catch {
    // 诊断系统自己的故障绝不允许冒进业务调用栈
    return null
  }
}

/**
 * 载荷没变就不记。给「状态采样」类事件用（display.source_changed 每轮
 * 渲染同步都会被算一次，但绝大多数轮次三个变体身份一个字都没动）。
 *
 * 去重键由调用方给（通常是面板 id），**不参与序列化**。
 */
export function recordIfChanged(dedupeKey: string, ev: DiagnosticEvent): RecordedEvent | null {
  try {
    const body = serializeEvent(ev)
    if (!body) return null
    const fingerprint = JSON.stringify(body)
    if (lastByKey.get(dedupeKey) === fingerprint) return null
    lastByKey.set(dedupeKey, fingerprint)
    return recordDiagnosticEvent(ev)
  } catch {
    return null
  }
}

/** 按时间序读出来（导出 / 测试断言）。返回的是拷贝，调用方改不动环 */
export function readDiagnosticTrace(): RecordedEvent[] {
  if (ring.length < RING_CAPACITY) return [...ring]
  return [...ring.slice(cursor), ...ring.slice(0, cursor)]
}

/** 环里现在有多少条 */
export function traceLength(): number {
  return ring.length
}

/** 本次会话已经走了多久（毫秒）——快照里报它 */
export function sessionElapsedMs(): number {
  return now() - sessionStart
}

/**
 * 清空。切项目 / 切文档时调用：上一个项目的交互轨迹留在环里，对排查当前
 * 项目没有帮助，而且它属于**另一份**用户内容的活动记录。
 *
 * seq **不重置**：它是「有没有事件被挤掉/清掉」的唯一线索，重置会让
 * 「seq 从 1 开始」这件事同时意味着「新会话」和「刚清过」，两者分不开。
 */
export function clearDiagnosticTrace(): void {
  ring.length = 0
  cursor = 0
  lastByKey.clear()
}

/** 只给测试：把 seq 与会话起点一起归零，好断言 seq 单调与 t_ms 相对值 */
export function __resetDiagnosticsForTests(): void {
  clearDiagnosticTrace()
  seq = 0
  sessionStart = Date.now()
}
