/**
 * 属性测试（fast-check）的共用参数。
 *
 * 可复现是硬要求（仓库对 flaky 零容忍）：种子固定，同一份代码在 CI 与本地跑出同一串输入；
 * 失败时 fast-check 自带的报告会打印 seed 与 path，用 `FC_SEED=<seed>` 复现、
 * `FC_RUNS=<n>` 加大次数做一次性的深挖。新种子只用于深挖，不改默认值。
 */
import type { Parameters } from 'fast-check'

const SEED = Number(process.env.FC_SEED ?? 20261117)
const RUNS_SCALE = Number(process.env.FC_RUNS_SCALE ?? 1)

export function propParams<T>(numRuns: number, extra: Parameters<T> = {}): Parameters<T> {
  return { seed: SEED, numRuns: Math.max(1, Math.round(numRuns * RUNS_SCALE)), ...extra }
}

/** 浮点容差内相等（相对 + 绝对）。 */
export function near(a: number, b: number, eps = 1e-7): boolean {
  return Math.abs(a - b) <= eps * (1 + Math.max(Math.abs(a), Math.abs(b)))
}
