import { create } from 'zustand'
import { fetchPanels, type PanelInfo, type PanelsResponse } from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { t } from '@/i18n'

const USED_KEY = 'tavotto.assetUsed'

function readUsed(): Record<string, number> {
  try {
    const raw = localStorage.getItem(USED_KEY)
    const v = raw ? JSON.parse(raw) : null
    return v && typeof v === 'object' ? (v as Record<string, number>) : {}
  } catch {
    return {}
  }
}

/* -------------------------------------------------------------------------- */
/*  并发治理                                                                    */
/*                                                                            */
/*  `load()` 的触发点今天有七个：项目打开、panel.file_changed、               */
/*  registry.changed、assets.changed、手动刷新、RegistryDialog 的操作、        */
/*  SSE 重连恢复。一次统一刷新会连着发 registry.changed + assets.changed 两条  */
/*  事件，所以「同一批事件里被调好几次」是常态而不是异常。                     */
/*                                                                            */
/*  三件事必须挡住：                                                          */
/*                                                                            */
/*   1. **较慢的旧响应覆盖较新的**。判据是请求序号，不是「谁最后返回」——      */
/*      后者恰恰是缺陷本身。序号比 AbortController 稳：被 abort 的请求在       */
/*      jsdom 与真实浏览器里抛的东西不一样，而我们要挡的行为（旧值落地）在     */
/*      两边一模一样。                                                        */
/*   2. **落进另一个项目**。判据是「发请求那一刻这个标签页认领的是哪个项目」，  */
/*      与 documentStore 排队落盘带走 pj 是同一条纪律。`null`（跟随后端默认    */
/*      项目）与某个具体 id 是**两个不同的取值**，不合并：合并的话切项目之后   */
/*      旧项目的素材会落进新项目。                                            */
/*   3. **在途期间发生的改动被合并吞掉**。合并的是「请求」不是「问题」：后来    */
/*      者复用在途那一份，而那一份可能在它的事件发生之前就读完了目录。所以在    */
/*      途期间来的非 force 调用共用一次**补问**（`trailing`），在本次落地之后   */
/*      再发一次。补一次就够——同一批里来多少次都只补这一次。                   */
/*                                                                            */
/*  合并：同项目的在途请求会被后来者复用（`inflight`），一批事件最多两次        */
/*  /api/panels（在途那次 + 一次补问）。但 `force` 永远另起一次——手动刷新按钮   */
/*  要是被一次早就发出的在途请求吞掉，用户按了没反应，而"没反应"正是他按它的     */
/*  原因。                                                                     */
/* -------------------------------------------------------------------------- */

/** 已经发出的请求数；每次请求取一个递增号 */
let seq = 0
/** 已经落地的那次请求的号；比它小的响应一律丢弃 */
let applied = 0
/** 同项目可复用的在途请求；`force` 不看它，也不写它 */
let inflight: {
  pj: string | null
  promise: Promise<PanelsResponse | null>
  /**
   * 在途期间又来了一次非 force 的 `load()` 时创建：本次落地之后**补跑一次**。
   *
   * 合并仍然成立——在途期间来多少次都只补**这一次**。但补这一次是必须的：
   * 服务端读完目录到响应落地之间还有一段时间，那段时间里发生的改动不在这份
   * 响应里，而 `inflight` 一清就再没有人去问第二遍——那个改动要一直等到下一
   * 条事件、重连或者用户手动刷新才看得见。
   */
  trailing: Promise<PanelsResponse | null> | null
} | null = null

interface AssetState {
  panels: PanelInfo[]
  byId: Record<string, PanelInfo>
  figuresDir: string
  loading: boolean
  /** 至少成功加载过一次；用来区分「首次加载」与「刷新」两种 loading */
  loaded: boolean
  error: string | null
  /** fileId → 最近一次加入画布的时间戳；「最近使用」排序用，与文件 mtime 是两回事 */
  recentlyUsed: Record<string, number>
  /**
   * 重取素材清单。返回**本次真正生效的**那份响应；被丢弃（旧响应 / 换了项目）
   * 或失败时返回 `null`——调用方据此决定要不要拿它去同步文档里的派生元数据。
   *
   * `force: true` = 绕过合并另起一次（手动刷新）。
   */
  load: (opts?: { force?: boolean }) => Promise<PanelsResponse | null>
  /** 事件驱动的刷新入口：与同一批里的其它调用合并成一次请求 */
  refresh: () => Promise<PanelsResponse | null>
  markUsed: (id: string) => void
}

