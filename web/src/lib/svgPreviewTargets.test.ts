import { beforeEach, describe, expect, it } from 'vitest'
import { svgPreviewTargets } from './svgPreviewTargets'

let svg: SVGSVGElement
const map = (value: unknown) => svg.setAttribute('data-tavotto-svg-targets', JSON.stringify(value))
const ids = (gid: string, kind: 'transform' | 'style' = 'transform') => svgPreviewTargets(svg, gid, kind)?.nodes.map(node => node.id)
beforeEach(() => {
  document.body.innerHTML = `<svg viewBox="50 20 100 100">
    <g id="source"/>
    <g data-tavotto-native-viewport-pt="200 100">
      <g id="a"><path id="nested"/></g><g id="b"/>
    </g>
    <g data-tavotto-native-viewport-pt="400 200"><g id="c"/></g>
  </svg>`
  svg = document.querySelector('svg')!
})

describe('native DOM target镜像只供临时预览', () => {
  it('无镜像保持普通SVG的单gid与缺席行为', () => {
    expect(ids('source')).toEqual(['source'])
    expect(svgPreviewTargets(svg, 'source', 'transform')?.mapped).toBe(false)
    expect(ids('missing')).toEqual([])
    expect(svgPreviewTargets(null, 'a', 'style')).toEqual({ mapped: false, nodes: [] })
  })

  it('一个语义gid映射跨wrapper的paint序antichain，transform/style各按权威数组', () => {
    map({ proxy: { transform: ['a', 'c'], style: ['b'] } })
    expect(ids('proxy')).toEqual(['a', 'c'])
    expect(ids('proxy', 'style')).toEqual(['b'])
    expect(svgPreviewTargets(svg, 'proxy', 'transform')?.mapped).toBe(true)
  })

  it('合法空数组与缺项都不借源场景的同名gid', () => {
    map({ source: { transform: [], style: [] } })
    expect(ids('source')).toEqual([])
    expect(svgPreviewTargets(svg, 'a', 'transform')).toBeNull()
  })

  it.each([null, [], 2, {}, { transform: ['a'] }, { transform: ['a'], style: [], extra: true },
    { transform: ['a', 2], style: [] }, { transform: [''], style: [] }].map(entry => ({ entry })))('坏entry $entry 不降级旧gid', ({ entry }) => {
    map({ source: entry })
    expect(svgPreviewTargets(svg, 'source', 'transform')).toBeNull()
  })

  it.each([
    ['a', 'a'], ['a', 'nested'], ['nested', 'a'], ['c', 'a'], ['a', 'missing'], ['source'],
  ].map(targets => ({ targets })))('重复/重叠/反序/丢失/非native目标 $targets 整次拒绝', ({ targets }) => {
    map({ proxy: { transform: targets, style: [] } })
    expect(svgPreviewTargets(svg, 'proxy', 'transform')).toBeNull()
  })

  it('目标ID不唯一、wrapper本身或坏pt标记均不可被当成native绘制目标', () => {
    map({ proxy: { transform: ['a'], style: [] } })
    svg.querySelector('#a')!.parentElement!.insertAdjacentHTML('beforeend', '<g id="a"/>')
    expect(svgPreviewTargets(svg, 'proxy', 'transform')).toBeNull()
    svg.querySelectorAll('#a')[1].remove()
    svg.querySelector('#a')!.setAttribute('data-tavotto-native-viewport-pt', '200 100')
    expect(svgPreviewTargets(svg, 'proxy', 'transform')).toBeNull()
    svg.querySelector('#a')!.removeAttribute('data-tavotto-native-viewport-pt')
    svg.querySelector('#a')!.parentElement!.setAttribute('data-tavotto-native-viewport-pt', '200 NaN')
    expect(svgPreviewTargets(svg, 'proxy', 'transform')).toBeNull()
  })

  it('同entry任一操作目标坏了即拒绝，不先写合法一半', () => {
    map({ proxy: { transform: ['a'], style: ['missing'] } })
    expect(svgPreviewTargets(svg, 'proxy', 'transform')).toBeNull()
  })

  it('解析缓存不让新坏镜像或已移除节点保留旧资格', () => {
    map({ proxy: { transform: ['a'], style: [] } })
    expect(ids('proxy')).toEqual(['a'])
    svg.querySelector('#a')!.remove()
    expect(svgPreviewTargets(svg, 'proxy', 'transform')).toBeNull()
    svg.setAttribute('data-tavotto-svg-targets', '{')
    expect(svgPreviewTargets(svg, 'proxy', 'transform')).toBeNull()
  })
})
