/**
 * 元素行名字拆成「类型词 + 名字」（2026-10-16 UI 2.0 第二轮 04：类型词降为 ink-3 前缀，名字为主体）。
 * 引擎的行名是 `类型词 “文字”`（中文 `标题 “Reaction kinetics”`、`X 轴 “…”`、`曲线 “…”`；英文同形）：
 * 在第一个「空白 + 引号」处切开。没有引号的名字（`子图 1`、`整张图`、刻度数字）整个是名字，没有前缀。
 * 前缀 + 名字拼回去恒等于原串（含那个空格），所以截断、title、可达名都不受影响。
 */
export function splitTypePrefix(label: string): { prefix: string; name: string } {
  const m = /^(.+?)(\s+)(?=[“"‘'])/s.exec(label)
  if (!m || m[1].length > 24) return { prefix: '', name: label }
  const prefix = m[1] + m[2]
  return { prefix, name: label.slice(prefix.length) }
}
