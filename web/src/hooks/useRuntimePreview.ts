import { useEffect, useRef, useState } from 'react'
import { fetchRuntimePreviewForState } from '@/lib/api'

/**
 * runtime 面板「还没有引擎图」时挂的预览：**按面板此刻的 overrides** 取（QA-WIN-21）。
 *
 * 老写法是 `<img src=/api/runtime/preview?id=…>`，后端回的是「最近一次渲染」。撤销回一个
 * 前端已经画过的变体时不会再发渲染请求（`useEngineSync` 见 `lastPatches === want` 就跳过），
 * 「最近一次」于是停在撤销前的状态上：重开文档的冷画布 / 导出预览显示撤销前的图，而文件里存的
 * 和导出的都是撤销后的。显示缓存必须按状态内容寻址——这里把 overrides 交给后端，命中才是
 * 「这个状态的图」，没命中 `exact === false`，调用方必须标「近似预览」。
 *
 * 取到之前 `url` 为 null（不借用另一个状态的图；调用方显示占位）。`nonce` 是重跑后的预览换代计数。
 * 失败（非 404）也回 `url: null` 且 `failed: true`，调用方可退回 GET 版（仍标近似）。
 */
export interface RuntimePreview {
  url: string | null
  /** 这张图是不是按此刻 overrides 画的；`null` = 还没取到 */
  exact: boolean | null
  failed: boolean
}

const IDLE: RuntimePreview = { url: null, exact: null, failed: false }

export function useRuntimePreview(
  fileId: string,
  overrides: readonly unknown[],
  enabled: boolean,
  nonce = 0,
): RuntimePreview {
  // 变体串（文件 + overrides 内容），与 `renderKeyOf` 同口径：runtime 面板没有来源驱动的 guard 后缀
  const variant = `${fileId} ${JSON.stringify(overrides)}`
  const [state, setState] = useState<RuntimePreview & { key: string | null }>({ ...IDLE, key: null })
  const urlRef = useRef<string | null>(null)

  useEffect(() => {
    if (!enabled) return
    const ctrl = new AbortController()
    void fetchRuntimePreviewForState(fileId, overrides as unknown[], ctrl.signal)
      .then((got) => {
        if (ctrl.signal.aborted) return
        if (urlRef.current) URL.revokeObjectURL(urlRef.current)
        urlRef.current = got ? URL.createObjectURL(got.blob) : null
        setState({ key: variant, url: urlRef.current, exact: got ? got.exact : null, failed: false })
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setState({ key: variant, url: null, exact: null, failed: true })
      })
    return () => ctrl.abort()
    // overrides 的内容变化由 variant 表达（数组引用每次 commit 都变）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, fileId, variant, nonce])

  // 卸载时把最后一张还回去
  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current)
  }, [])

  // 别的状态的图不冒充当前状态：key 对不上就当还没取到
  if (!enabled || state.key !== variant) return IDLE
  return state
}
