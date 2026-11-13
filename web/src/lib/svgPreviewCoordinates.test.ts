import { beforeEach, describe, expect, it } from 'vitest'
import { svgPreviewCoordinates } from './svgPreviewCoordinates'

const SIZE = [200 * 25.4 / 72, 100 * 25.4 / 72]
let svg: SVGSVGElement
const target = (id: string) => svg.querySelector(`[id="${id}"]`)!

beforeEach(() => {
  document.body.innerHTML = `<svg viewBox="50 20 100 100" width="200pt" height="100pt">
    <g id="source"/>
    <g data-tavotto-native-viewport-pt="200 100" transform="matrix(0.5 0 0 0.5 50 20)">
      <g id="native"/>
      <g data-tavotto-native-viewport-pt="300 150"><g id="nested"/></g>
    </g>
    <g data-tavotto-native-viewport-pt="400 250"><g id="other"/></g>
  </svg>`
  svg = document.querySelector('svg')!
})

describe('目标节点的共享DOM预览坐标', () => {
  it.each(['none', 'xMidYMid meet', 'xMaxYMin slice'])(
    'native pt依据不受原root非零viewBox/缩放/PAR=%s影响', (par) => {
      svg.setAttribute('preserveAspectRatio', par)
      expect(svgPreviewCoordinates(svg, target('native'), SIZE)).toEqual({
        space: 'native-pt', width: 200, height: 100, unitsPerPt: 1,
      })
    },
  )

  it('逐个target读取最近wrapper，不把整份SVG或兄弟native的坐标套过来', () => {
    expect(svgPreviewCoordinates(svg, target('nested'), SIZE)).toMatchObject({ width: 300, height: 150, unitsPerPt: 1 })
    expect(svgPreviewCoordinates(svg, target('other'), SIZE)).toMatchObject({ width: 400, height: 250, unitsPerPt: 1 })
    expect(svgPreviewCoordinates(svg, target('source'), SIZE)).toMatchObject({
      space: 'root-user-units', width: 100, height: 100,
    })
    expect(svgPreviewCoordinates(svg, target('source'), SIZE)?.unitsPerPt).toBeCloseTo(0.5, 12)
    expect(svgPreviewCoordinates(svg, svg, SIZE)?.space).toBe('root-user-units')
  })

  it.each(['', '200', '200 100 3', '0 100', '-20 100', '200 NaN', 'Infinity 100', '1e999 100', '0x20 100', '200,100'])(
    '坏marker %s 显式拒绝，不退回合法祖先或旧root', (marker) => {
      target('nested').parentElement!.setAttribute('data-tavotto-native-viewport-pt', marker)
      expect(svgPreviewCoordinates(svg, target('nested'), SIZE)).toBeNull()
    },
  )

  it('允许正有限SVG数字，保留旧缺席回退并限制在本面板内', () => {
    target('nested').parentElement!.setAttribute('data-tavotto-native-viewport-pt', '+2e2 .5')
    expect(svgPreviewCoordinates(svg, target('nested'))).toMatchObject({ width: 200, height: 0.5, unitsPerPt: 1 })
    expect(svgPreviewCoordinates(null)).toEqual({ space: 'root-user-units', width: 100, height: 100, unitsPerPt: 1 })
    expect(svgPreviewCoordinates(svg, document.body, SIZE)).toBeNull()
    svg.removeAttribute('viewBox')
    expect(svgPreviewCoordinates(svg, target('source'), SIZE)).toEqual({ space: 'root-user-units', width: 100, height: 100, unitsPerPt: 1 })
  })
})
