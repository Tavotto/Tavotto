/**
 * 标注公式的排版结果在前端的缓存（ADR 0152 PR 4）。
 *
 * 结果由后端排（`/api/mathtext` → `engine/mathtypeset.py`，matplotlib mathtext），**导出读的是同一份**，
 * 所以画布上的公式与 PDF 里的逐点相同。这里只做三件事：按 (族, 源码) 记住结果、把同一拍里要的
 * 合成一次请求、在结果到达时通知订阅者。键与内容一一对应，与项目无关——不需要项目代际。
 */
import { useSyncExternalStore } from 'react'
import { MATHTEXT_MAX_ITEMS, typesetMath, type MathBox } from './api'

export type { MathBox }

const results = new Map<string, MathBox>()
const pending = new Set<string>()
let queued: { src: string; family: string }[] = []
let flushScheduled = false
const listeners = new Set<() => void>()
let version = 0

const keyOf = (src: string, family: string) => `${family}\u0000${src}`

function notify() {
  version++
  for (const l of listeners) l()
}

async function flush() {
  flushScheduled = false
  const batch = queued
  queued = []
  for (let i = 0; i < batch.length; i += MATHTEXT_MAX_ITEMS) {
    const chunk = batch.slice(i, i + MATHTEXT_MAX_ITEMS)
    let got: MathBox[]
    try {
      got = (await typesetMath(chunk)).results
    } catch {
      // 请求失败（后端没起来等）不记结果：下次有人要时再试
      for (const it of chunk) pending.delete(keyOf(it.src, it.family))
      continue
    }
    chunk.forEach((it, k) => {
      const key = keyOf(it.src, it.family)
      pending.delete(key)
      results.set(key, got[k] ?? { ok: false, error: 'missing' })
    })
    notify()
  }
}

/** 取一个公式的结果；还没有就排进下一次请求，先回 undefined。 */
export function mathBoxOf(src: string, family: string): MathBox | undefined {
  const key = keyOf(src, family)
  const hit = results.get(key)
  if (hit || pending.has(key)) return hit
  pending.add(key)
  queued.push({ src, family })
  if (!flushScheduled) {
    flushScheduled = true
    queueMicrotask(() => void flush())
  }
  return undefined
}

export function subscribeMath(l: () => void): () => void {
  listeners.add(l)
  return () => listeners.delete(l)
}

/** React：公式结果到达时重渲染（`undefined` = 还在排） */
export function useMathBox(src: string, family: string): MathBox | undefined {
  useSyncExternalStore(subscribeMath, () => version)
  return mathBoxOf(src, family)
}

/**
 * 轮廓段 → SVG path 的 `d`（单位 em，**y 向下**：SVG 的 y = −公式的 y）。
 * 视口取 `0 −ascent width (ascent + descent)`，基线就在视口里 y = 0 处。
 */
export function mathPathData(box: MathBox): string {
  const out: string[] = []
  for (const seg of box.segments ?? []) {
    const [op, ...c] = seg as [string, ...number[]]
    const pts: string[] = []
    for (let k = 0; k + 1 < c.length; k += 2) pts.push(`${+c[k].toFixed(5)} ${+(-c[k + 1]).toFixed(5)}`)
    out.push(op + (pts.length ? ' ' + pts.join(' ') : ''))
  }
  return out.join(' ')
}

/** 测试用：清掉缓存与在途状态 */
export function __resetMathForTests() {
  results.clear()
  pending.clear()
  queued = []
  flushScheduled = false
  notify()
}
