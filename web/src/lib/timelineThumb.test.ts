/**
 * 时间线缩略图把面板 SVG 变成可画的图（Codex #679）：根元素上的 width / height /
 * preserveAspectRatio 是**解析后 setAttribute**，不是往 `<svg` 后面拼——renderStore 里的
 * SVG 经 `prepareSvg` 已经带着 preserveAspectRatio，拼一份就是重复属性，整份被浏览器拒收。
 */
import { describe, expect, it } from 'vitest'
import { sizedSvgMarkup } from './timelineThumb'

const count = (s: string, needle: string) => s.split(needle).length - 1
const parses = (s: string) =>
  new DOMParser().parseFromString(s, 'image/svg+xml').getElementsByTagName('parsererror').length === 0

/** 与 `renderStore.prepareSvg` 产出同形：去掉 pt 宽高、带上 preserveAspectRatio 与铺满的 style */
const PREPARED =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50" preserveAspectRatio="none" ' +
  'style="width:100%;height:100%;display:block"><rect width="100" height="50" fill="#123"/></svg>'

describe('sizedSvgMarkup', () => {
  it('输入已经带着 preserveAspectRatio / width / style：输出能解析、根上每个属性只有一份', () => {
    const src = PREPARED.replace('<svg ', '<svg width="3pt" height="2pt" ')
    expect(parses(src)).toBe(true)
    const out = sizedSvgMarkup(src, 200.4, 99.6)!
    expect(out).not.toBeNull()
    expect(parses(out)).toBe(true)
    const root = new DOMParser().parseFromString(out, 'image/svg+xml').documentElement
    expect(root.getAttribute('width')).toBe('200')
    expect(root.getAttribute('height')).toBe('100')
    expect(root.getAttribute('preserveAspectRatio')).toBe('none')
    expect(root.hasAttribute('style')).toBe(false)
    const head = out.slice(0, out.indexOf('>'))
    expect(count(head, 'preserveAspectRatio=')).toBe(1)
    expect(count(head, ' width=')).toBe(1)
    expect(count(head, ' height=')).toBe(1)
    // 内容原样
    expect(out).toContain('fill="#123"')
  })

  it('没有这些属性的原文：补上', () => {
    const out = sizedSvgMarkup('<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>', 10, 20)!
    const root = new DOMParser().parseFromString(out, 'image/svg+xml').documentElement
    expect([root.getAttribute('width'), root.getAttribute('height'), root.getAttribute('preserveAspectRatio')]).toEqual([
      '10',
      '20',
      'none',
    ])
  })

  it('不是合法的 SVG：返回 null（调用方换素材图那一路）', () => {
    expect(sizedSvgMarkup('<svg><g></svg>', 10, 10)).toBeNull()
    expect(sizedSvgMarkup('<html></html>', 10, 10)).toBeNull()
  })
})
