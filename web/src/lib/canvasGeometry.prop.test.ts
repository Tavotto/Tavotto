/**
 * 画布坐标变换的属性测试（fast-check，参数见 `test/prop.ts`：固定种子、失败打印 seed 与 path）。
 *
 * 根 AGENTS.md 头号风险：画布视觉位置与逻辑坐标分叉。这里只测**纯函数层**——视口换算、
 * 旋转 / 翻转、单选缩放落位、多选对齐与成组缩放、图内标注随图缩放。每条性质的主语写在
 * 它的 describe / it 名里：谁的、哪个时刻、哪个维度。
 */
import fc from 'fast-check'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  alignOneVisual,
  anchoredRect,
  applyAlign,
  boundsOf,
  normalizeRotationDeg,
  resizeRect,
  rotateVecDeg,
  unrotateVecDeg,
  visualBounds,
  type AlignMode,
  type Rect,
  type ResizeDir,
} from './geometry'
import {
  clientToMm,
  mmToPx,
  mmToViewX,
  mmToViewY,
  pxToMm,
  useViewportStore,
  visibleRectMm,
  MAX_ZOOM,
  MIN_ZOOM,
  type ViewTransform,
} from '@/store/viewportStore'
import { remapBox, resizeGroup, unionBox, type Rect4 } from './axesLayout'
import {
  contentToPageVec,
  pageToContentVec,
  panelContentTransform,
} from './panelTransform'
import { applyAnnotationScale, captureOrigins } from './annotationScale'
import { near, propParams } from '@/test/prop'
import type { CanvasObject, PanelObject, ShapeObject, TextObject } from '@/types/document'
import { panelContentSize, rotateVec } from '@/types/document'

/* ------------------------------ 生成器 ------------------------------------ */

const mm = fc.double({ min: -500, max: 500, noNaN: true, noDefaultInfinity: true }).map((v) => v + 0)
const pos = fc.double({ min: 0.5, max: 400, noNaN: true, noDefaultInfinity: true })
const px = fc.double({ min: -4000, max: 4000, noNaN: true, noDefaultInfinity: true }).map((v) => v + 0)
const zoom = fc.double({ min: MIN_ZOOM, max: MAX_ZOOM, noNaN: true, noDefaultInfinity: true })
const deg = fc.double({ min: -720, max: 720, noNaN: true, noDefaultInfinity: true })

const viewTransform: fc.Arbitrary<ViewTransform> = fc.record({
  zoom,
  panX: px,
  panY: px,
  originX: fc.double({ min: 0, max: 600, noNaN: true, noDefaultInfinity: true }),
  originY: fc.double({ min: 0, max: 200, noNaN: true, noDefaultInfinity: true }),
})

const rect: fc.Arbitrary<Rect> = fc.record({ x: mm, y: mm, w: pos, h: pos })
/** 缩放手柄只对不小于 resizeRect 最小尺寸（3 × 2 mm）的对象有定义：更小的对象第一帧就被钳大 */
const resizable: fc.Arbitrary<Rect> = fc.record({
  x: mm,
  y: mm,
  w: fc.double({ min: 3, max: 400, noNaN: true, noDefaultInfinity: true }),
  h: fc.double({ min: 2, max: 400, noNaN: true, noDefaultInfinity: true }),
})

const textObj = (id: string, r: Rect, rotationDeg: number): TextObject => ({
  id,
  type: 'text',
  text: 't',
  sizePt: 9,
  bold: false,
  color: '#000',
  align: 'left',
  ...r,
  ...(rotationDeg ? { rotationDeg } : {}),
})
const shapeObj = (id: string, r: Rect, rotationDeg: number): ShapeObject => ({
  id,
  type: 'shape',
  shape: 'rect',
  strokePt: 1,
  color: '#000',
  fill: null,
  ...r,
  ...(rotationDeg ? { rotationDeg } : {}),
})
const panelObj = (id: string, r: Rect, rotation: 0 | 90 | 180 | 270, flipH: boolean, flipV: boolean): PanelObject =>
  ({
    id,
    type: 'panel',
    fileId: 'f',
    fileKind: 'pdf',
    nativeW: 100,
    nativeH: 60,
    overrides: [],
    ...r,
    ...(rotation ? { rotation } : {}),
    ...(flipH ? { flipH } : {}),
    ...(flipV ? { flipV } : {}),
  }) as PanelObject

