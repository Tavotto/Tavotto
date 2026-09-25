/**
 * 重建进度 id 的格式是前后端的同源对（#606）：前端在发请求之前生成，后端按 `REBUILD_PROGRESS_ID_RE` 校验、
 * 格式不对就拒收。两侧各读 `tests/golden/rebuild_progress_id.json`，不读对方源码
 * （后端那一侧是 `tests/test_env_project_attribution.py::test_the_rebuild_id_format_is_the_golden_pair`）。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/rebuild_progress_id.json'
import { newRebuildProgressId } from './depRepairStore'

describe('重建进度 id（同源对）', () => {
  it('生成的 id 都合后端的格式，且每次不同', () => {
    const re = new RegExp(golden.pattern)
    // 尺子是活的：向量里该收的收、该拒的拒
    for (const raw of golden.accept) expect(re.test(raw)).toBe(true)
    for (const raw of golden.reject) expect(re.test(raw)).toBe(false)
    const ids = Array.from({ length: 64 }, () => newRebuildProgressId())
    for (const id of ids) expect(id).toMatch(re)
    // 旧固定 id 只留给没给 id 的老前端（后端兜底），新前端生成不出它
    expect(re.test(golden.legacy)).toBe(false)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
