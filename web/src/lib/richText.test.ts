import { describe, expect, it } from 'vitest'
import {
  hasScripts,
  parseRuns,
  plainText,
  serializeRuns,
  toggleMathScript,
  toggleScript,
  transformCase,
} from './richText'

describe('parseRuns', () => {
  it('把 ^{…} / _{…} 解析成片段', () => {
    expect(parseRuns('cm^{-1}')).toEqual([
      { text: 'cm', script: '' },
      { text: '-1', script: 'sup' },
    ])
    expect(parseRuns('H_{2}O')).toEqual([
      { text: 'H', script: '' },
      { text: '2', script: 'sub' },
      { text: 'O', script: '' },
    ])
  })

  it('孤零零的 ^ 或 _ 原样保留', () => {
    // 存量文字不该因为升级突然变形——只有 `^{`/`_{` 才是标记
    expect(hasScripts('a^b _c 100%')).toBe(false)
    expect(plainText('a^b _c 100%')).toBe('a^b _c 100%')
  })

  it('没有配对的 } 就当字面量，绝不吞字符', () => {
    expect(plainText('x^{2')).toBe('x^{2')
  })

  it('转义 \\^ \\_ 变成字面量', () => {
    expect(parseRuns(String.raw`x\^{2}`)).toEqual([{ text: 'x^{2}', script: '' }])
  })

  it('serialize 对规范写法是逐字节的逆运算', () => {
    for (const s of ['cm^{-1}', 'H_{2}O', 'a^b', '100%', '10^{-3} mol', String.raw`x\^{2}`]) {
      expect(serializeRuns(parseRuns(s))).toBe(s)
    }
  })

  it('结尾的孤立反斜杠不加倍 —— 后面没有片段就不可能被误读', () => {
    // 粘一段 Windows 路径就够触发：以前每点一次「大小写」末尾就多一个 `\`
    for (const s of ['C:\\figs\\', '\\', 'a^{2} b\\']) {
      expect(serializeRuns(parseRuns(s))).toBe(s)
    }
    expect(transformCase('abc\\', 'upper')).toBe('ABC\\')
    // 反过来：后面**确实**还有片段时那个反斜杠仍要成对写出，否则拼接成
    // `\^{2}` 会被读成字面 `^{2}`，上标就没了
    expect(plainText(serializeRuns(parseRuns(String.raw`a\\^{2}`))))
      .toBe(plainText(String.raw`a\\^{2}`))
  })

  it('多余的转义会被规范化掉，但语义不变', () => {
    // `\^y` 里的反斜杠本来就没必要（^ 后面不是 `{`）；去掉它不改变任何显示，
    // 反过来无脑保留/添加反斜杠才会让用户的正文越点越脏
    const normalized = serializeRuns(parseRuns(String.raw`x\^y`))
    expect(normalized).toBe('x^y')
    expect(plainText(normalized)).toBe('x^y')
  })
})

describe('toggleScript', () => {
  it('给选区套上标记并把选区跟着挪', () => {
    const r = toggleScript('cm-1', 2, 4, 'sup')
    expect(r.text).toBe('cm^{-1}')
    expect(r.text.slice(r.start, r.end)).toBe('-1')
  })

  it('再点一次是取消（与加粗/斜体一致的切换语义）', () => {
    const on = toggleScript('cm-1', 2, 4, 'sup')
    const off = toggleScript(on.text, on.start, on.end, 'sup')
    expect(off.text).toBe('cm-1')
    expect(off.text.slice(off.start, off.end)).toBe('-1')
  })

  it('选中整段 ^{…} 时也能取消', () => {
    const r = toggleScript('cm^{-1}', 2, 7, 'sup')
    expect(r.text).toBe('cm-1')
  })

  it('没有选区就插入一对空标记，光标落在里面', () => {
    const r = toggleScript('cm', 2, 2, 'sub')
    expect(r.text).toBe('cm_{}')
    expect(r.start).toBe(r.end)
    expect(r.text.slice(0, r.start)).toBe('cm_{')
  })
})

describe('transformCase', () => {
  it('四种模式', () => {
    expect(transformCase('hello world', 'upper')).toBe('HELLO WORLD')
    expect(transformCase('HELLO', 'lower')).toBe('hello')
    expect(transformCase('hello world', 'title')).toBe('Hello World')
    expect(transformCase('one. two three', 'sentence')).toBe('One. Two three')
  })

  it('标记本身不受影响，只转片段内容', () => {
    // `^{-1}` 里的花括号不能被当成词参与首字母大写
    expect(transformCase('cm^{-1} per mol', 'upper')).toBe('CM^{-1} PER MOL')
    expect(transformCase('h_{2}o here', 'title')).toBe('H_{2}O Here')
  })

  it('CJK 不受影响', () => {
    expect(transformCase('波长 nm', 'upper')).toBe('波长 NM')
  })
})

describe('toggleMathScript（图内元素走 matplotlib mathtext）', () => {
  it('包成 $^{…}$ / $_{…}$', () => {
    expect(toggleMathScript('cm-1', 2, 4, 'sup').text).toBe('cm$^{-1}$')
    expect(toggleMathScript('H2O', 1, 2, 'sub').text).toBe('H$_{2}$O')
  })

  it('再点一次取消', () => {
    const on = toggleMathScript('cm-1', 2, 4, 'sup')
    expect(toggleMathScript(on.text, on.start, on.end, 'sup').text).toBe('cm-1')
  })
})

describe('transformCase 的公式保护', () => {
  it('$…$ 里的 matplotlib 命令原样不动', () => {
    // \alpha 被改成 \ALPHA 就直接把公式弄坏了
    expect(transformCase(String.raw`peak $\alpha$ shift`, 'upper', true)).toBe(
      String.raw`PEAK $\alpha$ SHIFT`,
    )
    expect(transformCase('cm$^{-1}$ value', 'upper', true)).toBe('CM$^{-1}$ VALUE')
  })

  it('不开保护时（画布标注）照常全转', () => {
    expect(transformCase('cm^{-1} value', 'upper')).toBe('CM^{-1} VALUE')
  })
})
