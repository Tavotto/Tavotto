import { defineConfig } from 'i18next-cli'

/**
 * i18next-cli 配置。它负责三件事：
 *
 *  1. **extract** —— 从源码里抽出直接写全 key 的调用，补进
 *     `src/i18n/locales/<locale>/<ns>.json`；CI 用 `--dry-run --ci`，
 *     源码里出现了资源文件里没有的 key 就非零退出。
 *  2. **types** —— 由主语言生成 `src/i18n/resources.d.ts`，`--ci` 检查是否过期。
 *  3. **lint** —— 扫源码里剩下的硬编码用户可见字符串。
 *
 * ## 它覆盖不到的那一半
 *
 * 本项目里大量调用点走的是**每个模块自己的短助手**：
 *
 *     const wb = (key: string, v?) => translate(`writeBack.${key}`, { ns: 'inspector', ...v })
 *     wb('confirm')      // → inspector:writeBack.confirm
 *
 * 这种写法把「命名空间 + 前缀」固定在一处，调用点只写最后一段——可读性好，
 * 但 key 是**运行时拼出来的**，静态提取器只看得到 `'confirm'` 那一截。
 * 因此这里：
 *
 *   * `functions` **只列第一个实参就是完整 key 的函数**（t / translate / msg …）；
 *     把助手也列进去反而会抽出一堆 `confirm`、`layout` 这样的半截 key，
 *     还会与真实的 `confirm.*` 撞成 nesting conflict。
 *   * `keepRemoved: true` —— 提取器看不见助手写的 key，绝不能让它把那些
 *     「没找到引用」的条目删掉。
 *
 * 「删掉多余 key」「助手拼出来的 key 有没有对应译文」这两件事交给
 * `scripts/i18n-check.mjs`：它自己解析助手声明，把前缀与命名空间还原回去，
 * 检查范围比 CLI 默认那套**更大**而不是更小。
 */
export default defineConfig({
  locales: ['zh-CN', 'en-US'],

  extract: {
    input: ['src/**/*.{ts,tsx}', '!src/**/*.test.{ts,tsx}', '!src/i18n/**'],
    output: 'src/i18n/locales/{{language}}/{{namespace}}.json',
    defaultNS: 'common',
    nsSeparator: ':',
    keySeparator: '.',
    // 主语言是中文：中文文案是原文，英文是译文
    primaryLanguage: 'zh-CN',
    // 新 key 不拿 key 本身当默认值——那样漏翻会被伪装成「已经有译文」。
    // 留空，由 i18n-check 的空值检查抓出来。
    defaultValue: '',
    // 见上：助手拼出来的 key 提取器看不见，不能让它当成「多余」删掉。
    // 「多余 key」的检查由 scripts/i18n-check.mjs 负责（它认得助手）。
    removeUnusedKeys: false,
    // 复数条目只留 _one/_other，不再额外生成一条无后缀的基键——两者并存时
    // i18next 的取用规则会变得难讲，也让 key 数量凭空翻倍
    generateBasePluralForms: false,
    functions: ['t', 'translate', 'i18n.t'],
    transKeepBasicHtmlNodesFor: ['br', 'strong', 'b', 'i', 'code'],
  },

  types: {
    input: ['src/i18n/locales/zh-CN/*.json'],
    output: 'src/i18n/resources.d.ts',
  },

  lint: {
    input: ['src/**/*.{ts,tsx}', '!src/**/*.test.{ts,tsx}', '!src/i18n/**'],
  },
})
