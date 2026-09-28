/**
 * i18n-check 第 7 组用的：一个插值实参的源码文本 → 它**顶层**传了哪些变量。
 *
 * 用 TypeScript 编译器 API 解析（与 `import-graph.mjs` 同一个依赖），不用正则：
 * 正则分不清层级，`{ meta: { product: x }, name }` 会把嵌套的 `product` 当成传了
 * （i18next 只认顶层键，`{{product}}` 照样露出来，Codex #695）。
 *
 * 返回 `null` = 看不全、不判：实参是变量 / 函数调用 / 三元，对象里有展开或计算键。
 */
import ts from 'typescript'

/** @param {string | undefined} text @returns {Set<string> | null} */
export function topLevelKeys(text) {
  const src = (text ?? '').trim()
  if (!src) return new Set()
  const sf = ts.createSourceFile('args.ts', `(${src})`, ts.ScriptTarget.Latest, false, ts.ScriptKind.TSX)
  const stmt = sf.statements[0]
  if (sf.statements.length !== 1 || !stmt || !ts.isExpressionStatement(stmt)) return null
  let expr = stmt.expression
  while (ts.isParenthesizedExpression(expr) || ts.isAsExpression(expr) || ts.isSatisfiesExpression(expr)) {
    expr = expr.expression
  }
  if (!ts.isObjectLiteralExpression(expr)) return null
  const keys = new Set()
  for (const prop of expr.properties) {
    if (ts.isSpreadAssignment(prop)) return null
    const name = prop.name
    if (!name || ts.isComputedPropertyName(name)) return null
    if (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name)) keys.add(name.text)
    else return null
  }
  return keys
}
