/**
 * i18n-check 第 8 组用的：一份 JSON 文本里，**同一个对象里出现了两次的键**（点分路径）。
 *
 * `JSON.parse` 遇到重复键不报错、后一个悄悄盖掉前一个——git 自动合并把一整段（例如
 * `dataFigureV2.spec`）重复了一份时不报冲突，别的检查也都照常通过，文案却可能悄悄回到旧的
 * （2026-11-24 #2156 并 main 时真的发生过）。所以这里不靠 `JSON.parse`，自己走一遍文本。
 *
 * 只认合法 JSON（资源文件先过了 `JSON.parse`）；数组里的对象路径带下标。
 */

/** @param {string} text @returns {string[]} 重复键的点分路径（按出现顺序，每条只报一次） */
export function duplicateKeys(text) {
  const out = []
  const seen = new Set()
  let i = 0
  const ws = () => {
    while (i < text.length && /\s/.test(text[i])) i++
  }
  const string = () => {
    // text[i] === '"'
    let j = i + 1
    while (j < text.length && text[j] !== '"') j += text[j] === '\\' ? 2 : 1
    const raw = text.slice(i, j + 1)
    i = j + 1
    return JSON.parse(raw)
  }
  const value = (at) => {
    ws()
    const c = text[i]
    if (c === '{') {
      i++
      const keys = new Set()
      ws()
      if (text[i] === '}') {
        i++
        return
      }
      for (;;) {
        ws()
        const key = string()
        const here = at ? `${at}.${key}` : key
        if (keys.has(key) && !seen.has(here)) {
          seen.add(here)
          out.push(here)
        }
        keys.add(key)
        ws()
        i++ // ':'
        value(here)
        ws()
        if (text[i] === ',') {
          i++
          continue
        }
        i++ // '}'
        return
      }
    }
    if (c === '[') {
      i++
      ws()
      if (text[i] === ']') {
        i++
        return
      }
      for (let n = 0; ; n++) {
        value(`${at}[${n}]`)
        ws()
        if (text[i] === ',') {
          i++
          continue
        }
        i++ // ']'
        return
      }
    }
    if (c === '"') {
      string()
      return
    }
    while (i < text.length && !/[,\]}\s]/.test(text[i])) i++ // 数 / true / false / null
  }
  value('')
  return out
}
