/**
 * SVG 导入的**前端那一侧**（ADR 0185 M5）：读后端快照 `tests/golden/svgimport/*.json`（`scripts/dev/gen_svgimport_golden.py`），
 * 核前端落位（换 id / 组 id、平移到落点）后：每个对象本构建都认识、组链完整、剪切组的蒙版生效、导出载荷收得下（被裁的对象带 clip）。
 */
import { describe, expect, it } from 'vitest'
import illustrator from '../../../tests/golden/svgimport/illustrator.json'
import inkscape from '../../../tests/golden/svgimport/inkscape.json'
import { isKnownObject } from '@/types/document'
import { toExportObjects } from './exportPayload'
import { groupMasks, groupPathOf } from './groupTree'
import { looksLikeSvg, placeImported } from './svgImport'

describe('后端快照落到画布', () => {
  it.each([
    ['illustrator', illustrator],
    ['inkscape', inkscape],
  ])('%s：全部认识、id 换新、整体平移', (_name, snap) => {
    const placed = placeImported(snap.objects, { x: 10, y: 20 })!
    expect(placed).toHaveLength(snap.objects.length)
    placed.forEach((o, i) => {
      const src = snap.objects[i] as { id: string; x: number; y: number }
      expect(isKnownObject(o)).toBe(true)
      expect(o.id).not.toBe(src.id)
      expect(o.x).toBeCloseTo(src.x + 10)
      expect(o.y).toBeCloseTo(src.y + 20)
    })
    // 组 id 整批换新：同一个旧组还是同一个新组
    const before = new Map<string, Set<string>>()
    snap.objects.forEach((o: { groupId?: string }, i: number) => {
      if (!o.groupId) return
      before.set(o.groupId, (before.get(o.groupId) ?? new Set()).add(groupPathOf(placed[i]).at(-1)!))
    })
    for (const [old, s] of before) {
      expect(s.size).toBe(1)
      expect([...s][0]).not.toBe(old) // 换成了新 id，不会和画布上已有的组撞上
    }
    expect(() => toExportObjects(placed)).not.toThrow()
  })

  it('Illustrator 样例的 clipPath 落成生效的剪切组：圆被裁、蒙版不进载荷', () => {
    const placed = placeImported(illustrator.objects, { x: 0, y: 0 })!
    expect(groupMasks(placed).size).toBe(1)
    const payload = toExportObjects(placed)
    expect(payload).toHaveLength(placed.length - 1)
    expect(payload.filter((o) => o.clip?.length)).toHaveLength(1)
  })

  it('一个能用的都没有 = null（不认识的类型丢掉）', () => {
    expect(placeImported([{ id: 'x', type: 'blob', x: 0, y: 0 }], { x: 0, y: 0 })).toBeNull()
  })
})

describe('认不认得出 SVG 标记', () => {
  it.each([
    ['<svg xmlns="http://www.w3.org/2000/svg"></svg>', true],
    ['  <?xml version="1.0"?>\n<svg>', true],
    ['<!-- Generator: Adobe Illustrator -->\n<svg version="1.1">', true],
    ['hello <svg>', false],
    ['<html><svg></svg></html>', false],
    ['', false],
  ])('%s → %s', (text, want) => {
    expect(looksLikeSvg(text)).toBe(want)
  })
})
