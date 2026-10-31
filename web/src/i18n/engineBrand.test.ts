/**
 * `errors.engine.*` 的文案不手写品牌名：写 `{{product}}`，调用处传 `PRODUCT_NAME`（`lib/brand.ts`，根 AGENTS.md）。
 * 与 `prBrand.test.ts` 的区别：那份只钉 #742 碰过的键，这份覆盖 `engine` 下**全部**叶子、两个语种。
 * 渲染出的文字确实带品牌常量的值，由各生成点所在组件的用例断言（EngineEnvironmentCard / DependencyRepairCard /
 * DependencyPrepareDialog / MissingInputDialog 的 test）；这里管资源本身。
 */
import { describe, expect, it } from 'vitest'
import { resources } from '@/i18n'

type Tree = { [k: string]: string | Tree }
function leaves(tree: Tree, prefix = ''): [string, string][] {
  return Object.entries(tree).flatMap(([k, v]) => {
    const path = prefix ? `${prefix}.${k}` : k
    return typeof v === 'string' ? [[path, v] as [string, string]] : leaves(v, path)
  })
}

// 环境变量名（TAVOTTO_WORKER_PYTHON）是真实存在的变量，不是品牌文案；只认首字母大写的产品名写法
const PRODUCT_WORD = /Tavotto/

describe.each(Object.keys(resources))('errors.engine.* 不手写产品名（%s）', (lang) => {
  const engine = (resources as unknown as Record<string, { errors: { engine: Tree } }>)[lang].errors.engine
  it('没有任何叶子手写 Tavotto', () => {
    const all = leaves(engine)
    expect(all.length).toBeGreaterThan(300) // 取不到资源时这条判据不能恒绿
    expect(all.filter(([, v]) => PRODUCT_WORD.test(v)).map(([k]) => k)).toEqual([])
  })

  it('固定解除：一句话版与完整版一致，说“取消固定”，不要求“恢复自动检测”', () => {
    const e = engine as unknown as { repairErrorShort: Record<string, string>; repairPinnedBody: string }
    const short = e.repairErrorShort.dependency_interpreter_pinned
    expect(short).toMatch(lang === 'zh-CN' ? /取消固定/ : /remove the pin/)
    expect(short).not.toMatch(/恢复自动检测|restore automatic detection/i)
  })
})