/** 标注（文字 / 形状，可带任意角度）；面板在另一条性质里单独测 */
const annotation: fc.Arbitrary<CanvasObject> = fc
  .tuple(fc.constantFrom('text', 'shape'), rect, deg, fc.uuid())
  .map(([k, r, d, id]) => (k === 'text' ? textObj(id, r, d) : shapeObj(id, r, d)))

const dirArb = fc.constantFrom<ResizeDir>('nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w')

/** 一个对象的四个角（未旋转局部系里绕中心转 deg 后的页面坐标） */
function pageCorners(o: Rect, rotDeg: number): [number, number][] {
  const cx = o.x + o.w / 2
  const cy = o.y + o.h / 2
  return [
    [-o.w / 2, -o.h / 2],
    [o.w / 2, -o.h / 2],
    [o.w / 2, o.h / 2],
    [-o.w / 2, o.h / 2],
  ].map(([dx, dy]) => {
    const [rx, ry] = rotateVecDeg(dx, dy, rotDeg)
    return [cx + rx, cy + ry]
  })
}

/* ------------------------------ 视口换算 ----------------------------------- */

describe('视口坐标换算（屏幕 ↔ 文档 mm）', () => {
  it('clientToMm 是 mmToViewX/Y + 视口原点 的逆：任意 zoom/pan/origin、x 与 y 两个维度', () => {
    fc.assert(
      fc.property(viewTransform, mm, mm, (t, x, y) => {
        const cx = mmToViewX(x, t) + t.originX
        const cy = mmToViewY(y, t) + t.originY
        const back = clientToMm(cx, cy, t)
        expect(near(back.x, x)).toBe(true)
        expect(near(back.y, y)).toBe(true)
      }),
      propParams(2400),
    )
  })

  it('长度换算 mmToPx / pxToMm 互逆，且与点换算的差分一致（任意 zoom）', () => {
    fc.assert(
      fc.property(viewTransform, mm, mm, (t, a, b) => {
        expect(near(pxToMm(mmToPx(a, t), t), a)).toBe(true)
        // 平移不变：两点在屏幕上的间距 = 长度换算
        const d = mmToViewX(a, t) - mmToViewX(b, t)
        expect(near(d, mmToPx(a - b, t), 1e-6)).toBe(true)
      }),
      propParams(1800),
    )
  })

  it('visibleRectMm 的四个角正是视口四角经 clientToMm 的结果（此刻的可见矩形 = 屏幕角点的反算）', () => {
    fc.assert(
      fc.property(viewTransform, pos, pos, (t, vw, vh) => {
        const r = visibleRectMm(t, vw, vh)!
        // clientToMm 吃的是 client 坐标 = 视口内坐标 + origin
        const tl = clientToMm(t.originX, t.originY, t)
        const br = clientToMm(t.originX + vw, t.originY + vh, t)
        expect(near(r.x, tl.x)).toBe(true)
        expect(near(r.y, tl.y)).toBe(true)
        expect(near(r.x + r.w, br.x, 1e-6)).toBe(true)
        expect(near(r.y + r.h, br.y, 1e-6)).toBe(true)
      }),
      propParams(1800),
    )
  })

  describe('zoomAt / panBy 一串操作（store 真实路径）', () => {
    beforeEach(() => {
      useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0, fitted: false })
    })

    const op = fc.oneof(
      fc.record({ k: fc.constant('pan' as const), dx: px, dy: px }),
      fc.record({
        k: fc.constant('zoom' as const),
        f: fc.double({ min: 0.5, max: 2, noNaN: true, noDefaultInfinity: true }),
        ax: fc.double({ min: 0, max: 1500, noNaN: true, noDefaultInfinity: true }),
        ay: fc.double({ min: 0, max: 900, noNaN: true, noDefaultInfinity: true }),
      }),
    )

    it('缩放锚点下的文档点不动；平移后缩放也一样（每一步、x 与 y）；zoom 始终在 [MIN, MAX]', () => {
      fc.assert(
        fc.property(fc.array(op, { minLength: 1, maxLength: 25 }), (ops) => {
          useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, originX: 0, originY: 0 })
          for (const o of ops) {
            const s = useViewportStore.getState()
            if (o.k === 'pan') {
              const before = clientToMm(300, 200)
              s.panBy(o.dx, o.dy)
              // 平移 dx px = 文档点的屏幕位置整体挪 dx：原先在 (300,200) 的点现在在 (300+dx, 200+dy)
              const moved = clientToMm(300 + o.dx, 200 + o.dy)
              expect(near(moved.x, before.x, 1e-6)).toBe(true)
              expect(near(moved.y, before.y, 1e-6)).toBe(true)
            } else {
              const anchorMm = clientToMm(o.ax, o.ay)
              s.zoomAt(o.f, o.ax, o.ay)
              const after = clientToMm(o.ax, o.ay)
              expect(near(after.x, anchorMm.x, 1e-6)).toBe(true)
              expect(near(after.y, anchorMm.y, 1e-6)).toBe(true)
            }
            const z = useViewportStore.getState().zoom
            expect(z).toBeGreaterThanOrEqual(MIN_ZOOM)
            expect(z).toBeLessThanOrEqual(MAX_ZOOM)
          }
        }),
        propParams(1200),
      )
    })
  })
})

