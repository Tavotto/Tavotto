import { describe, expect, it } from 'vitest'
import { parsePayload } from './clipboard'
import { CLIPBOARD_FORMAT } from './brand'

const objects = [
  { id: 'o1', type: 'text', x: 0, y: 0, w: 20, h: 8, text: 'hi',
    sizePt: 9, bold: false, color: '#000', align: 'left' },
]

const payload = (magic: string) =>
  JSON.stringify({ magic, sourceDocId: 'd1', objects, layoutGroups: [] })

describe('剪贴板负载魔数兼容', () => {
  it('接受新魔数 tavotto/objects@1', () => {
    const p = parsePayload(payload(CLIPBOARD_FORMAT))
    expect(p?.objects).toHaveLength(1)
  })

  it('不再认改名前的魔数（干净断裂，见 lib/brand.ts）', () => {
    expect(parsePayload(payload('magplot/objects@1'))).toBeNull()
    expect(parsePayload(payload('magic-matplot/objects@1'))).toBeNull()
  })

  it('拒绝陌生负载与普通文本', () => {
    expect(parsePayload(payload('someone-else/objects@1'))).toBeNull()
    expect(parsePayload('随便一段文字')).toBeNull()
    expect(parsePayload('{"magic":"tavotto/objects@1"}')).toBeNull() // 缺 objects
  })
})
