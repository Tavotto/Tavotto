/**
 * 环形缓冲、hash、allowlist 序列化（ADR 0016 §4 / §5 / §7）。
 *
 * 这一档用例守的是**隐私边界本身**，不是行为便利。每加一条事件类型都该回来
 * 跑一次：allowlist 的失效方式是静默的，没有用例它不会自己红。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import {
  RING_CAPACITY,
  __resetDiagnosticsForTests,
  readDiagnosticTrace,
  recordDiagnosticEvent,
  recordIfChanged,
} from './store'
import { __setDiagnosticSaltForTests, diagnosticHash, docHash, fileHash, variantHash } from './hash'
import { serializeEvent } from './sanitize'
import type { DiagnosticEvent } from './types'

/** 一条最简单的合法事件，seq 用 patch_count 携带好断言顺序 */
const commit = (n: number): DiagnosticEvent => ({
  type: 'document.commit',
  label_key: 'setProp',
  patch_count: n,
  past_count: n,
  future_count: 0,
  txn_open: false,
  document_hash_before: docHash(n),
  document_hash_after: docHash(n + 1),
})

beforeEach(() => {
  __resetDiagnosticsForTests()
  __setDiagnosticSaltForTests('test-salt')
})

describe('环形缓冲', () => {
  it('超出容量后只留最近的那些，顺序与 seq 都不乱', () => {
    const total = RING_CAPACITY + 60
    for (let i = 0; i < total; i++) recordDiagnosticEvent(commit(i))

    const trace = readDiagnosticTrace()
    expect(trace).toHaveLength(RING_CAPACITY)
    // 留下的是**最后** RING_CAPACITY 条
    expect(trace[0].patch_count).toBe(total - RING_CAPACITY)
    expect(trace.at(-1)!.patch_count).toBe(total - 1)
    // 时间序：读出来必须按发生顺序，不是按环里的物理下标
    for (let i = 1; i < trace.length; i++) {
      expect(trace[i].seq).toBeGreaterThan(trace[i - 1].seq)
      expect(trace[i].patch_count).toBe((trace[i - 1].patch_count as number) + 1)
    }
  })

  it('seq 不重排：被挤掉的事件在编号上留下缺口，读包的人看得出来', () => {
    for (let i = 0; i < RING_CAPACITY + 5; i++) recordDiagnosticEvent(commit(i))
    const trace = readDiagnosticTrace()
    // 第一条的 seq 是 6（前 5 条被挤掉了），不是 1
    expect(trace[0].seq).toBe(6)
  })

  it('内存不无限增长：写一万条之后环长仍然是容量', () => {
    for (let i = 0; i < 10_000; i++) recordDiagnosticEvent(commit(i))
    expect(readDiagnosticTrace()).toHaveLength(RING_CAPACITY)
  })

  it('recordIfChanged：载荷没变就不记，变了才记', () => {
    const ev = (exact: boolean): DiagnosticEvent => ({
      type: 'display.source_changed',
      panel: 'panel:aaaaaaaaaaaa',
      file: 'file:bbbbbbbbbbbb',
      document_variant: 'var:cccccccccccc',
      display_variant: 'var:dddddddddddd',
      authority_variant: null,
      exact,
      render_status: 'rendering',
      stale: false,
    })
    recordIfChanged('p1', ev(false))
    recordIfChanged('p1', ev(false))
    recordIfChanged('p1', ev(false))
    expect(readDiagnosticTrace()).toHaveLength(1)
    recordIfChanged('p1', ev(true))
    expect(readDiagnosticTrace()).toHaveLength(2)
  })

  it('诊断自己出错绝不冒进业务调用栈', () => {
    const hostile = {
      type: 'document.commit',
      get label_key(): string {
        throw new Error('boom')
      },
    } as unknown as DiagnosticEvent
    expect(() => recordDiagnosticEvent(hostile)).not.toThrow()
  })
})

describe('hash', () => {
  it('相同输入 → 相同 hash；不同输入 → 不同 hash', () => {
    expect(diagnosticHash('abc')).toBe(diagnosticHash('abc'))
    expect(diagnosticHash('abc')).not.toBe(diagnosticHash('abd'))
    expect(variantHash('f.py [] ')).not.toBe(variantHash('f.py [{"gid":"a"}]'))
  })

  it('输出里不含原始输入，形状固定为 前缀:12位十六进制', () => {
    const secret = '/Users/private-user-name/paper/SUPER_SECRET_PAPER_TITLE_12345.py'
    const h = fileHash(secret)
    expect(h).toMatch(/^file:[0-9a-f]{12}$/)
    expect(h).not.toContain('private-user-name')
    expect(h).not.toContain('SUPER_SECRET')
    expect(h).not.toContain('paper')
  })

  it('salt 让同一个值在不同会话里 hash 不同（跨包画像不成立）', () => {
    const value = '/Users/alice/fig.py'
    __setDiagnosticSaltForTests('session-a')
    const a = fileHash(value)
    __setDiagnosticSaltForTests('session-b')
    const b = fileHash(value)
    expect(a).not.toBe(b)
  })
})