/* ------------------------------ 旋转 / 翻转 -------------------------------- */

describe('旋转与翻转的换算', () => {
  it('rotateVecDeg 与 unrotateVecDeg 互逆：任意角度（含直角整数倍）、任意向量', () => {
    fc.assert(
      fc.property(mm, mm, deg, (x, y, d) => {
        const [rx, ry] = rotateVecDeg(x, y, d)
        const [bx, by] = unrotateVecDeg(rx, ry, d)
        expect(near(bx, x, 1e-9)).toBe(true)
        expect(near(by, y, 1e-9)).toBe(true)
      }),
      propParams(2400),
    )
  })

  it('直角整数倍旋转逐位还原（不留 6e-17 的残差）', () => {
    fc.assert(
      fc.property(mm, mm, fc.integer({ min: -8, max: 8 }), (x, y, k) => {
        const [rx, ry] = rotateVecDeg(x, y, k * 90)
        const [bx, by] = unrotateVecDeg(rx, ry, k * 90)
        expect(Object.is(bx + 0, x + 0)).toBe(true)
        expect(Object.is(by + 0, y + 0)).toBe(true)
      }),
      propParams(1800),
    )
  })

  it('normalizeRotationDeg：落在 (-180, 180]、幂等、与原角度同一朝向', () => {
    fc.assert(
      fc.property(deg, (d) => {
        const n = normalizeRotationDeg(d)
        expect(n).toBeGreaterThan(-180)
        expect(n).toBeLessThanOrEqual(180)
        expect(normalizeRotationDeg(n)).toBe(n)
        // 同一朝向：用单位向量比（0.1° 取整的误差 ≤ 0.05° → 向量差 ≤ 1e-3）
        const [ax, ay] = rotateVecDeg(1, 0, d)
        const [bx, by] = rotateVecDeg(1, 0, n)
        expect(Math.hypot(ax - bx, ay - by)).toBeLessThan(2e-3)
      }),
      propParams(1800),
    )
  })

  it('visualBounds（任意角度标注）：外接框包住四个转出来的角、且每条边都被某个角顶住（紧）', () => {
    fc.assert(
      fc.property(rect, deg, (r, d) => {
        const o = textObj('a', r, normalizeRotationDeg(d))
        const vb = visualBounds(o)
        const cs = pageCorners(r, normalizeRotationDeg(d))
        const xs = cs.map((c) => c[0])
        const ys = cs.map((c) => c[1])
        expect(near(vb.x, Math.min(...xs), 1e-6)).toBe(true)
        expect(near(vb.y, Math.min(...ys), 1e-6)).toBe(true)
        expect(near(vb.x + vb.w, Math.max(...xs), 1e-6)).toBe(true)
        expect(near(vb.y + vb.h, Math.max(...ys), 1e-6)).toBe(true)
        // 包围盒中心 = 对象中心（旋转绕中心）
        expect(near(vb.x + vb.w / 2, r.x + r.w / 2, 1e-6)).toBe(true)
        expect(near(vb.y + vb.h / 2, r.y + r.h / 2, 1e-6)).toBe(true)
      }),
      propParams(1800),
    )
  })

  it('面板：contentToPageVec 与 pageToContentVec 互逆（旋转 × 水平翻转 × 垂直翻转 共 16 种）', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(0, 90, 180, 270) as fc.Arbitrary<0 | 90 | 180 | 270>,
        fc.boolean(),
        fc.boolean(),
        mm,
        mm,
        (rotation, flipH, flipV, x, y) => {
          const t = panelContentTransform({ rotation, flipH, flipV })
          const [px_, py_] = contentToPageVec(t, x, y)
          const [bx, by] = pageToContentVec(t, px_, py_)
          expect(near(bx, x, 1e-9)).toBe(true)
          expect(near(by, y, 1e-9)).toBe(true)
        },
      ),
      propParams(1800),
    )
  })

  it('面板：内容空间的四个角经 contentToPageVec 落在页面包围盒 (x,y,w,h) 的四个角上（视觉框 = 逻辑框；90/270 时宽高互换）', () => {
    fc.assert(
      fc.property(
        rect,
        fc.constantFrom(0, 90, 180, 270) as fc.Arbitrary<0 | 90 | 180 | 270>,
        fc.boolean(),
        fc.boolean(),
        (r, rotation, flipH, flipV) => {
          const p = panelObj('p', r, rotation, flipH, flipV)
          const t = panelContentTransform(p)
          const c = panelContentSize(p)
          const cx = p.x + p.w / 2
          const cy = p.y + p.h / 2
          const pageXs: number[] = []
          const pageYs: number[] = []
          for (const sx of [-1, 1])
            for (const sy of [-1, 1]) {
              const [dx, dy] = contentToPageVec(t, (sx * c.w) / 2, (sy * c.h) / 2)
              pageXs.push(cx + dx)
              pageYs.push(cy + dy)
            }
          expect(near(Math.min(...pageXs), p.x, 1e-9)).toBe(true)
          expect(near(Math.max(...pageXs), p.x + p.w, 1e-9)).toBe(true)
          expect(near(Math.min(...pageYs), p.y, 1e-9)).toBe(true)
          expect(near(Math.max(...pageYs), p.y + p.h, 1e-9)).toBe(true)
          // 面板的 visualBounds 就是它自己（x/y/w/h 已是旋转后的盒）
          expect(visualBounds(p)).toEqual({ x: p.x, y: p.y, w: p.w, h: p.h })
          // 翻转与旋转的先后：先翻转再旋转（与 CSS 同序）
          const [ux, uy] = contentToPageVec(t, 1, 2)
          const [wx, wy] = rotateVec(flipH ? -1 : 1, flipV ? -2 : 2, rotation)
          expect([ux, uy]).toEqual([wx, wy])
        },
      ),
      propParams(1800),
    )
  })
})

