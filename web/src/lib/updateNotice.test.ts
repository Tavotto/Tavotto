/**
 * 「该不该提示、提示什么」的判据（`lib/updateNotice.ts`）。
 *
 * 钉住的三件事：
 *   1. **通道互斥**：桌面模式 `status` 可能一直是 null，桌面那条只看 `desktopUpdate`；
 *   2. **「稍后」按版本记**：关过的那一版下次不说，更新的版本照说；
 *   3. **存储不可用不算错**：读回 null、写不抛——它是偏好，不是状态机。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  pendingUpdateNotice,
  readDismissedUpdate,
  writeDismissedUpdate,
  type UpdateNoticeInputs,
} from './updateNotice'

const base: UpdateNoticeInputs = { desktopUpdate: null, status: null, dismissedVersion: null }

beforeEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('三种形态', () => {
  it('桌面：只看 desktopUpdate，status 为 null 也能说；说明链接退回 Releases 列表', () => {
    expect(pendingUpdateNotice({ ...base, desktopUpdate: { version: '0.15.0' } })).toEqual({
      kind: 'desktop',
      version: '0.15.0',
      current: null,
      notes: null,
      notesUrl: null,
      upgradeCommand: null,
    })
    expect(
      pendingUpdateNotice({
        ...base,
        desktopUpdate: { version: '0.15.0' },
        status: { releases_url: 'https://example.test/releases' },
      })?.notesUrl,
    ).toBe('https://example.test/releases')
  })

  it('pip / pipx：后端能代劳 → self，说明链接是那个版本的页面', () => {
    expect(
      pendingUpdateNotice({
        ...base,
        status: {
          current: '0.14.0',
          update_available: true,
          latest: '0.15.0',
          notes: '## 新功能\n- 一件事\n',
          can_self_update: true,
          html_url: 'https://example.test/v0.15.0',
          releases_url: 'https://example.test/releases',
        },
      }),
    ).toEqual({
      kind: 'self',
      version: '0.15.0',
      current: '0.14.0',
      notes: '## 新功能\n- 一件事',
      notesUrl: 'https://example.test/v0.15.0',
      upgradeCommand: null,
    })
  })

  it('源码检出：不代劳 → manual，把后端给的命令带上', () => {
    expect(
      pendingUpdateNotice({
        ...base,
        status: {
          update_available: true,
          latest: '0.15.0',
          can_self_update: false,
          upgrade_command: 'git pull',
        },
      }),
    ).toMatchObject({ kind: 'manual', upgradeCommand: 'git pull' })
  })

  it('桌面查到了就以桌面为准，哪怕 status 里也说有新版；说明正文取桌面那份', () => {
    expect(
      pendingUpdateNotice({
        ...base,
        desktopUpdate: { version: '0.15.0', notes: '桌面说明' },
        status: { update_available: true, latest: '0.16.0', notes: '后端说明', can_self_update: true },
      }),
    ).toMatchObject({ kind: 'desktop', version: '0.15.0', notes: '桌面说明' })
  })

  it('说明正文只有空白也算没有（弹窗不给一块空的滚动区）', () => {
    expect(
      pendingUpdateNotice({
        ...base,
        status: { update_available: true, latest: '0.15.0', notes: '  \n ' },
      })?.notes,
    ).toBeNull()
  })
})

describe('不该说的时候', () => {
  it('没查到新版：null', () => {
    expect(pendingUpdateNotice(base)).toBeNull()
    expect(pendingUpdateNotice({ ...base, status: { update_available: false, latest: '0.14.0' } })).toBeNull()
  })

  it('说有新版却没给版本号：不提示（横幅上没有版本可写）', () => {
    expect(pendingUpdateNotice({ ...base, status: { update_available: true } })).toBeNull()
  })

  it('这一版已经说过稍后：null；出了更新的版本照说', () => {
    const status = { update_available: true, latest: '0.15.0', can_self_update: true }
    expect(pendingUpdateNotice({ ...base, status, dismissedVersion: '0.15.0' })).toBeNull()
    expect(pendingUpdateNotice({ ...base, status, dismissedVersion: '0.14.1' })?.version).toBe('0.15.0')
    // 桌面那条同样按版本比
    expect(
      pendingUpdateNotice({ ...base, desktopUpdate: { version: '0.15.0' }, dismissedVersion: '0.15.0' }),
    ).toBeNull()
  })
})

describe('「稍后」的落盘', () => {
  it('写了就读得回来；没写过是 null', () => {
    expect(readDismissedUpdate()).toBeNull()
    writeDismissedUpdate('0.15.0')
    expect(readDismissedUpdate()).toBe('0.15.0')
  })

  it('存储不可用：读回 null，写不抛', () => {
    const broken = {
      getItem: () => {
        throw new Error('SecurityError')
      },
      setItem: () => {
        throw new Error('QuotaExceededError')
      },
    }
    vi.stubGlobal('localStorage', broken)
    expect(readDismissedUpdate()).toBeNull()
    expect(() => writeDismissedUpdate('0.15.0')).not.toThrow()
  })
})
