/**
 * 原生菜单的动作 id 与壳严格同源：两侧各自与 `tests/golden/menu_actions.json` 比，
 * 不读对方源码（壳那一侧是 `src-tauri/src/main.rs` 的
 * `menu_ids_match_the_golden_pair_on_both_platforms`，量的是 `menu_spec` 真正挂上去的项）。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/menu_actions.json'
import { MENU_ACTIONS } from './desktop'

describe('MENU_ACTIONS ↔ 壳的菜单项', () => {
  it('转发给前端的 id 与 golden 同一个集合、没有重复', () => {
    expect(new Set(MENU_ACTIONS).size).toBe(MENU_ACTIONS.length)
    expect([...MENU_ACTIONS].sort()).toEqual([...golden.forwarded].sort())
  })

  it('壳自己开链接的 help-* 不进前端', () => {
    for (const id of golden.shell) expect(MENU_ACTIONS as readonly string[]).not.toContain(id)
  })
})