describe('allowlist 序列化', () => {
  it('未知字段根本不会被读到，更不会进 JSON', () => {
    const out = serializeEvent({
      type: 'align.commit',
      mode: 'left',
      panel: 'panel:aaaaaaaaaaaa',
      selected_count: 3,
      document_variant: 'var:111111111111',
      display_variant: 'var:222222222222',
      authority_variant: 'var:111111111111',
      exact_authority: true,
      patch_count: 2,
      move_count: 0,
      // 下面这些 schema 里没有——TS 会先报错，这里用 as 绕过去验运行期那道
      secret_unexpected_field: 'SUPER_SECRET_API_KEY_67890',
      text: 'Experimental results for Fig. 3',
    } as unknown as DiagnosticEvent)!

    expect(out.mode).toBe('left')
    expect(out).not.toHaveProperty('secret_unexpected_field')
    expect(out).not.toHaveProperty('text')
    expect(JSON.stringify(out)).not.toContain('SUPER_SECRET')
    expect(JSON.stringify(out)).not.toContain('Experimental')
  })

  it('未知事件类型整条丢弃', () => {
    expect(serializeEvent({ type: 'totally.made.up' } as unknown as DiagnosticEvent)).toBeNull()
  })

  it('嵌套结构里的用户内容一样出不去：patch 只留 gid/prop，没有 value', () => {
    const out = serializeEvent({
      type: 'document.commit',
      label_key: 'editText',
      patch_count: 1,
      past_count: 3,
      future_count: 0,
      txn_open: false,
      document_hash_before: 'doc:aaaaaaaaaaaa',
      document_hash_after: 'doc:bbbbbbbbbbbb',
      patches: [
        // value 不在 PatchRef 类型里；就算硬塞进来也读不到
        { gid: 'axes_0.title', prop: 'text', value: 'SUPER_SECRET_PAPER_TITLE_12345' },
      ],
    } as unknown as DiagnosticEvent)!

    expect(out.patches).toEqual([{ prop: 'text', gid: 'axes_0.title' }])
    expect(JSON.stringify(out)).not.toContain('SUPER_SECRET')
  })

  it('形状不对的 gid 换成 hash，绝不原样带过去', () => {
    const out = serializeEvent({
      type: 'element.drag.cancel',
      panel: 'panel:aaaaaaaaaaaa',
      // 一个「gid 里混进了用户文字」的假想未来
      gid: 'Experimental results for Fig. 3',
      cancelled: true,
    } as unknown as DiagnosticEvent)!
    expect(out.gid).toMatch(/^gid:[0-9a-f]{12}$/)
    expect(String(out.gid)).not.toContain('Experimental')
  })

  it('该是 hash 的字段拿到原值时**丢掉字段**，不顺手替它 hash', () => {
    // 顺手 hash 会让「调用点忘了 hash」永远不被发现，下一个字段就没这么幸运
    const out = serializeEvent({
      type: 'render.request',
      file: '/Users/alice/fig.py',
      variant: 'var:111111111111',
      policy: 'immediate',
      preview_dpi: null,
    } as unknown as DiagnosticEvent)!
    expect(out).not.toHaveProperty('file')
    expect(out.variant).toBe('var:111111111111')
  })

  it('几何只留数字与技术 gid', () => {
    const out = serializeEvent({
      type: 'align.request',
      mode: 'left',
      panel: 'panel:aaaaaaaaaaaa',
      selected_count: 2,
      document_variant: 'var:111111111111',
      display_variant: null,
      authority_variant: null,
      exact_authority: false,
      input_geometry: [
        { gid: 'axes_0.title', bbox: [0.31, 0.12, 0.18, 0.04], anchor: [0.4, 0.15] },
        { gid: 'axes_0.xaxis.label', bbox: [0.2, 0.9, 0.3, 0.05], label: '实验结果' },
      ],
    } as unknown as DiagnosticEvent)!

    const geom = out.input_geometry as Record<string, unknown>[]
    expect(geom).toHaveLength(2)
    expect(geom[0]).toEqual({
      gid: 'axes_0.title',
      bbox: [0.31, 0.12, 0.18, 0.04],
      anchor: [0.4, 0.15],
    })
    expect(geom[1]).not.toHaveProperty('label')
    expect(JSON.stringify(out)).not.toContain('实验结果')
  })

  it('枚举外的值、错类型的值一律丢掉', () => {
    const out = serializeEvent({
      type: 'align.blocked',
      mode: 'left',
      panel: 'panel:aaaaaaaaaaaa',
      reason: 'because the user is bad',
      document_variant: 'var:111111111111',
      display_variant: null,
      authority_variant: null,
    } as unknown as DiagnosticEvent)!
    expect(out).not.toHaveProperty('reason')
  })

  it('bool 混进 int 字段不会悄悄通过', () => {
    const out = serializeEvent({
      type: 'render.stale',
      file: 'file:aaaaaaaaaaaa',
      variant_count: true,
    } as unknown as DiagnosticEvent)!
    expect(out).not.toHaveProperty('variant_count')
  })
})
