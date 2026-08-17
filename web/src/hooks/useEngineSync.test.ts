import { describe, expect, it } from 'vitest'
import { pickRenderTargets } from './useEngineSync'
import type { CanvasObject, PanelObject } from '@/types/document'

function panel(id: string, fileId: string, overrides: number): PanelObject {
  return {
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    fileId,
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    script: 'fig.py',
    overrides: Array.from({ length: overrides }, (_, i) => ({
      gid: `g${i}`,
      prop: 'color',
      value: '#000',
    })),
  } as PanelObject
}

describe('pickRenderTargets', () => {
  it('每个 fileId 只选一个面板', () => {
    // 复制面板会保留原 fileId（structuredClone），改其中一个之后两者
    // overrides 不同。若两个都去请求渲染，就会同步互相顶掉对方的
    // wantPatches → effect ↔ store 死循环 → React #185 整个界面白掉。
    const objects: CanvasObject[] = [panel('a', 'Fig1.pdf', 2), panel('b', 'Fig1.pdf', 0)]
    const targets = pickRenderTargets(objects, null, { 'Fig1.pdf': { tracked: true } })
    expect(targets).toHaveLength(1)
    expect(targets[0].id).toBe('a') // 改动多的说了算
  })

  it('正在图内编辑的面板优先于改动更多的副本', () => {
    const objects: CanvasObject[] = [panel('a', 'Fig1.pdf', 5), panel('b', 'Fig1.pdf', 1)]
    const targets = pickRenderTargets(objects, 'b', { 'Fig1.pdf': { tracked: true } })
    expect(targets.map((t) => t.id)).toEqual(['b'])
  })

  it('不同文件各自入选', () => {
    const objects: CanvasObject[] = [panel('a', 'Fig1.pdf', 1), panel('b', 'Fig2.pdf', 1)]
    expect(pickRenderTargets(objects, null, {})).toHaveLength(2)
  })

  it('无改动也未被跟踪的面板不进渲染队列', () => {
    // 磁盘文件本身就是那个样子，白跑一次引擎（heavy 脚本要几分钟）没意义
    expect(pickRenderTargets([panel('a', 'Fig1.pdf', 0)], null, {})).toEqual([])
  })

  it('没有脚本的面板永远不进队列', () => {
    const raster = { ...panel('a', 'photo.png', 3), script: undefined } as PanelObject
    expect(pickRenderTargets([raster], 'a', {})).toEqual([])
  })
})
