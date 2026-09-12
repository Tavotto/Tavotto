/**
 * 启动时的「有新版本」提示：**该不该说、说什么**只在这里判。
 *
 * 更新检查本身早就有了（`store/updateStore.ts` 启动静默查一次，桌面归 Tauri、
 * 浏览器归 `/api/update/*`），缺的是把结果**说出口**——之前有新版只在「⋯」上
 * 点一个 6px 的圆点，没人看得见。这里给出弹窗要显示的模型；渲染在
 * `components/UpdateNoticeDialog.tsx`，动作全部复用 store 里既有的 action。
 *
 * 三条约束：
 *   * **升级永远由用户按下按钮触发**（与 store 同一条纪律），弹窗只是把按钮
 *     放到看得见的地方；
 *   * **「稍后」按版本记**：同一个版本不再每次启动都催；出了更新的版本才再说。
 *     记在 localStorage（这是本机偏好，不进文档）；
 *   * **通道互斥**：桌面模式 `status` 可能一直是 null（后端 updater 关着，只有
 *     设置页才去取），所以桌面那条只看 `desktopUpdate`，不等 `status`。
 */

const KEY = 'tavotto.update.dismissed'

/** 用户上次点「稍后」时的版本；读不到 / 存储不可用都回 null（= 没关过） */
export function readDismissedUpdate(): string | null {
  try {
    const raw = localStorage.getItem(KEY)
    return typeof raw === 'string' && raw.trim() ? raw : null
  } catch {
    return null
  }
}

export function writeDismissedUpdate(version: string): void {
  try {
    localStorage.setItem(KEY, version)
  } catch {
    /* 存储不可用：本次会话仍由 store 里的字段挡着，下次启动再提示一次而已 */
  }
}

/**
 * 弹窗的三种形态，按「按下主按钮会发生什么」分——不是按安装方式分：
 *   * `desktop`  Tauri 下载签名包并就地替换，装完要重启；
 *   * `self`     后端跑 pip / pipx 装 wheel，升完要重启进程；
 *   * `manual`   源码检出，不代劳（`git pull`），只能带用户去看。
 */
export type UpdateNoticeKind = 'desktop' | 'self' | 'manual'

export interface UpdateNotice {
  kind: UpdateNoticeKind
  version: string
  /** 正在跑的版本；桌面模式下 `status` 没取过时是 null（弹窗上就不写这一句） */
  current: string | null
  /** 发行说明正文（Release body，后端截到 4000 字）；没有就 null */
  notes: string | null
  /** 发行说明页面；桌面通道没有单独的页面链接，退回 Releases 列表 */
  notesUrl: string | null
  /** `manual` 时让用户自己跑的命令（后端给的，如 `git pull`）；其余形态 null */
  upgradeCommand: string | null
}

/** store 里与这条判据相关的那几个字段（不依赖整个 UpdateState，测试好摆） */
export interface UpdateNoticeInputs {
  desktopUpdate: { version: string; notes?: string } | null
  status: {
    current?: string
    update_available?: boolean
    latest?: string
    notes?: string
    can_self_update?: boolean
    upgrade_command?: string
    html_url?: string
    releases_url?: string
  } | null
  dismissedVersion: string | null
}

/** 该弹的那条提示；null = 没新版 / 这一版已经说过稍后 */
export function pendingUpdateNotice(s: UpdateNoticeInputs): UpdateNotice | null {
  const notice = rawNotice(s)
  if (!notice) return null
  if (s.dismissedVersion !== null && s.dismissedVersion === notice.version) return null
  return notice
}

function rawNotice(s: UpdateNoticeInputs): UpdateNotice | null {
  const current = s.status?.current || null
  if (s.desktopUpdate?.version) {
    return {
      kind: 'desktop',
      version: s.desktopUpdate.version,
      current,
      notes: s.desktopUpdate.notes?.trim() || null,
      notesUrl: s.status?.releases_url ?? null,
      upgradeCommand: null,
    }
  }
  const st = s.status
  if (!st?.update_available || !st.latest) return null
  return {
    kind: st.can_self_update ? 'self' : 'manual',
    version: st.latest,
    current,
    notes: st.notes?.trim() || null,
    notesUrl: st.html_url ?? st.releases_url ?? null,
    upgradeCommand: st.can_self_update ? null : st.upgrade_command || null,
  }
}
