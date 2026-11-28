/**
 * 组名与组链的写路径登记（ADR 0191 修订）：谁调 `setGroupPath` 是有名单的。
 *
 * `setGroupPath` 自己会清链外的名字键，所以「改链」的调用点不用管名字；但**派生新对象**（路径文字、文字转轮廓、拆 SVG……）
 * 的调用点得让新对象带上源对象在外层组上的名字（`inheritGroupNames`）。新增一个调用 `setGroupPath` 的文件而没有登记在这里就红——
 * 登记时写明它属于哪一类，别让名字在某条新路径上悄悄丢掉。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = path.resolve(process.cwd(), 'src')
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = path.join(dir, f)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f) ? [p] : []
  })
}
const rel = (p: string) => path.relative(SRC, p).split(path.sep).join('/')

/** 改链（拆组 / 成组 / 孤儿蒙版 / 复制换 id）：setGroupPath 清链外键，不需要继承；`derive` = 派生新对象，必须同时调 `inheritGroupNames` */
const CALLERS: Record<string, 'chain' | 'derive'> = {
  'store/actions.ts': 'chain', // groupUnits / releaseOrphanMasks / ungroupSelected；复制走 remapGroupPath（键跟着换）
  'store/pathTextActions.ts': 'derive',
  'store/textOutlineActions.ts': 'derive',
  'store/svgImportActions.ts': 'derive',
}

describe('setGroupPath 调用点登记', () => {
  const files = walk(SRC).filter((p) => rel(p) !== 'lib/groupTree.ts')
  const callers = files.filter((p) => /\bsetGroupPath\(/.test(readFileSync(p, 'utf8'))).map(rel).sort()

  it('调用 setGroupPath 的文件都登记过', () => {
    expect(callers).toEqual(Object.keys(CALLERS).sort())
  })

  it.each(Object.entries(CALLERS).filter(([, k]) => k === 'derive'))('%s 派生新对象，要带上源对象的组名', (file) => {
    expect(readFileSync(path.join(SRC, file), 'utf8')).toMatch(/\binheritGroupNames\(/)
  })
})
