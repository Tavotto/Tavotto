import { describe, expect, it } from 'vitest'
import vectors from '../../../tests/golden/filename_vectors.json'
import {
  checkFilename,
  dedupeCheck,
  FILENAME_MAX,
  outputName,
  stripOutputExtension,
} from './exportName'

/**
 * 文件名规则的跨语言看护。
 *
 * 规则有两个实现（`engine/exportreq.py` 给真正落盘的那一侧，`exportName.ts`
 * 给输入时的就地提示）。两边跑**同一份向量**（`tests/golden/filename_vectors.json`，
 * 由 `scripts/gen_filename_vectors.py` 从 Python 侧生成），pytest 与 vitest
 * 各断言一次——只有一边被断言的话，分叉正是从"只改了一边"开始的。
 *
 * 这里踩过一个具体的坑：`str.strip()` 与 `String.trim()` 认的空白字符集**不
 * 一样**（U+FEFF 只有 JS 认，`\x1c`–`\x1f` 只有 Python 认）。靠各自的内建
 * 函数，两侧对同一个名字会给出不同答案。向量里那几条就是为它留的。
 */
describe('文件名规则与 Python 侧逐条一致', () => {
  it('上限一致', () => {
    expect(FILENAME_MAX).toBe(vectors.filename_max)
  })

  it.each(vectors.check.map((c) => [JSON.stringify(c.name), c] as const))(
    'checkFilename(%s)',
    (_label, c) => {
      expect(checkFilename(c.name)).toBe(c.reason)
    },
  )

  it.each(vectors.strip.map((c) => [JSON.stringify(c.name), c] as const))(
    'stripOutputExtension(%s)',
    (_label, c) => {
      expect(stripOutputExtension(c.name, c.formats)).toBe(c.stripped)
    },
  )

  it.each(vectors.output_name.map((c) => [`${c.base}.${c.format}`, c] as const))(
    'outputName(%s)',
    (_label, c) => {
      expect(outputName(c.base, c.format)).toBe(c.name)
    },
  )

  it.each(vectors.dedupe.map((c) => [`${c.base}.${c.format}`, c] as const))(
    'dedupeCheck(%s)',
    (_label, c) => {
      expect(dedupeCheck(c.base, c.format, (n) => c.taken.includes(n))).toBe(c.name)
    },
  )

  it('向量覆盖了每一条判据（少一条 = 那条规则两侧可以分叉而没人发现）', () => {
    const seen = new Set(vectors.check.map((c) => c.reason).filter(Boolean))
    expect(seen).toEqual(
      new Set([
        'empty',
        'whitespace_edge',
        'too_long',
        'control_char',
        'illegal_char',
        'trailing_dot',
        'dot_only',
        'reserved_name',
      ]),
    )
  })
})