export const useAssetStore = create<AssetState>((set, get) => ({
  panels: [],
  byId: {},
  figuresDir: '',
  loading: false,
  loaded: false,
  error: null,
  recentlyUsed: readUsed(),

  load: (opts) => {
    if (!opts?.force && inflight && inflight.pj === currentProjectId()) {
      const cur = inflight
      // **不能直接把在途那份还给调用方**：它可能是在本次事件发生之前就读完
      // 目录的，那份数据里没有刚刚这一下改动。合并的是「请求」，不是「问题」
      // ——本次落地之后补问一遍，同一批里的多个调用共用这同一次补问。
      cur.trailing ??= cur.promise.then(() =>
        // 补问期间换了项目：那份清单属于别人的图库，一个字节都不许落地
        currentProjectId() === cur.pj ? get().load() : null,
      )
      return cur.trailing
    }

    const mine = ++seq
    const pj = currentProjectId()
    set({ loading: true })
    const promise = fetchPanels()
      .then((data) => {
        // 换过项目 = 这份清单属于别人的图库，一个字节都不许落地
        if (pj !== currentProjectId()) return null
        // 更新的那次已经落地了：旧响应到得再晚也只是历史
        if (mine < applied) return null
        applied = mine
        set({
          panels: data.panels,
          byId: Object.fromEntries(data.panels.map((p) => [p.id, p])),
          figuresDir: data.figures_dir,
          loaded: true,
          error: null,
        })
        return data
      })
      .catch((err: unknown) => {
        if (pj !== currentProjectId() || mine < applied) return null
        // **panels / byId 一个都不清**：后台刷新失败时清空等于让画布上的
        // 面板集体变成「缺失素材」，而磁盘上它们好好的。首次加载失败时
        // `loaded` 仍是 false，界面照旧显示 EmptyState。
        set({ error: err instanceof Error ? err.message : String(err) })
        return null
      })
      .finally(() => {
        if (inflight?.promise === promise) inflight = null
        // 还有更新的请求在途时不要收掉 loading：收掉了转圈会闪一下又转回来
        if (mine === seq) set({ loading: false })
      })

    if (!opts?.force) inflight = { pj, promise, trailing: null }
    return promise
  },

  refresh: () => get().load(),

  markUsed: (id) =>
    set((s) => {
      const recentlyUsed = { ...s.recentlyUsed, [id]: Date.now() }
      try {
        localStorage.setItem(USED_KEY, JSON.stringify(recentlyUsed))
      } catch {
        /* 存不下就只在本次会话里有效 */
      }
      return { recentlyUsed }
    }),
}))

/**
 * 只给测试用：把模块级的并发账本清零。
 *
 * 它们活得比一次 `useAssetStore.setState()` 长——不清的话，上一个用例留下的
 * `applied` 会让下一个用例的第一次响应被当成「旧响应」丢掉，而那个用例看到的
 * 现象是「明明 resolve 了，store 里什么都没有」。
 */
export function resetAssetLoadBookkeeping(): void {
  seq = 0
  applied = 0
  inflight = null
}

/**
 * 约定俗成的几个目录有专属显示名；**其余目录一律原样显示**——那是用户自己
 * 起的文件夹名，翻译它只会让人对不上磁盘。
 */
const FOLDER_KEYS: Record<string, string> = {
  '.': 'folderRoot',
  main_text_panels: 'folderMainText',
  supplementary_panels: 'folderSupplementary',
  base: 'folderBase',
}

export const folderLabel = (folder: string): string => {
  const key = FOLDER_KEYS[folder]
  return key ? t(`assets.${key}`, { ns: 'workspace' }) : folder
}