/* ------------------------------ 单选缩放 ----------------------------------- */

describe('单选缩放落位（旋转对象）', () => {
  it('anchoredRect：被拖手柄对侧在页面上纹丝不动（任意角度、8 个方向；角柄 / 自由边柄 = 锚点位置不动，等比边柄 = 对边所在直线不动）', () => {
    fc.assert(
      fc.property(
        resizable,
        dirArb,
        deg,
        fc.double({ min: -200, max: 200, noNaN: true, noDefaultInfinity: true }),
        fc.double({ min: -200, max: 200, noNaN: true, noDefaultInfinity: true }),
        fc.boolean(),
        (orig, dir, d0, dx, dy, proportional) => {
          const d = normalizeRotationDeg(d0)
          const next = resizeRect(orig, dir, dx, dy, proportional)
          const placed = anchoredRect(orig, next, dir, d)
          // 对侧锚点相对中心的偏移（局部系）
          const off = (r: Rect): [number, number] => [
            dir.includes('e') ? -r.w / 2 : dir.includes('w') ? r.w / 2 : 0,
            dir.includes('s') ? -r.h / 2 : dir.includes('n') ? r.h / 2 : 0,
          ]
          const anchorPage = (r: Rect): [number, number] => {
            const [ox, oy] = off(r)
            const [rx, ry] = rotateVecDeg(ox, oy, d)
            return [r.x + r.w / 2 + rx, r.y + r.h / 2 + ry]
          }
          const [ax, ay] = anchorPage(orig)
          const [bx, by] = anchorPage(placed)
          const corner = dir.length === 2
          const edgeProportional = !corner && proportional
          if (!edgeProportional) {
            expect(near(ax, bx, 1e-6)).toBe(true)
            expect(near(ay, by, 1e-6)).toBe(true)
          } else {
            // 等比边柄：另一轴随之伸缩，对边的中点会沿边滑动，但那条边所在的直线不动
            const horizontal = dir === 'e' || dir === 'w'
            const [ux, uy] = rotateVecDeg(horizontal ? 1 : 0, horizontal ? 0 : 1, d)
            expect(near(ax * ux + ay * uy, bx * ux + by * uy, 1e-6)).toBe(true)
          }
          // 尺寸不被落位改动
          expect(placed.w).toBe(next.w)
          expect(placed.h).toBe(next.h)
        },
      ),
      propParams(2400),
    )
  })

  it('resizeRect：被拖边的对边不动；等比时宽高比保持（角柄与边柄）', () => {
    fc.assert(
      fc.property(resizable, dirArb, mm, mm, (orig, dir, dx, dy) => {
        const n = resizeRect(orig, dir, dx, dy, true)
        if (dir.includes('e')) expect(near(n.x, orig.x)).toBe(true)
        if (dir.includes('w')) expect(near(n.x + n.w, orig.x + orig.w)).toBe(true)
        if (dir.includes('s')) expect(near(n.y, orig.y)).toBe(true)
        if (dir.includes('n')) expect(near(n.y + n.h, orig.y + orig.h)).toBe(true)
        expect(near(n.w / n.h, orig.w / orig.h, 1e-6)).toBe(true)
      }),
      propParams(1800),
    )
  })
})

