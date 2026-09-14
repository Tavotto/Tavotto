/**
 * matplotlib mathtext → 可读文本（`$\mathrm{min^{-1}}$` → `min⁻¹`）。
 *
 * 2026-09-13 审计 B48 先只给对象头 / 面包屑用；2026-09-14 审计 A3 量到图例项列表、
 * 元素树、问题面板、导出清单、上下文栏都还显示原始源码——同一个名字五种写法。
 * 现在挂在 `engineLabel` 的出口上，凡是经它出来的名字都可读；「名称 / 内容」输入框
 * 里仍是原始源码，用户改的是那一份。认不出的命令原样保留、`$` 不成对整段原样返回：
 * 宁可露出源码，不许改掉用户的字。
 */

const SUP: Record<string, string> = {
  '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
  '+': '⁺', '-': '⁻', '−': '⁻', '(': '⁽', ')': '⁾', '=': '⁼', n: 'ⁿ', i: 'ⁱ',
}
const SUB: Record<string, string> = {
  '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
  '+': '₊', '-': '₋', '−': '₋', '(': '₍', ')': '₎', '=': '₌',
}
const GREEK: Record<string, string> = {
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε', varepsilon: 'ε', zeta: 'ζ', eta: 'η',
  theta: 'θ', vartheta: 'ϑ', iota: 'ι', kappa: 'κ', lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ', pi: 'π',
  rho: 'ρ', sigma: 'σ', tau: 'τ', upsilon: 'υ', phi: 'φ', varphi: 'ϕ', chi: 'χ', psi: 'ψ', omega: 'ω',
  Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π', Sigma: 'Σ', Phi: 'Φ', Psi: 'Ψ',
  Omega: 'Ω', times: '×', pm: '±', mp: '∓', cdot: '·', degree: '°', circ: '°', infty: '∞', leq: '≤',
  geq: '≥', neq: '≠', approx: '≈', sim: '∼', rightarrow: '→', leftarrow: '←', 'to': '→', prime: '′',
  percent: '%',
}
const FONT_CMD = /\\(?:mathrm|mathit|mathbf|mathsf|mathtt|text|textrm|textit|textbf|mathcal)\{([^{}]*)\}/

/** 一段上 / 下标能不能整段换成 Unicode 字符（换不了就原样留着，绝不丢字） */
const scripted = (body: string, table: Record<string, string>): string | null => {
  const out = [...body].map((c) => table[c])
  return out.every((c) => c !== undefined) ? out.join('') : null
}

/** 一段 `$…$` 里的内容 */
function plainMath(src: string): string {
  let s = src
  // 上 / 下标先换：`^{-1}` / `^2` / `_{2}` / `_3`；换不成字符的原样保留（含花括号）。
  // 先于字体命令处理，`\mathrm{min^{-1}}` 里层的花括号才会先消掉
  s = s.replace(/([\^_])(?:\{([^{}]*)\}|([^\s{}\\]))/g, (m, op: string, braced?: string, single?: string) => {
    const body = braced ?? single ?? ''
    return scripted(body, op === '^' ? SUP : SUB) ?? m
  })
  // 字体命令只是排版指令，内容才是字（嵌套时从里往外剥，每轮剥一层）
  for (let i = 0; i < 4 && FONT_CMD.test(s); i++) s = s.replace(new RegExp(FONT_CMD.source, 'g'), '$1')
  // 希腊字母与常见符号（控制词后面那个空格是分隔符，不是字，一并吃掉）；
  // 其余 `\命令` 原样留着——不认识的东西不装懂
  s = s.replace(/\\([A-Za-z]+) ?/g, (m, name: string) => GREEK[name] ?? m)
  // 数学空白
  s = s.replace(/\\[,;:!]/g, ' ').replace(/\\ /g, ' ').replace(/\\%/g, '%')
  return s
}

/**
 * 标题显示用：把 matplotlib mathtext（`$\mathrm{min^{-1}}$`）换成可读文本
 * （`min⁻¹`）。**只给对象头 / 面包屑用**——「名称」输入框里仍是原始源码，用户改的
 * 是那一份（2026-09-13 审计 B48：对象头暴露数学源码，读起来像配置面板）。
 * 认不出的命令原样保留、`$` 不成对整段原样返回：宁可露出源码，不许改掉用户的字。
 */
export function displayLabel(label: string): string {
  if (!label.includes('$')) return label
  const parts = label.split(/(?<!\\)\$/)
  if (parts.length % 2 === 0) return label // `$` 不成对
  return parts.map((seg, i) => (i % 2 === 1 ? plainMath(seg) : seg)).join('')
}
