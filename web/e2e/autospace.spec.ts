/**
 * 中西文自动留白（`text-autospace`）只给散文 / 标签角色类开，字面数据不开（Codex P2 #1099）。
 *
 * 脚本名 / 路径 / 输入框里的值显示的字符必须与复制出来的一致：「图A.py」不能显示成「图 A.py」，
 * 也不能改变截断位置。拿真产物的 CSS 量 computed 值与真实渲染宽度（两者都要：值错了或被覆盖都会红）。
 */
import { expect, test } from './fixtures'

test('text-autospace：散文角色开、字面数据（输入框 / 等宽脚本名 / 代码）不开，宽度也对得上', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.locator('#root').waitFor()
  const r = await page.evaluate(() => {
    const host = document.createElement('div')
    // 等宽脚本名坐在一个开着 autospace 的散文祖先里：兜底必须压过继承
    host.innerHTML = `
      <p id="prose" class="type-reading">图A.py 变量x</p>
      <p id="cap" class="type-caption">图A.py 变量x</p>
      <div class="type-body"><span id="mono" class="font-mono type-body">图A.py</span></div>
      <input id="inp" class="type-body" value="图A.py" />
      <textarea id="ta" class="type-body">图A.py</textarea>
      <pre id="pre" class="type-body">图A.py</pre>
      <span id="plain" class="text-sm">图A.py</span>`
    document.body.appendChild(host)
    const v = (id: string) => getComputedStyle(host.querySelector('#' + id)!).textAutospace
    const w = (cls: string, auto: string) => {
      const s = document.createElement('span')
      s.className = cls
      s.style.cssText = `text-autospace:${auto};position:absolute;visibility:hidden;white-space:nowrap`
      s.textContent = '图A.py'
      document.body.appendChild(s)
      const x = s.getBoundingClientRect().width
      s.remove()
      return x
    }
    const out = {
      prose: v('prose'), cap: v('cap'), mono: v('mono'), inp: v('inp'), ta: v('ta'), pre: v('pre'), plain: v('plain'),
      // 真实渲染宽度：mono 的实际宽度 == 强制 no-autospace 的宽度
      monoW: host.querySelector<HTMLElement>('#mono')!.getBoundingClientRect().width,
      monoNoAutoW: w('font-mono type-body', 'no-autospace'),
      monoAutoW: w('font-mono type-body', 'normal'),
    }
    host.remove()
    return out
  })
  expect(r.prose, '散文 type-reading').toBe('normal')
  expect(r.cap, '说明 type-caption').toBe('normal')
  for (const k of ['mono', 'inp', 'ta', 'pre'] as const) expect(r[k], `字面数据 ${k}`).toBe('no-autospace')
  expect(r.plain, '没挂角色类的 text-sm 不开').toBe('no-autospace')
  expect(r.monoW).toBeCloseTo(r.monoNoAutoW, 1)
  // 反向：autospace 开着时这串字确实会变宽——否则上面的宽度比较是空的
  expect(r.monoAutoW).toBeGreaterThan(r.monoNoAutoW)
})
