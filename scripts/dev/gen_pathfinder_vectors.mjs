// 生成 tests/golden/pathfinder_vectors.json：路径查找器内核（web/src/lib/vecops/vecops.wasm）对几组固定输入的输出。
// 前端用例 web/src/lib/pathfinder.test.ts 逐数比对——重建 .wasm（换上游提交、换工具链）后结果变了就红，
// 要么是有意的（重跑本脚本、在 PR 里写明为什么变），要么是回归。
//   node scripts/dev/gen_pathfinder_vectors.mjs
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const { instance } = await WebAssembly.instantiate(readFileSync(root + 'web/src/lib/vecops/vecops.wasm'), {})
const x = instance.exports
const K = 0.5522847498

const circle = (cx, cy, r) => {
  const f = [1, 1, 4]
  for (const [px, py, tx, ty] of [[cx + r, cy, 0, 1], [cx, cy + r, -1, 0], [cx - r, cy, 0, -1], [cx, cy - r, 1, 0]])
    f.push(px, py, px - tx * r * K, py - ty * r * K, px + tx * r * K, py + ty * r * K, 1)
  return f
}
const rect = (x0, y0, w, h) => {
  const f = [1, 1, 4]
  for (const [px, py] of [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]]) f.push(px, py, px, py, px, py, 0)
  return f
}
const stack = (...items) => {
  const o = [items.length]
  items.forEach(([eo, p], i) => o.push(eo ? 1 : 0, i, ...p))
  return o
}
const run = (op, input) => {
  const p = x.alloc(input.length)
  new Float64Array(x.memory.buffer, p, input.length).set(input)
  const n = x.pathfinder(op, p, input.length)
  const out = Array.from(new Float64Array(x.memory.buffer, x.out_ptr(), n))
  x.dealloc(p, input.length)
  return out
}

const twoCircles = stack([false, circle(28, 28, 28)], [false, circle(62, 28, 28)])
const ring = stack([false, rect(0, 0, 100, 100)], [false, circle(50, 50, 28)])
const nested = [2, 1, 4, ...rect(0, 0, 80, 80).slice(3), 1, 4, ...rect(20, 20, 40, 40).slice(3)]
const cases = [
  ['两圆 合并', 0, twoCircles],
  ['两圆 减去顶层', 1, twoCircles],
  ['两圆 相交', 2, twoCircles],
  ['两圆 排除重叠', 3, twoCircles],
  ['两圆 分割', 4, twoCircles],
  ['两圆 减去后方', 8, twoCircles],
  ['方框减去圆（带洞）', 1, ring],
  ['同向嵌套两框 奇偶 合并（中间成洞）', 0, stack([true, nested])],
  ['同向嵌套两框 非零 合并（实心）', 0, stack([false, nested])],
].map(([name, op, input]) => ({ name, op, input, output: run(op, input) }))

writeFileSync(
  root + 'tests/golden/pathfinder_vectors.json',
  JSON.stringify({ note: '由 scripts/dev/gen_pathfinder_vectors.mjs 生成；坐标单位 pt；编码见 vecops/src/lib.rs', cases }, null, 1) + '\n',
)
console.log(cases.map((c) => `${c.name}: ${c.output.length}`).join('\n'))