/* ------------------------------ 多选变换 ----------------------------------- */

describe('多选对齐 / 分布：每个对象看得见的框与逻辑坐标一致', () => {
  const alignMode = fc.constantFrom<AlignMode>('left', 'hcenter', 'right', 'top', 'vcenter', 'bottom')
  const selection = fc.array(annotation, { minLength: 2, maxLength: 7 })

  it('对齐后：每个对象的 visualBounds 对在选区外接框的同一条边 / 中线上；选区外接框本身不漂（该轴上不扩不缩）', () => {
    fc.assert(
      fc.property(selection, alignMode, (objs, mode) => {
        const before = boundsOf(objs.map(visualBounds))!
        const work = structuredClone(objs)
        applyAlign(work, mode, { w: 300, h: 200 })
        const vbs = work.map(visualBounds)
        for (const vb of vbs) {
          if (mode === 'left') expect(near(vb.x, before.x, 1e-6)).toBe(true)
          if (mode === 'right') expect(near(vb.x + vb.w, before.x + before.w, 1e-6)).toBe(true)
          if (mode === 'hcenter') expect(near(vb.x + vb.w / 2, before.x + before.w / 2, 1e-6)).toBe(true)
          if (mode === 'top') expect(near(vb.y, before.y, 1e-6)).toBe(true)
          if (mode === 'bottom') expect(near(vb.y + vb.h, before.y + before.h, 1e-6)).toBe(true)
          if (mode === 'vcenter') expect(near(vb.y + vb.h / 2, before.y + before.h / 2, 1e-6)).toBe(true)
        }
        // 只平移 x/y：w/h/旋转不变
        work.forEach((o, i) => {
          expect(o.w).toBe(objs[i].w)
          expect(o.h).toBe(objs[i].h)
          expect(o.rotationDeg).toBe(objs[i].rotationDeg)
        })
      }),
      propParams(1800),
    )
  })

  it('对齐幂等：对同一选区同一模式再来一次，位置逐位（容差内）不变', () => {
    fc.assert(
      fc.property(selection, alignMode, (objs, mode) => {
        const once = structuredClone(objs)
        applyAlign(once, mode, { w: 300, h: 200 })
        const twice = structuredClone(once)
        applyAlign(twice, mode, { w: 300, h: 200 })
        twice.forEach((o, i) => {
          expect(near(o.x, once[i].x, 1e-6)).toBe(true)
          expect(near(o.y, once[i].y, 1e-6)).toBe(true)
        })
      }),
      propParams(1200),
    )
  })

  it('单选对齐以页面为框：看得见的框贴页面边 / 居中（旋转标注也不例外）', () => {
    fc.assert(
      fc.property(annotation, alignMode, (o, mode) => {
        const w = structuredClone(o)
        const page = { w: 300, h: 200 }
        applyAlign([w], mode, page)
        const vb = visualBounds(w)
        if (mode === 'left') expect(near(vb.x, 0, 1e-6)).toBe(true)
        if (mode === 'right') expect(near(vb.x + vb.w, page.w, 1e-6)).toBe(true)
        if (mode === 'hcenter') expect(near(vb.x + vb.w / 2, page.w / 2, 1e-6)).toBe(true)
        if (mode === 'top') expect(near(vb.y, 0, 1e-6)).toBe(true)
        if (mode === 'bottom') expect(near(vb.y + vb.h, page.h, 1e-6)).toBe(true)
        if (mode === 'vcenter') expect(near(vb.y + vb.h / 2, page.h / 2, 1e-6)).toBe(true)
      }),
      propParams(1200),
    )
  })

  it('水平 / 垂直等距分布：看得见的框之间的空隙相等，首尾框贴选区外接框', () => {
    fc.assert(
      fc.property(
        fc.array(annotation, { minLength: 3, maxLength: 7 }),
        fc.constantFrom<AlignMode>('hdist', 'vdist'),
        (objs, mode) => {
          const before = boundsOf(objs.map(visualBounds))!
          const work = structuredClone(objs)
          expect(applyAlign(work, mode, { w: 300, h: 200 })).toBe(true)
          const k = mode === 'hdist' ? 'x' : 'y'
          const s = mode === 'hdist' ? 'w' : 'h'
          // 分布按**起手时**的位置排序（并列时保持选区顺序）；事后位置可能因负空隙而换序，不能拿事后排序当序
          const order = objs
            .map((o, i) => ({ i, p: visualBounds(o)[k] }))
            .sort((a, b) => a.p - b.p)
            .map((e) => e.i)
          const sorted = order.map((i) => visualBounds(work[i]))
          const gaps = sorted.slice(1).map((vb, i) => vb[k] - (sorted[i][k] + sorted[i][s]))
          for (const g of gaps) expect(near(g, gaps[0], 1e-5)).toBe(true)
          expect(near(sorted[0][k], before[k], 1e-6)).toBe(true)
          const last = sorted[sorted.length - 1]
          expect(near(last[k] + last[s], before[k] + before[s], 1e-5)).toBe(true)
        },
      ),
      propParams(1200),
    )
  })

  it('alignOneVisual：对齐到任意参照框后，看得见的框贴在参照框的相应边上', () => {
    fc.assert(
      fc.property(annotation, alignMode, rect, (o, mode, box) => {
        const a = structuredClone(o)
        alignOneVisual(a, mode, box)
        // 对齐后外接框确实在参照框的相应边上
        const vb = visualBounds(a)
        if (mode === 'left') expect(near(vb.x, box.x, 1e-6)).toBe(true)
        if (mode === 'bottom') expect(near(vb.y + vb.h, box.y + box.h, 1e-6)).toBe(true)
      }),
      propParams(900),
    )
  })
})

