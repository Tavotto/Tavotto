import { describe, expect, it } from 'vitest'
import type { PdfPagesCapture, RuntimeAssetInfo } from '@/lib/api'
import { mergePdfPlan, pdfPagesBaseName } from './pdfPagesMerge'

const asset = (id: string, stem: string, script = 's.py', run_config?: string): RuntimeAssetInfo =>
  ({ id, script, stem, entry: 'main', status: 'ready', ...(run_config ? { run_config } : {}) }) as unknown as RuntimeAssetInfo
const entry = (over: Partial<PdfPagesCapture> = {}): PdfPagesCapture => ({
  name: 'all_samples.pdf',
  pages: 3,
  captured: 3,
  stems: ['a', 'b', 'c'],
  path: 'out/all_samples.pdf',
  scope: 'project',
  page_stems: ['a', 'b', 'c'],
  complete: true,
  ...over,
})
const A = [asset('r:a', 'a'), asset('r:b', 'b'), asset('r:c', 'c')]
const of = (...e: PdfPagesCapture[]) => () => ({ pages: e, runConfig: '' })

describe('mergePdfPlan', () => {
  it('页序取脚本的 page_stems，与所选图的顺序无关；名字取 PdfPages 的文件名', () => {
    expect(mergePdfPlan(['r:c', 'r:a', 'r:b'], A, of(entry()))).toEqual({
      name: 'all_samples',
      figureIds: ['r:a', 'r:b', 'r:c'],
    })
  })
  it('同一张图写多页：按页序重复', () => {
    expect(mergePdfPlan(['r:a', 'r:b'], A, of(entry({ page_stems: ['a', 'b', 'a'] })))?.figureIds).toEqual([
      'r:a',
      'r:b',
      'r:a',
    ])
  })
  it.each([
    ['只选一张', () => mergePdfPlan(['r:a'], A, of(entry()))],
    ['素材清单还没取到', () => mergePdfPlan(['r:a', 'r:b'], null, of(entry()))],
    ['有图不是 runtime 素材', () => mergePdfPlan(['r:a', 'file.pdf'], A, of(entry()))],
    ['不在同一个脚本', () => mergePdfPlan(['r:a', 'r:x'], [...A, asset('r:x', 'a2', 'o.py')], of(entry()))],
    ['缺页（complete 不为 true）', () => mergePdfPlan(['r:a', 'r:b'], A, of(entry({ complete: false })))],
    ['没有路径', () => mergePdfPlan(['r:a', 'r:b'], A, of(entry({ path: null })))],
    ['图的 stem 不在 page_stems 里', () => mergePdfPlan(['r:a', 'r:b'], A, of(entry({ page_stems: ['a', 'z'] })))],
    ['同 stem 两张图（不同运行配置）', () => mergePdfPlan(['r:a', 'r:a2'], [...A, asset('r:a2', 'a')], of(entry()))],
    ['没有 PdfPages 记录', () => mergePdfPlan(['r:a', 'r:b'], A, of())],
  ])('不提供：%s', (_n, run) => {
    expect(run()).toBeNull()
  })
  it('第一项对不上就看下一项', () => {
    const other = entry({ path: 'x.pdf', page_stems: ['q', 'r'] })
    expect(mergePdfPlan(['r:a', 'r:b'], A, of(other, entry()))?.name).toBe('all_samples')
  })
})

describe('账本只属于产出它的那次运行（Codex #1395 P1）', () => {
  const ledger = (runConfig: string) => () => ({ pages: [entry()], runConfig })
  it('素材与账本同一配置：提供', () => {
    const X = [asset('r:a', 'a', 's.py', 'rc_x'), asset('r:b', 'b', 's.py', 'rc_x')]
    expect(mergePdfPlan(['r:a', 'r:b'], X, ledger('rc_x'))?.figureIds).toEqual(['r:a', 'r:b'])
  })
  it('配置 X 的 a 与配置 Y 的 b：不提供（不拼两次运行都没产出过的混合 PDF）', () => {
    const mixed = [asset('r:a', 'a', 's.py', 'rc_x'), asset('r:b', 'b', 's.py', 'rc_y')]
    expect(mergePdfPlan(['r:a', 'r:b'], mixed, ledger('rc_x'))).toBeNull()
  })
  it('账本是别的配置那次运行的：不提供', () => {
    const X = [asset('r:a', 'a', 's.py', 'rc_x'), asset('r:b', 'b', 's.py', 'rc_x')]
    expect(mergePdfPlan(['r:a', 'r:b'], X, ledger(''))).toBeNull()
    expect(mergePdfPlan(['r:a', 'r:b'], A, ledger('rc_x'))).toBeNull()
  })
})

describe('pdfPagesBaseName', () => {
  it.each([
    ['all_samples.pdf', 'all_samples'],
    ['out/sub/All.PDF', 'All'],
    ['a\\b\\c.pdf', 'c'],
  ])('%s -> %s', (p, n) => expect(pdfPagesBaseName(p)).toBe(n))
})
