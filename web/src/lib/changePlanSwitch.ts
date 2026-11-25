/**
 * 「改图前先给我看修改方案」开关（`ai.change_plan.enabled`，ADR 0206 PR 4）在前端的读法。
 *
 * 权威在后端，经 `GET /api/ai/capabilities` 的 `change_plan_enabled` 给；那条请求要探测本机的编码助手、可能很慢，
 * 助手面板没开过就没有。所以**还没读到时**按「上次读到的」办（本机记一份，读写都不假设存储可用；与 `tafFlagStore` 同一做法），
 * 从没读到过 = 默认开。这样关掉它的用户在一进来就点「一键排版」时也不会被偷偷出一张卡。
 */
const KEY = 'tavotto.change-plan-enabled'

export function readChangePlanCache(): boolean | null {
  try {
    const v = window.localStorage.getItem(KEY)
    return v === '1' ? true : v === '0' ? false : null
  } catch {
    return null
  }
}

export function writeChangePlanCache(on: boolean): void {
  try {
    window.localStorage.setItem(KEY, on ? '1' : '0')
  } catch {
    /* 存不下不影响这次 */
  }
}

/** `caps` 是助手面板探测到的能力（还没探测 = null / undefined）；返回此刻该不该先出方案卡 */
export function changePlanEnabled(caps: { change_plan_enabled?: boolean } | null | undefined): boolean {
  if (typeof caps?.change_plan_enabled === 'boolean') return caps.change_plan_enabled
  return readChangePlanCache() !== false
}
