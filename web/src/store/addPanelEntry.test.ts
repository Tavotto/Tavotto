/**
 * 界面「把一张图加到画布」只许经 `workspace.addPanelToCanvas` / `addRuntimePanelToCanvas`
 * （#706 评审 P2）：`actions.addPanel` / `addRuntimePanel` 只管文档、不碰视口，直接调它们
 * 的入口会绕过 `frameAddedPanel`——那一轮评审里三个对话框正是这样漏掉取景的，随后又在
 * 快编模式下各自踩到停放视口。新入口直接调 action，这里红。
 */
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { productionFiles } from '../../scripts/import-graph.mjs'

const WEB = path.resolve(__dirname, '../..')
/** 允许直接调用的地方：定义处，与唯一的界面入口所在 */
const ALLOWED = new Set(['src/store/actions.ts', 'src/store/workspace.ts'])
const CALL = /\badd(?:Runtime)?Panel\s*\(/

describe('加图 action 只有 workspace 一个调用方', () => {
  const files = productionFiles() as string[]
  const callers = files.filter((rel) => CALL.test(fs.readFileSync(path.join(WEB, rel), 'utf8')))

  it('尺子是活的：扫得到文件，也认得出 workspace 里的那两处调用', () => {
    expect(files.length).toBeGreaterThan(100)
    expect(callers).toContain('src/store/workspace.ts')
  })

  it('除了 actions / workspace，没有生产模块直接调 addPanel / addRuntimePanel', () => {
    expect(callers.filter((rel) => !ALLOWED.has(rel))).toEqual([])
  })
})
