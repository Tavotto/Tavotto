/**
 * 红色状态提示门禁：每个 `setStatus(…, 'error')` 要么带 `action` 按钮，要么在台账里写明为什么不需要。
 *
 * 规则（docs/rules/frontend/ui-visual-discipline.md「失败出口三种结局」）：失败出口只能是
 * ① 静默做完并说一句；② 一句话 + 一个能直接完成用户原动作的主按钮；③ 真做不了 →「发给我们看看」。
 * 红色常驻提示不带按钮 = 用户只读到一句报错、不知道怎么继续——这类调用点必须在
 * `statusErrorLedger.json` 登记理由，新增的未登记即红。
 *
 * 判据（AST，不用子串）：被调用表达式的属性名 / 标识符名是 `setStatus`；
 *  - 第二实参是字符串字面量 `'error'`，或三元里任一分支是 `'error'` → error 调用点；
 *  - 第二实参是别的表达式（变量、调用等）→ 判不出，只列进「判不出」清单，不让测试红（判不出就别判）；
 *  - 第三实参是对象字面量：含 `action` 属性（含简写 `{ action }`）= 带按钮；含展开 `...x` 且无 `action` = 判不出；
 *    第三实参是标识符 / 其它表达式 = 判不出；缺省 = 不带。
 * 包装助手：扫描器先找「函数里有一处 `setStatus(m, <自己的形参>, …)`」的包装（目前认出 `sayDrop`（别名 `say`）、
 * `sayNotebook`、`store/actions.ts` 的 `status`），再把对它们的调用按同样判据当调用点：tone 实参是 'error'，
 * 且包装有 action 形参时看那一位实参给没给（`feedbackAction()` 这类运行期可能返回 undefined 的也算「带」）。
 * 新增的包装只要把 tone 当形参透传给 `setStatus` 就会被自动认出，不用改这里。
 * 第三实参是标识符时，会去同文件找 `const x = {…}` 的对象字面量来判；找不到才算判不出。
 *
 * 台账键 = 文件路径 + 文案 key（msg 实参里的 i18n key 字面量；对象字面量取 `key` 属性；判不出写 `?`），
 * 同文件同 key 多处用 `count`，不用行号。
 */
import { describe, expect, it } from 'vitest'
import { scan } from './test/statusErrorScan'
import ledgerJson from './statusErrorLedger.json'

/** `TODO(dead-end)` 条目数上限：只许降不许升（修掉一条就把这个数改小）。 */
const DEAD_END_CAP = 18

interface LedgerEntry { file: string; key: string; count: number; reason: string }
const ledger = ledgerJson.entries as LedgerEntry[]
const idOf = (e: { file: string; key: string }) => `${e.file} :: ${e.key}`

describe('红色状态提示门禁：error 调用点要么带 action，要么在台账登记理由', () => {
  const { errors, undeterminedTone } = scan()
  // 判不出是否带按钮的（opts 是变量 / 展开）不要求登记，列出来供人看
  const needLedger = errors.filter((s) => s.hasAction === false)

  it('实际「不带按钮的 error 调用点」集合 == 台账', () => {
    const actual = new Map<string, number>()
    for (const s of needLedger) actual.set(idOf(s), (actual.get(idOf(s)) ?? 0) + 1)
    const expected = new Map<string, number>()
    for (const e of ledger) expected.set(idOf(e), (expected.get(idOf(e)) ?? 0) + e.count)
    const fix = '要么给这条提示加一个能完成用户原动作的 action 按钮（setStatus 第三实参 { action }），要么去 statusErrorLedger.json 登记不需要按钮的理由。'
    const missing = [...actual].filter(([k, n]) => (expected.get(k) ?? 0) < n).map(([k, n]) => `${k}（实际 ${n}，台账 ${expected.get(k) ?? 0}）`)
    const stale = [...expected].filter(([k, n]) => (actual.get(k) ?? 0) < n).map(([k, n]) => `${k}（台账 ${n}，实际 ${actual.get(k) ?? 0}）`)
    expect(
      { 新增未登记: missing, 台账陈旧: stale },
      `${fix}\n台账陈旧 = 提示已加按钮或已删除，请删掉对应台账条目。`,
    ).toEqual({ 新增未登记: [], 台账陈旧: [] })
  })

  it('台账条目：理由必填、不许占位、键不重复、count 为正整数', () => {
    const seen = new Set<string>()
    for (const e of ledger) {
      expect(seen.has(idOf(e)), `重复条目 ${idOf(e)}`).toBe(false)
      seen.add(idOf(e))
      expect(Number.isInteger(e.count) && e.count > 0, `${idOf(e)} 的 count 必须是正整数`).toBe(true)
      const r = e.reason.replace(/^TODO\(dead-end\):\s*/, '').trim()
      expect(r.length >= 8, `${idOf(e)} 的 reason 太短 / 为空`).toBe(true)
      expect(/^(待定|无|n\/a|todo|tbd|xxx|同上|\.+)$/i.test(r), `${idOf(e)} 的 reason 是占位`).toBe(false)
    }
  })

  it('TODO(dead-end) 条目数不超过上限（只许降不许升）', () => {
    const n = ledger.filter((e) => e.reason.startsWith('TODO(dead-end): ')).length
    expect(n, '死胡同台账条目变多了：新增的失败出口要么带按钮，要么写真理由').toBeLessThanOrEqual(DEAD_END_CAP)
  })

  it('扫描器自检：确有 error 调用点被认出（防扫描器静默失效）', () => {
    expect(errors.length).toBeGreaterThan(5)
    expect(errors.some((s) => s.hasAction === true)).toBe(true)
  })

  it('判不出的清单（只输出，不判红）', () => {
    // 判不出的：tone 是变量 / 调用，或 opts 是变量 / 展开。人工评审时看这一张。
    const lines = [
      ...undeterminedTone.map((s) => `tone 判不出 ${s.file}:${s.line} key=${s.key}`),
      ...errors.filter((s) => s.hasAction === 'unknown').map((s) => `action 判不出 ${s.file}:${s.line} key=${s.key}`),
    ]
    if (process.env.STATUS_ERROR_VERBOSE) console.log(lines.join('\n'))
    expect(Array.isArray(lines)).toBe(true)
  })
})