describe('成组缩放（图内多选，figure 分数坐标）', () => {
  const frac = fc.double({ min: 0, max: 0.9, noNaN: true, noDefaultInfinity: true })
  const size = fc.double({ min: 0.02, max: 0.4, noNaN: true, noDefaultInfinity: true })
  const box4: fc.Arbitrary<Rect4> = fc.tuple(frac, frac, size, size)
  const boxes = fc.array(box4, { minLength: 2, maxLength: 8 })
  const frac1 = fc.double({ min: -0.5, max: 0.5, noNaN: true, noDefaultInfinity: true })

  it('组框拖到 next 后，各元素按 remapBox 映射：新框的并集 = next；组内相对布局（边的分数位置）不变', () => {
    fc.assert(
      fc.property(boxes, dirArb, frac1, frac1, (bs, dir, dfx, dfy) => {
        const g = unionBox(bs)!
        const next = resizeGroup(g, dir, dfx, dfy)
        const mapped = bs.map((b) => remapBox(b, g, next))
        const u = unionBox(mapped)!
        for (let i = 0; i < 4; i++) expect(near(u[i], next[i], 1e-9)).toBe(true)
        bs.forEach((b, i) => {
          const m = mapped[i]
          // 元素左边在组框里的分数位置、宽度占比，缩放前后一致
          expect(near((b[0] - g[0]) / g[2], (m[0] - next[0]) / next[2], 1e-9)).toBe(true)
          expect(near((b[1] - g[1]) / g[3], (m[1] - next[1]) / next[3], 1e-9)).toBe(true)
          expect(near(b[2] / g[2], m[2] / next[2], 1e-9)).toBe(true)
          expect(near(b[3] / g[3], m[3] / next[3], 1e-9)).toBe(true)
        })
      }),
      propParams(1800),
    )
  })

  it('往 next 再映射回 g（缩放往返）：每个元素还原；对边 / 对角锚点在缩放中不动', () => {
    fc.assert(
      fc.property(boxes, dirArb, frac1, frac1, (bs, dir, dfx, dfy) => {
        const g = unionBox(bs)!
        const next = resizeGroup(g, dir, dfx, dfy)
        bs.forEach((b) => {
          const back = remapBox(remapBox(b, g, next), next, g)
          for (let i = 0; i < 4; i++) expect(near(back[i], b[i], 1e-9)).toBe(true)
        })
        if (dir.includes('e')) expect(near(next[0], g[0], 1e-12)).toBe(true)
        if (dir.includes('w')) expect(near(next[0] + next[2], g[0] + g[2], 1e-9)).toBe(true)
        if (dir.includes('s')) expect(near(next[1], g[1], 1e-12)).toBe(true)
        if (dir.includes('n')) expect(near(next[1] + next[3], g[1] + g[3], 1e-9)).toBe(true)
        // 角柄等比
        if ((dir.includes('e') || dir.includes('w')) && (dir.includes('n') || dir.includes('s')))
          expect(near(next[2] / g[2], next[3] / g[3], 1e-9)).toBe(true)
        // 最小尺寸兜底：不缩成 0
        expect(next[2]).toBeGreaterThan(0)
        expect(next[3]).toBeGreaterThan(0)
      }),
      propParams(1800),
    )
  })
})

