/**
 * 审阅转出来的脚本方案卡收尾（ADR 0206 修订 2026-11-26）：应用成功后转给脚本的那几条意见标「已处理」，
 * 撤销（按备份还原）时撤回，放弃 / 失败 / 没应用不标——与图内方案卡走同一条 `pendingSync` / `resolveReviewComment`。
 */
import { expect, it } from 'vitest'
import type { ChangePlan, ReviewShareDetail } from '@/lib/api'
import type { PlanEntry } from '@/store/changePlanStore'
import { pendingSync, resolvableIds } from './reviewSync'

const BASE = [{ gid: 'ax.title', prop: 'fontsize', value: 9 }]
const plan: ChangePlan = {
  id: 'p2',
  version: 1,
  kind: 'script',
  source: 'review',
  base: { target: 'f_p1', fingerprint: 'fp', script_sha256: 'aa' },
  base_patches: BASE,
  candidate_patches: BASE,
  groups: [],
  unhandled: [],
  preview: { before: '/b.png', after: '/a.png' },
}
const entry = (over: Partial<PlanEntry> = {}): PlanEntry =>
  ({
    key: 'k2',
    pj: null,
    panelId: 'p1',
    fileId: 'f_p1',
    source: { fileId: 'f_p1', script: 'fig.py' },
    status: 'ready',
    plan,
    unchecked: {},
    drafts: {},
    request: { mode: 'script', share_id: 's1', scriptFrom: { plan_id: 'p1', comment_ids: ['c2', 'c3'] } },
    comments: [],
    commentCount: 2,
    ...over,
  }) as unknown as PlanEntry
const detail = (resolved: string[] = []) =>
  ({ share_id: 's1', comments: ['c1', 'c2', 'c3'].map((id) => ({ id, resolved: resolved.includes(id) })) }) as unknown as ReviewShareDetail

it('转成脚本卡的意见：应用成功后标已处理（只标转的那几条、已处理的不重复标）', () => {
  const e = entry({ status: 'applied' })
  expect(resolvableIds(e)).toEqual(['c2', 'c3'])
  expect(pendingSync(e, BASE, detail(), null)).toEqual({ kind: 'mark', ids: ['c2', 'c3'] })
  expect(pendingSync(e, BASE, detail(['c2']), null)).toEqual({ kind: 'mark', ids: ['c3'] })
})

it('撤销回待确认：把这张卡标过的撤回；没标过的不动', () => {
  const undone = entry({ status: 'ready', resolvedIds: ['c2', 'c3'] })
  expect(pendingSync(undone, BASE, detail(['c2', 'c3']), null)).toEqual({ kind: 'restore', ids: ['c2', 'c3'] })
  expect(pendingSync(entry({ status: 'ready' }), BASE, detail(['c2']), null)).toBeNull()
})

it('没应用 / 放弃 / 失败：不标', () => {
  for (const status of ['ready', 'discarded', 'failed', 'generating'] as const) {
    expect(pendingSync(entry({ status }), BASE, detail(), null)).toBeNull()
  }
})

it('助手来源的脚本卡（没有 scriptFrom）不牵连审阅意见', () => {
  const e = entry({ status: 'applied', request: { mode: 'script', instruction: '改标题' } })
  expect(resolvableIds(e)).toEqual([])
  expect(pendingSync(e, BASE, detail(), null)).toBeNull()
})
