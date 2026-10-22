import { describe, expect, it } from 'vitest'
import { runtimeSiblingOf } from './assetSibling'
import type { PanelInfo, RuntimeAssetInfo } from './api'

const panel = (id: string, name: string, extra: Partial<PanelInfo> = {}) =>
  ({ id, name, script: 'panels.py', ...extra }) as PanelInfo
const asset = (stem: string) => ({ id: `runtime:${stem}`, script: 'panels.py', stem }) as RuntimeAssetInfo

describe('runtimeSiblingOf', () => {
  it('同脚本同 stem 的磁盘图是它的同源', () => {
    const a = panel('Fig1.pdf', 'Fig1')
    expect(runtimeSiblingOf(asset('Fig1'), [a])).toBe(a)
  })

  it('注册表说 panel.pdf 是 panel-2（#1251）：配对按后端给的 stem，不按文件名', () => {
    const jia = panel('panel.png', 'panel')
    const yi = panel('panel.pdf', 'panel', { stem: 'panel-2' })
    expect(runtimeSiblingOf(asset('panel-2'), [jia, yi])).toBe(yi)
    expect(runtimeSiblingOf(asset('panel'), [jia, yi])).toBe(jia)
  })
})
