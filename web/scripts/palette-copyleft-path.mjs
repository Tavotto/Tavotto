// GPL 期刊风格色板的排除开关（ADR 0210 §三）。四份 vite / vitest 配置都挂这个插件。
//
// `@palettesCopyleft` 别名在配置里写死指向 src/tavotto/profiles/palettes_copyleft.json（别名目标必须是字面量：
// `pagePtLens.test.ts` 的 import 流判据要静态读出它）。构建环境设 TAVOTTO_NO_COPYLEFT_PALETTES=1
// （与引擎 engine/palettes.py 同名开关）时，这个插件在**加载**那份文件时换成空表：GPL 的色值整份不进产物，
// 闭源高级版组件（ADR 0198）只拿得到许可宽松的那份。
import { readFileSync } from 'node:fs'

export const NO_COPYLEFT_ENV = 'TAVOTTO_NO_COPYLEFT_PALETTES'
export const COPYLEFT_FILE_SUFFIX = '/src/tavotto/profiles/palettes_copyleft.json'

export function copyleftExcluded(env = process.env) {
  return String(env[NO_COPYLEFT_ENV] ?? '').trim() === '1'
}

/** 加载的是不是那份 GPL 目录（去掉查询串、统一成 `/`） */
export function isCopyleftCatalog(id) {
  return String(id).split('?')[0].replace(/\\/g, '/').endsWith(COPYLEFT_FILE_SUFFIX)
}

export function noCopyleftPalettes(env = process.env) {
  return {
    name: 'no-copyleft-palettes',
    enforce: 'pre',
    load(id) {
      if (!copyleftExcluded(env) || !isCopyleftCatalog(id)) return null
      return readFileSync(new URL('../src/lib/palette/copyleftEmpty.json', import.meta.url), 'utf8')
    },
  }
}
