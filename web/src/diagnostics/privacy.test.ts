/**
 * 从 `lib/authorityTrace.test.ts` **迁移**过来的隐私边界用例（ADR 0016 收编
 * ADR 0017 的追踪环）。
 *
 * 迁移的理由比「别让用例掉队」更硬：`shortHash` 现在换成了 `diagnosticHash`，
 * 而它的产物之一 **`data-display-key` 是落在 DOM 上的属性**——那是一个对外
 * 暴露面（e2e 读它、用户的浏览器里也看得到）。这几条是「它不泄漏文件名与
 * override 原文」的唯一看护。
 *
 * 原用例里「环有界」「补丁不带值」「不认识的形状丢掉」几条已经由
 * `store.test.ts` 覆盖（判据更严：那边连字段名都要在册），这里只保留**内容
 * 形状**这一类，并补上原来没有的真实中文 / Windows 路径样本。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { __setDiagnosticSaltForTests, diagnosticHash, variantHash } from './hash'
import { __resetDiagnosticsForTests, readDiagnosticTrace, recordDiagnosticEvent } from './store'
import { serializeEvent } from './sanitize'
import type { DiagnosticEvent } from './types'

/** 一个**真实形状**的变体键：Windows 路径 + 中文目录 + override 里的用户正文 */
const HOSTILE_KEY =
  'D:\\论文\\figures\\接触角验证.pdf [{"gid":"t1","prop":"text","value":"未发表结论"}]'

beforeEach(() => {
  __resetDiagnosticsForTests()
  __setDiagnosticSaltForTests('test-salt')
})

describe('变体键：反推不回文件名与 overrides', () => {
  it('hash 之后中文目录、文件名、override 正文一个字都不剩', () => {
    const h = variantHash(HOSTILE_KEY)
    expect(h).toMatch(/^var:[0-9a-f]{12}$/)
    expect(h).not.toContain('未发表结论')
    expect(h).not.toContain('论文')
    expect(h).not.toContain('接触角')
    expect(h).not.toContain('.pdf')
  })

  it('落进事件后整条 JSON 里也搜不到', () => {
    recordDiagnosticEvent({
      type: 'render.request',
      file: diagnosticHash(HOSTILE_KEY).replace(/^/, 'file:'),
      variant: variantHash(HOSTILE_KEY),
      policy: 'immediate',
      preview_dpi: null,
    })
    const dumped = JSON.stringify(readDiagnosticTrace())
    for (const secret of ['未发表结论', '论文', '接触角', '.pdf', 'D:\\']) {
      expect(dumped).not.toContain(secret)
    }
  })

  it('`data-display-key` 用的就是同一个 hash：DOM 上那个属性同样反推不回去', () => {
    // PanelView 把它写进 DOM（e2e 读它、用户浏览器里也看得到），所以它是
    // 对外暴露面，不是内部字段
    const attr = diagnosticHash(HOSTILE_KEY)
    expect(attr).toMatch(/^[0-9a-f]{12}$/)
    expect(attr).not.toContain('接触角')
    // null（还没挂上任何一版）要有稳定占位，不能变成 "undefined" 或空串
    expect(diagnosticHash(null)).toMatch(/^[0-9a-f]{12}$/)
    expect(diagnosticHash(null)).toBe(diagnosticHash(null))
  })
})

describe('短技术枚举照常可读', () => {
  it('mode / reason 原样留下——它们是判据，读包的人要看得懂', () => {
    const out = serializeEvent({
      type: 'align.blocked',
      mode: 'left',
      panel: 'panel:aaaaaaaaaaaa',
      reason: 'authority_stale',
      document_variant: 'var:111111111111',
      display_variant: null,
      authority_variant: null,
    })!
    expect(out.mode).toBe('left')
    expect(out.reason).toBe('authority_stale')
  })
})

describe('不认识的形状不做深序列化', () => {
  it('对象 / 函数塞进未登记字段：整条都不会带出内容', () => {
    recordDiagnosticEvent({
      type: 'align.commit',
      mode: 'left',
      panel: 'panel:aaaaaaaaaaaa',
      selected_count: 1,
      document_variant: 'var:111111111111',
      display_variant: null,
      authority_variant: 'var:111111111111',
      exact_authority: true,
      patch_count: 1,
      move_count: 0,
      // 真实调用点不会这么传，但 record 是公共入口，兜底必须在
      blob: { secret: '用户正文' },
      fn: () => '用户正文',
    } as unknown as DiagnosticEvent)
    expect(JSON.stringify(readDiagnosticTrace())).not.toContain('用户正文')
  })
})
