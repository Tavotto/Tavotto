/**
 * `number_list` 字段（固定刻度位置）的文本 ↔ 数组换算。
 *
 * 分隔符收得宽（逗号、中文逗号、分号、空白）：用户多半是从别处粘一串数
 * 进来的，为了一个全角逗号让他重打一遍不值得。解不出数的段落丢掉，能用的
 * 留下——整串作废的话，粘进来带单位的数据就一个都存不下。
 */
export function parseNumberList(text: string): number[] {
  return text
    .split(/[,;，；\s]+/)
    .filter((s) => s.length > 0)
    .map((s) => Number(s))
    .filter((n) => Number.isFinite(n))
}

/** 数组 → 输入框里的文本（与 parseNumberList 互逆） */
export function formatNumberList(values: readonly number[]): string {
  return values.join(', ')
}
