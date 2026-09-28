/**
 * `i18n-args.mjs` 的类型声明——只为 `src/i18n/i18nArgs.test.ts` 能过 `tsc -b`
 * （`tsconfig.app.json` 没开 allowJs）。实现在 .mjs 里；改了导出记得同步这里。
 */
export function topLevelKeys(text: string | undefined): Set<string> | null
