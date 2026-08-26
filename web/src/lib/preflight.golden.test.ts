import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/preflight_vectors.json'
import { runSpec, type PreflightSpec } from './preflight'
import { loadProfile, type JournalOverride } from './profile'

/**
 * 预检的**跨语言等价性**看护。
 *
 * 规则只有一份（`src/tavotto/profiles/publication.json`），但求值器有两个：
 * Python 的 `engine/preflight.py` 服务 Codex 的 MCP server，本文件这一侧服务
 * 画布与导出对话框。浏览器里跑不了 Python，所以第二份是必需的——代价是它随时
 * 可能与第一份分叉，而分叉的症状是「Codex 说这张图过了，Tavotto 说没过」。
 *
 * 所以两边跑**同一份向量**（tests/golden/preflight_vectors.json，由
 * `python scripts/gen_preflight_vectors.py --write` 按 Python 侧生成，
 * pytest 的 test_preflight.py 也断言同一份）。只比判据，不比中文措辞：
 * 措辞是界面的事，数字格式化在两种语言里不必逐字相同。
 */

interface GoldenIssue {
  id: string
  severity: string
  /** 可翻译描述符（issue #30）：MCP 画布按 locale 渲染靠它，两侧必须逐字一致 */
  message: { key: string; params: Record<string, unknown> }
  object_ids: string[]
  gids: string[]
  detail: Record<string, unknown>
}

interface GoldenCase {
  name: string
  profile_id: string
  journal?: Record<string, unknown>
  spec: unknown
  expected: GoldenIssue[]
}

const CASES = (vectors as { cases: GoldenCase[] }).cases

describe('preflight golden vectors（与 engine/preflight.py 逐条对齐）', () => {
  it('向量文件非空——空文件会让这份看护变成永远绿的摆设', () => {
    expect(CASES.length).toBeGreaterThan(10)
  })

  for (const c of CASES) {
    it(c.name, () => {
      const profile = loadProfile(c.profile_id, (c.journal ?? null) as JournalOverride | null)
      const got = runSpec(c.spec as PreflightSpec, profile).map((i) => ({
        id: i.id,
        severity: i.severity,
        // UiMessage 的 key 带 `preflight.` 前缀与 ns；向量里存的是裸 key + params
        message: {
          key: i.message.key.replace(/^preflight\./, ''),
          params: i.message.values ?? {},
        },
        object_ids: i.objectIds,
        gids: i.gids,
        detail: i.detail,
      }))
      expect(got).toEqual(c.expected)
    })
  }
})
