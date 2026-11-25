/** 右栏标题里的文件名 / 工作表名是用户自己的字符串：原样显示（`[data-literal]`），不套标题类的中西文自动留白。 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { DataPanel } from './DataPanel'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLDivElement
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  useDataFigureStore.getState().clear()
})
const mount = async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(<TooltipProvider><DataPanel /></TooltipProvider>))
}

describe('文件名原样', () => {
  it('标题里文件名与工作表名各自带 data-literal，文字一字不差（含中西文相邻的写法）', async () => {
    for (const [path, sheet] of [['dir/均值_err不明.csv', null], ['剂量响应_带sd.csv', '有一组只有一个值']] as const) {
      useDataFigureStore.setState({ selected: { path, sheet }, group: null, phase: 'ready', roles: null })
      await mount()
      const lits = [...document.querySelectorAll('[data-data-panel] h2 [data-literal]')].map((e) => e.textContent)
      expect(lits).toEqual(sheet ? [path.slice(path.lastIndexOf('/') + 1), sheet] : [path.slice(path.lastIndexOf('/') + 1)])
      expect(document.querySelector('[data-data-panel] h2')!.textContent).toBe(`${lits[0]}${sheet ? ` · ${sheet}` : ''}`)
      await act(async () => root.unmount())
      host.remove()
      root = createRoot((host = document.createElement('div')))
    }
  })

  it('合画的标题是翻译好的句子，没有用户字符串，不加这个属性', async () => {
    useDataFigureStore.setState({ selected: { path: 'a.csv', sheet: null }, group: [{ path: 'a.csv', sheet: null }, { path: 'b.csv', sheet: null }], phase: 'ready', roles: null })
    await mount()
    expect(document.querySelector('[data-data-panel] h2 [data-literal]')).toBeNull()
  })
})

describe('index.css：[data-literal] 在无层的「字面数据」规则里（压过任何 utilities 层的 text-autospace: normal）', () => {
  const css = readFileSync(`${process.cwd()}/src/index.css`, 'utf8')
  it('选择器列表里有它、声明是 no-autospace、位置在所有 @layer 之外', () => {
    const at = css.indexOf('.font-mono,\n[data-literal] {\n  text-autospace: no-autospace;')
    expect(at).toBeGreaterThan(0)
    let depth = 0
    for (const ch of css.slice(0, at)) depth += ch === '{' ? 1 : ch === '}' ? -1 : 0
    expect(depth).toBe(0)
  })
})
