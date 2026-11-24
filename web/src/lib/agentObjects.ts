/**
 * Agent 新增的画布对象（ADR 0208 §五）：字段表在 `src/tavotto/profiles/agent_objects.json`（唯一出处，
 * 后端 `engine/agentobjects.py` 校验、生成 id、转导出载荷都读它）。
 *
 * 这里只放**逐键覆盖表**：每个对象接口的每个键都要标一个去向——
 * - `accept`：Agent 可以写，字段表里有它（`agentObjects.golden.test.ts` 核两边的集合相同）；
 * - `withhold`：不给 Agent（锁定、隐藏、剪切蒙版、原图字体……；`groupId` 由 `groupPath` 生成）；
 * - `reserved`：字段表保留、还没开放。
 * 映射类型要求键一个不少、一个不多：`types/document.ts` 加了字段而这里没标，tsc 直接红——
 * 不靠人记得回来改。
 */
import table from '@agentObjects'
import type { ArrowObject, ObjectBase, PathObject, ShapeObject, TextObject } from '@/types/document'

export type FieldFate = 'accept' | 'withhold' | 'reserved'
type Coverage<T> = { [K in keyof Required<T>]: FieldFate }

const BASE: Coverage<ObjectBase> = {
  id: 'accept',
  type: 'accept',
  x: 'accept',
  y: 'accept',
  w: 'accept',
  h: 'accept',
  locked: 'withhold',
  hidden: 'withhold',
  name: 'accept',
  groupId: 'withhold',
  groupPath: 'accept',
  clipMask: 'withhold',
  clipOff: 'withhold',
  layoutPinned: 'withhold',
  rotationDeg: 'accept',
}

const TEXT: Coverage<Omit<TextObject, keyof ObjectBase>> = {
  text: 'accept',
  sizePt: 'accept',
  fontFamily: 'accept',
  sourceFont: 'withhold',
  writingMode: 'withhold',
  interpretation: 'accept',
  textFormat: 'accept',
  bold: 'accept',
  italic: 'accept',
  color: 'accept',
  align: 'accept',
  underline: 'accept',
  lineHeight: 'accept',
  padding: 'accept',
  bg: 'accept',
  borderColor: 'accept',
  borderPt: 'accept',
}

const ARROW: Coverage<Omit<ArrowObject, keyof ObjectBase>> = {
  start: 'accept',
  end: 'accept',
  strokePt: 'accept',
  color: 'accept',
  head: 'accept',
  headStart: 'accept',
  headEnd: 'accept',
  headSizeStart: 'accept',
  headSizeEnd: 'accept',
  dash: 'accept',
  strokeOpacity: 'accept',
  lineCap: 'accept',
  lineJoin: 'accept',
  miterLimit: 'accept',
  dashPattern: 'accept',
}

const SHAPE: Coverage<Omit<ShapeObject, keyof ObjectBase>> = {
  shape: 'accept',
  strokePt: 'accept',
  color: 'accept',
  fill: 'accept',
  start: 'accept',
  end: 'accept',
  cornerRadius: 'accept',
  sides: 'accept',
  starRatio: 'accept',
  fillOpacity: 'accept',
  dash: 'accept',
  stroke: 'accept',
  strokeOpacity: 'accept',
  lineCap: 'accept',
  lineJoin: 'accept',
  miterLimit: 'accept',
  dashPattern: 'accept',
  fillGradient: 'accept',
  strokeGradient: 'accept',
  strokeAlign: 'accept',
}

const PATH: Coverage<Omit<PathObject, keyof ObjectBase>> = {
  subpaths: 'accept',
  fillRule: 'accept',
  strokePt: 'accept',
  color: 'accept',
  fill: 'accept',
  fillOpacity: 'accept',
  stroke: 'accept',
  strokeOpacity: 'accept',
  dash: 'accept',
  lineCap: 'accept',
  lineJoin: 'accept',
  miterLimit: 'accept',
  dashPattern: 'accept',
  fillGradient: 'accept',
  strokeGradient: 'accept',
  strokeAlign: 'accept',
  headStart: 'accept',
  headEnd: 'accept',
  headSizeStart: 'accept',
  headSizeEnd: 'accept',
}

/** Agent 能新增的对象类型 → 它的逐键覆盖（含 `ObjectBase` 那部分） */
export const AGENT_OBJECT_COVERAGE: Readonly<Record<'text' | 'arrow' | 'shape' | 'path', Record<string, FieldFate>>> = {
  text: { ...BASE, ...TEXT },
  arrow: { ...BASE, ...ARROW },
  shape: { ...BASE, ...SHAPE },
  path: { ...BASE, ...PATH },
}

/** 字段表（只读）：给需要列字段的地方用，不在 TS 侧另抄一份 */
export const AGENT_OBJECT_TABLE = table
