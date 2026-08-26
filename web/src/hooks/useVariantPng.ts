/**
 * 「按 overrides 出一张缩略图」的可复用 hook（issue #131）。
 *
 * 布局版本时间线以前对面板一律用 `panelSrc(fileId, …)`——那是**磁盘上的素材**，
 * 不含任何图内修改。于是两个版本之间图内文字位置、字号、图例、axes 布局完全
 * 不同，缩略图却一模一样：用户点了恢复、画面确实变了，可时间线上看不出差别，
 * 只能得出「恢复失败」的结论。
 *
 * 这里走 `POST /api/engine/preview_png`（按 patches 出图、状态中立），并且：
 *   - 按变体键缓存 blob URL，同一版本反复展开只发一次；
 *   - 缓存**有界**（LRU，上限 CACHE_MAX），超出的当场 revoke，不泄漏 blob；
 *   - 组件卸载 / 变体切换时 abort 在途请求；
 *   - 失败**保留磁盘图**并把 `approximate` 置位——调用方必须据此标注
 *     「图内修改预览不可用」，绝不无提示地拿原图冒充版本视觉状态；
 *   - 只在 `enabled` 时发请求：版本列表里每一条都预渲染的话，一次展开对话框
 *     就是几十次 matplotlib 往返。
 */
import { useEffect, useState } from 'react'
import { enginePreviewPng } from '@/lib/api'
import { engineTransport } from '@/lib/engineTransport'
import { currentProjectId } from '@/lib/session'

/** 缓存上限：一次对话框里用户来回比对的版本数远小于它 */
const CACHE_MAX = 24

/** 变体键 → blob URL。Map 的插入序天然就是 LRU 需要的顺序 */
const cache = new Map<string, string>()

/**
 * 缓存键**必须带项目与素材版本**。
 *
 * `fileId` 是项目内的相对路径：两个项目里同名同 overrides 的图完全可能是两张
 * 不同的图，只按 (fileId, overrides, bucket) 缓存的话，在项目 A 看过某个版本
 * 之后切到项目 B，会直接命中 A 的 blob 并把**别人的图**当成这一版的预览显示
 * 出来，而且一次请求都不发。素材本身被改过（mtime 变了）同理。
 */
const keyOf = (fileId: string, overrides: unknown[], bucket: number, rev: number) =>
  `${currentProjectId() ?? '-'} ${rev} ${bucket} ${fileId} ${JSON.stringify(overrides)}`

function remember(key: string, url: string) {
  cache.set(key, url)
  while (cache.size > CACHE_MAX) {
    const oldest = cache.keys().next().value as string | undefined
    if (oldest == null) break
    const dead = cache.get(oldest)
    cache.delete(oldest)
    // 自己造的 blob 自己收：不 revoke 的话每开一次对话框都留一批在内存里
    if (dead && dead.startsWith('blob:')) URL.revokeObjectURL(dead)
  }
}

/** 换项目 / 测试隔离：整表释放 */
export function clearVariantPngCache(): void {
  for (const url of cache.values()) {
    if (url.startsWith('blob:')) URL.revokeObjectURL(url)
  }
  cache.clear()
}

export interface VariantPng {
  /** 出好的图；null = 还没有（调用方先显示磁盘原图） */
  url: string | null
  /** 正在出图 */
  loading: boolean
  /**
   * 这一版的图内修改**没能渲染出来**（引擎不可达 / 脚本报错 / 素材没脚本）。
   * 调用方必须把它显示出来——「近似预览」比一张骗人的原图诚实。
   */
  approximate: boolean
}

export function useVariantPng(
  fileId: string,
  overrides: unknown[],
  bucket: number,
  enabled: boolean,
  /** 素材版本（mtime）：磁盘上那份被改过时旧缩略图必须作废 */
  rev = 0,
): VariantPng {
  const key = keyOf(fileId, overrides, bucket, rev)
  const [state, setState] = useState<VariantPng>(() => ({
    url: cache.get(key) ?? null,
    loading: false,
    approximate: false,
  }))

  useEffect(() => {
    if (!enabled) {
      setState({ url: null, loading: false, approximate: false })
      return
    }
    // 没有 override 的面板与磁盘图一模一样，白跑一次引擎没有意义
    if (!overrides.length) {
      setState({ url: null, loading: false, approximate: false })
      return
    }
    const hit = cache.get(key)
    if (hit) {
      setState({ url: hit, loading: false, approximate: false })
      return
    }
    const ctrl = new AbortController()
    let live = true
    setState((s) => ({ url: s.url, loading: true, approximate: false }))
    const transport = engineTransport()
    const pending = transport
      ? transport.previewPngUrl(fileId, overrides, bucket, ctrl.signal)
      : enginePreviewPng(fileId, overrides, bucket, ctrl.signal).then((b) =>
          URL.createObjectURL(b),
        )
    void pending
      .then((url) => {
        remember(key, url)
        if (live) setState({ url, loading: false, approximate: false })
      })
      .catch(() => {
        // 失败不是「空白」：退回磁盘图，但必须标成近似
        if (live) setState({ url: null, loading: false, approximate: true })
      })
    return () => {
      live = false
      ctrl.abort()
    }
    // overrides 的内容变化由 key 表达（数组引用每次都是新的）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled])

  return state
}
