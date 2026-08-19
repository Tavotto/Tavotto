/**
 * 语言标签的规范化、优先级与持久化。
 *
 * 这一层的错误全都是「用户看到的界面语言不是他要的那个」，而且不报错、
 * 不留日志——所以每一档回退都单独钉一条。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DEFAULT_LOCALE,
  LOCALE_LABELS,
  LOCALE_STORAGE_KEY,
  SUPPORTED_LOCALES,
  normalizeLocale,
  readStoredLocale,
  writeStoredLocale,
} from './locale'

/** 换系统语言：locale.ts 每次都现读 navigator，不需要 resetModules */
const asSystem = (...tags: string[]) => {
  Object.defineProperty(navigator, 'languages', { value: tags, configurable: true })
  Object.defineProperty(navigator, 'language', { value: tags[0], configurable: true })
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  localStorage.clear()
})

describe('normalizeLocale', () => {
  it('中文的各种写法统统归到 zh-CN', () => {
    for (const tag of ['zh', 'zh-CN', 'zh-Hans', 'zh-Hans-CN', 'zh-SG', 'ZH_cn', ' zh-cn ']) {
      expect(normalizeLocale(tag)).toBe('zh-CN')
    }
  })

  it('繁体暂时也落到 zh-CN：中文界面比英文界面离它更近', () => {
    for (const tag of ['zh-Hant', 'zh-TW', 'zh-HK', 'zh-MO']) {
      expect(normalizeLocale(tag)).toBe('zh-CN')
    }
  })

  it('所有 en-* 归到 en-US', () => {
    for (const tag of ['en', 'en-US', 'en-GB', 'en-AU', 'EN_us']) {
      expect(normalizeLocale(tag)).toBe('en-US')
    }
  })

  it('认不出来回 null——「认不出来」和「明确选了中文」必须分得开', () => {
    for (const tag of ['ja', 'fr-FR', 'de', 'xx', '', '   ', null, undefined]) {
      expect(normalizeLocale(tag)).toBeNull()
    }
  })
})

describe('优先级：手动 > 系统 > zh-CN', () => {
  /**
   * detectLocale 在模块求值时不缓存任何东西，但它读的 readStoredLocale /
   * systemLocale 都是现读，所以这里直接调即可。
   */
  const detect = async () => (await import('./locale')).detectLocale()

  it('没选过 + 系统是中文 → zh-CN', async () => {
    asSystem('zh-CN')
    expect(await detect()).toBe('zh-CN')
  })

  it('没选过 + 系统是英文 → en-US', async () => {
    asSystem('en-US')
    expect(await detect()).toBe('en-US')
  })

  it('没选过 + 系统是不支持的语言 → 兜底 zh-CN', async () => {
    asSystem('ja-JP', 'ko-KR')
    expect(await detect()).toBe(DEFAULT_LOCALE)
    expect(DEFAULT_LOCALE).toBe('zh-CN')
  })

  it('navigator.languages 里第一个不支持的被跳过，取第一个认得的', async () => {
    asSystem('ja-JP', 'en-GB', 'zh-CN')
    expect(await detect()).toBe('en-US')
  })

  it('手动选择压过系统语言', async () => {
    asSystem('en-US')
    writeStoredLocale('zh-CN')
    expect(await detect()).toBe('zh-CN')

    writeStoredLocale('en-US')
    asSystem('zh-CN')
    expect(await detect()).toBe('en-US')
  })
})

describe('持久化', () => {
  it('偏好存在独立的 tavotto.locale 里，不碰文档/项目数据', () => {
    writeStoredLocale('en-US')
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US')
    expect(LOCALE_STORAGE_KEY).toBe('tavotto.locale')
    // 只多这一个键，别的什么都没写
    expect(Object.keys(localStorage)).toEqual([LOCALE_STORAGE_KEY])
  })

  it('手动选择后「刷新」仍保持：新一轮读取拿到的还是它', async () => {
    asSystem('zh-CN') // 系统是中文，故意与选择相反
    writeStoredLocale('en-US')

    // 刷新 = 整个模块图重新求值
    vi.resetModules()
    const fresh = await import('./locale')
    expect(fresh.readStoredLocale()).toBe('en-US')
    expect(fresh.detectLocale()).toBe('en-US')
  })

  it('存进去的脏值按规范化读出来，读不懂就当没选过', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-Hans')
    expect(readStoredLocale()).toBe('zh-CN')

    localStorage.setItem(LOCALE_STORAGE_KEY, 'klingon')
    expect(readStoredLocale()).toBeNull()
  })

  it('传 null 是「清掉手动选择」，回到跟随系统', async () => {
    writeStoredLocale('en-US')
    writeStoredLocale(null)
    expect(readStoredLocale()).toBeNull()
    asSystem('zh-CN')
    expect((await import('./locale')).detectLocale()).toBe('zh-CN')
  })

  it('存储不可用（隐私模式）时不抛，只是记不住', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => writeStoredLocale('en-US')).not.toThrow()
    spy.mockRestore()

    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    expect(readStoredLocale()).toBeNull()
    get.mockRestore()
  })
})

describe('语言清单', () => {
  it('每档都有自称，且用目标语言自己写（切换菜单里不翻译）', () => {
    expect(SUPPORTED_LOCALES).toEqual(['zh-CN', 'en-US'])
    expect(LOCALE_LABELS['zh-CN']).toBe('简体中文')
    expect(LOCALE_LABELS['en-US']).toBe('English')
  })
})


/**
 * 桌面壳带过来的语言（落地 URL 的 `?lang=`）。
 *
 * 这条盯的是一个只在真桌面版上才发作的坏法：sidecar 绑 `127.0.0.1:0`，端口
 * 每次启动都不一样，而端口是 Web Storage origin 的一部分——localStorage 的
 * 语言偏好活不过一次重启。于是 `detectLocale()` 退回系统语言，`main.tsx` 又
 * 把这个退回值报给壳，把用户真正选过的那门语言连同原生菜单一起覆盖掉：
 * 选了跟系统不同语言的用户，每次重启都被打回去。
 */
describe('桌面壳经 ?lang= 带过来的选择', () => {
  const detect = async () => (await import('./locale')).detectLocale()
  const setSearch = (search: string) => {
    Object.defineProperty(window, 'location', {
      value: { ...window.location, search },
      writable: true,
      configurable: true,
    })
  }

  afterEach(() => setSearch(''))

  it('没有手动选择时用壳带来的那门语言，而不是系统语言', async () => {
    setSearch('?lang=en-US')
    asSystem('zh-CN')
    expect(await detect()).toBe('en-US')
  })

  it('本次会话里刚做的选择优先于壳带来的那份', async () => {
    setSearch('?lang=en-US')
    writeStoredLocale('zh-CN')
    expect(await detect()).toBe('zh-CN')
  })

  it('壳没带（浏览器模式）时行为一个字节不变', async () => {
    setSearch('?open=Fig1')
    asSystem('en-GB')
    expect(await detect()).toBe('en-US')
  })

  it('认不出来的 lang 直接忽略，不当成一次选择', async () => {
    setSearch('?lang=fr-FR')
    asSystem('zh-CN')
    expect(await detect()).toBe('zh-CN')
  })
})
