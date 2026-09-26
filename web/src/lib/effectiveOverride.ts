/**
 * 某个 (gid, prop) **生效的那条** override——读与写共用的唯一判据。
 *
 * 载入的旧文档里可能有重复的 (gid, prop)（历史写法 / 外部改过的 JSON）。引擎按
 * last-wins 取值（`engine/patchspec.py` 的去重：同一个 (gid, prop) 只留最后一条），
 * 所以前端的写入（`store/actions.upsertOverrides`、同值 no-op 判据）改的、比的都是
 * 最后那条；读取方（检查器控件、几何辅助、对齐基线……）也必须读最后那条——读第一条
 * 的话，改完之后引擎画的是新值、控件却一直显示过期的旧值，位置类编辑还会拿旧值算
 * 下一次拖动，画面会跳（#587 评审 P1）。
 *
 * 按 (gid, prop) 取 override 的值一律经这里，不要再写 `overrides.find(...)`：
 * `find` 回的是第一条。
 */

type OverrideKey = { readonly gid: string; readonly prop: string }

/** 生效那条的下标：重复时取最后一条，没有为 -1 */
export function effectiveOverrideIndex(
  overrides: readonly OverrideKey[],
  gid: string,
  prop: string,
): number {
  for (let i = overrides.length - 1; i >= 0; i--) {
    if (overrides[i].gid === gid && overrides[i].prop === prop) return i
  }
  return -1
}

/** 生效的那条 override（重复时是最后一条），没有为 undefined */
export function effectiveOverride<T extends OverrideKey>(
  overrides: readonly T[],
  gid: string,
  prop: string,
): T | undefined {
  const i = effectiveOverrideIndex(overrides, gid, prop)
  return i >= 0 ? overrides[i] : undefined
}

/** 遍历整张数组时用：下标 i 那条是不是它 (gid, prop) 生效的那条（被后面同键遮住的返回 false） */
export function isEffectiveOverrideAt(overrides: readonly OverrideKey[], i: number): boolean {
  const o = overrides[i]
  return effectiveOverrideIndex(overrides, o.gid, o.prop) === i
}
