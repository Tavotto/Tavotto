/**
 * 外部审阅网页的点选是这份 `pickElementStack` 的原样拷贝（tavotto-infra serverless/review/web/src/vendor/，
 * 由 scripts/sync-hit.mjs 生成；ADR 0187 修订 2026-11-16）。两个仓库各存一份同样的夹具、各跑一遍：
 * 这边改了点选规则而那边没重新拷，那边的夹具会先红；反过来同理。
 *
 * 夹具的元素表是引擎 `reviewshare.manifest_elements` 对一张真实 matplotlib 图的输出（上传格式），
 * 所以这里补上画布 manifest 才有的 `editable` / `label` 等字段再喂进去。
 */
import { describe, expect, it } from 'vitest'

import type { Manifest, ManifestElement } from '@/lib/api'
import fx from './__fixtures__/review-pick-fixture.json'
import { pickElementStack } from './interactions'

const manifest = {
  ...fx.manifest,
  elements: fx.manifest.elements.map((e) => ({ editable: [], ...e }) as unknown as ManifestElement),
} as unknown as Manifest

describe('审阅页与画布点选对拍', () => {
  for (const p of fx.probes as { name: string; pt: [number, number]; expect: string | null }[]) {
    it(p.name, () => {
      expect(pickElementStack(manifest, p.pt[0], p.pt[1])[0]?.gid ?? null).toBe(p.expect)
    })
  }
})
