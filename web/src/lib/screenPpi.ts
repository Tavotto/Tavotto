import { useSyncExternalStore } from 'react'
import { getScreenPpi, onScreenPpiChange } from './desktop'

/**
 * 主窗口所在屏幕的物理 ppi（ADR 0168 第二节），模块级一份：第一个订阅者到来时向壳回读一次并开始听换屏事件，
 * 之后所有进度条共用。浏览器 / 远程窗口 / 读不到时一直是 null。
 */
let ppi: number | null = null
let started = false
const subs = new Set<() => void>()

function set(next: number | null) {
  if (next === ppi) return
  ppi = next
  subs.forEach((f) => f())
}

function start() {
  if (started) return
  started = true
  // 先听再读：事件与回读谁先到都不丢；回读只在还没有事件值时采用
  let heard = false
  void onScreenPpiChange((v) => {
    heard = true
    set(v)
  })
  void getScreenPpi().then((v) => {
    if (!heard) set(v)
  })
}

function subscribe(f: () => void) {
  start()
  subs.add(f)
  return () => {
    subs.delete(f)
  }
}

export function useScreenPpi(): number | null {
  return useSyncExternalStore(
    subscribe,
    () => ppi,
    () => null,
  )
}

/** 目标点径（毫米）：Mac Retina 上「2 个物理像素」的实际大小（14 寸 MacBook Pro 254 ppi ≈ 0.2 mm） */
export const TARGET_DOT_MM = 0.2

/** 按屏幕 ppi 选点径（物理像素）：round(0.2 mm × ppi ÷ 25.4)，夹在 1–4；读不到返回 undefined（按缩放倍数取整） */
export function dotForPpi(p: number | null): number | undefined {
  if (p === null || !Number.isFinite(p) || p <= 0) return undefined
  return Math.min(4, Math.max(1, Math.round((TARGET_DOT_MM * p) / 25.4)))
}

export function __resetScreenPpiForTests() {
  ppi = null
  started = false
  subs.clear()
}