describe('图内标注随面板等比缩放（applyAnnotationScale）', () => {
  it('固定点不动：每个标注的框中心按 fixed + (c0 - fixed)·s 位似，尺寸与按页面 pt 计的量一起乘 s', () => {
    fc.assert(
      fc.property(
        fc.array(annotation, { minLength: 1, maxLength: 6 }),
        mm,
        mm,
        fc.double({ min: 0.1, max: 5, noNaN: true, noDefaultInfinity: true }),
        (objs, fx, fy, s) => {
          const work = structuredClone(objs)
          const origins = captureOrigins(work)
          applyAnnotationScale(work, origins, { x: fx, y: fy }, s)
          work.forEach((o, i) => {
            const c0x = objs[i].x + objs[i].w / 2
            const c0y = objs[i].y + objs[i].h / 2
            expect(near(o.x + o.w / 2, fx + (c0x - fx) * s, 1e-9)).toBe(true)
            expect(near(o.y + o.h / 2, fy + (c0y - fy) * s, 1e-9)).toBe(true)
            expect(near(o.w, objs[i].w * s, 1e-9)).toBe(true)
            expect(near(o.h, objs[i].h * s, 1e-9)).toBe(true)
            if (objs[i].type === 'text')
              expect(near((o as TextObject).sizePt, (objs[i] as TextObject).sizePt * s, 1e-9)).toBe(true)
            // 旋转角不被缩放改写
            expect(o.rotationDeg).toBe(objs[i].rotationDeg)
          })
          // 看得见的外接框中心也按同一位似走（旋转标注不漂）
          work.forEach((o, i) => {
            const v0 = visualBounds(objs[i])
            const v1 = visualBounds(o)
            expect(near(v1.x + v1.w / 2, fx + (v0.x + v0.w / 2 - fx) * s, 1e-6)).toBe(true)
            expect(near(v1.w, v0.w * s, 1e-6)).toBe(true)
          })
        },
      ),
      propParams(1800),
    )
  })

  it('每帧从起手快照现算（不累乘）：任意一串比例之后再回到 1，等于没动过', () => {
    fc.assert(
      fc.property(
        fc.array(annotation, { minLength: 1, maxLength: 5 }),
        mm,
        mm,
        fc.array(fc.double({ min: 0.1, max: 5, noNaN: true, noDefaultInfinity: true }), { minLength: 1, maxLength: 8 }),
        (objs, fx, fy, scales) => {
          const work = structuredClone(objs)
          const origins = captureOrigins(work)
          for (const s of scales) applyAnnotationScale(work, origins, { x: fx, y: fy }, s)
          applyAnnotationScale(work, origins, { x: fx, y: fy }, 1)
          work.forEach((o, i) => {
            expect(near(o.x, objs[i].x, 1e-9)).toBe(true)
            expect(near(o.y, objs[i].y, 1e-9)).toBe(true)
            expect(near(o.w, objs[i].w, 1e-9)).toBe(true)
            expect(near(o.h, objs[i].h, 1e-9)).toBe(true)
          })
        },
      ),
      propParams(1200),
    )
  })
})
