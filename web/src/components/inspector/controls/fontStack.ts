/**
 * matplotlib 字体族选项 → 预览用的 CSS 字体栈。
 * 只影响**选项文字自身的显示字体**（Aa 预览），写入引擎的值仍是原始选项串；
 * 引擎侧的真实字体解析归 matplotlib，两边不承诺像素等价。
 */
export function fontStackOf(option: string): string {
  switch (option) {
    case 'serif':
      return 'Georgia, "Times New Roman", serif'
    case 'sans-serif':
      return 'Helvetica, Arial, sans-serif'
    case 'monospace':
      return 'ui-monospace, "SF Mono", Menlo, monospace'
    default:
      // 具体字体名（Times New Roman / Arial…）：本机装了就预览，没装回退 serif
      return `"${option}", serif`
  }
}
