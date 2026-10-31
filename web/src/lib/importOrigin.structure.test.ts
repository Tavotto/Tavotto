/**
 * 前端没有解析逻辑（Import Origin PR6，沿用 `store/envAdvice.test.ts` 的口径：前端只**保存与转发**后端的事实，不自写判据）。
 *
 * 这是一条**结构**门禁：Import Origin 在前端的几个文件里，不许出现 import 解析 / 包名映射 / 环境判据的任何痕迹——
 * 没有标准库 / 第三方 / curated 名单、没有对 import 名的字符串切分与正则、没有 `importlib` / `site-packages` 的概念、没有包名表。
 * 「这个 import 是谁提供的、该装哪个包、能不能装」只有后端一个出处（`importscan` / `depresolve` / `depplan`）。
 *
 * 读文件走 `import.meta.glob('?raw')`（与 `components/ui/nativeSelect.test.ts` 同一手法）。注释里可以讲这些概念（正是在解释为什么不做），
 * 所以先剥注释再扫。
 */
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', { eager: true, query: '?raw', import: 'default' }) as Record<string, string>

const stripComments = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[\s;{}()])\/\/.*$/gm, '$1')

/** Import Origin 在前端的全部文件（新增文件要加进来；`ORIGIN_FILES_COVER_THE_FEATURE` 钉着没漏） */
const ORIGIN_FILES = [
  '/src/lib/importOrigin.ts',
  '/src/components/ImportOriginDialog.tsx',
  '/src/components/OriginDetails.tsx',
]

const code = (path: string) => stripComments(SOURCES[path] ?? '')

describe('Import Origin 的前端文件里没有解析逻辑', () => {
  it('文件都在（路径写错会让下面的扫描空转）', () => {
    for (const f of ORIGIN_FILES) expect(SOURCES[f], f).toBeTruthy()
  })

  it('没有判据词：importlib / site-packages / stdlib / curated / 标准库 / 包名表 / normalize', () => {
    for (const f of ORIGIN_FILES) {
      expect(code(f), f).not.toMatch(/importlib|site-?packages|stdlib|STDLIB|curated|CURATED|ALTERNATIVE|SAME_NAME|normalize|PyPI/)
    }
  })

  it('没有对 import 名的字符串切分 / 正则 / 大小写折叠', () => {
    for (const f of ORIGIN_FILES) {
      const src = code(f)
      expect(src, f).not.toMatch(/new RegExp|\.split\(['"`][._-]|\.toLowerCase\(|\.toUpperCase\(|\.localeCompare\(/)
      // 正则字面量只许出现在 import_name 之外的地方：这些文件里根本没有 `/…/.test(` 这种写法
      expect(src, f).not.toMatch(/\/[^/\n]+\/[a-z]*\.(test|exec)\(/)
      expect(src, f).not.toMatch(/\.match\(\s*\//)
    }
  })

  it('没有任何具体的库名 / 包名字面量（预填只来自后端的 suggested_distribution）', () => {
    for (const f of ORIGIN_FILES) {
      expect(code(f), f).not.toMatch(/['"`](numpy|scipy|pandas|matplotlib|cv2|opencv|yaml|PIL|pillow|sklearn|scikit|torch|docx|python-docx|ROOT|tkinter)\b/i)
    }
  })

  it('没有用 import 名拼包名 / 拼 requirement 的代码（提交体里的值只来自用户输入或后端预填）', () => {
    const src = code('/src/lib/importOrigin.ts')
    expect(src).not.toMatch(/import_name\s*\+|`\$\{[^}]*import_name[^}]*\}[-=<>]|replace\(/)
    // 预填的唯一来源：suggested_distribution，且不经改写
    expect(src).toMatch(/suggested_distribution \?\? ''/)
  })

  it('不在前端自己判「能不能装 / 能不能指定」：没有对 reason / resolution_status / bucket 的比较', () => {
    for (const f of ORIGIN_FILES) {
      expect(code(f), f).not.toMatch(/\.(reason|resolution_status|distribution_status|origin_kind|provenance|bucket)\s*[!=]==?\s*['"]/)
    }
  })

  it('这个功能没有第二个入口读 origins：除了三个文件和已登记的消费者，没有别的文件碰 origins[] / suggested_distribution', () => {
    const allowed = new Set([
      ...ORIGIN_FILES,
      '/src/lib/api.ts', // 类型镜像
      '/src/lib/preparationText.ts', // 经 originFactsOf 读计数
      '/src/components/DependencyPrepareDialog.tsx', // unknown 区、备选提示
      '/src/components/PreparationCard.tsx', // 详情里的原因计数
      '/src/store/depRepairStore.ts', // 草稿 / 提交 / 确认
    ])
    const readers = Object.keys(SOURCES).filter(
      (f) => !/\.test\.tsx?$/.test(f) && /suggested_distribution|\.origins\b/.test(stripComments(SOURCES[f])),
    )
    expect(readers.filter((f) => !allowed.has(f))).toEqual([])
  })
})
