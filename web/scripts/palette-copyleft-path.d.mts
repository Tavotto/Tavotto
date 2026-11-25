/** `palette-copyleft-path.mjs` 的类型声明（`tsconfig.app.json` 没开 allowJs）。改了导出记得同步这里。 */
export const NO_COPYLEFT_ENV: string
export const COPYLEFT_FILE_SUFFIX: string
export function copyleftExcluded(env?: Record<string, string | undefined>): boolean
export function isCopyleftCatalog(id: string): boolean
export function noCopyleftPalettes(env?: Record<string, string | undefined>): {
  name: string
  enforce: 'pre'
  load(id: string): string | null
}
