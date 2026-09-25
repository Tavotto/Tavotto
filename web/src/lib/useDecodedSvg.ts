import { useEffect, useState } from 'react'

/**
 * 换一版内联 SVG 时，**先把新图里嵌着的位图解码好，再换上去**；解码期间旧画面留着。
 *
 * 为什么（2026-09-25 用户报「松手后整张图糊一下，约 0.1 秒」）：引擎预览 SVG 里的
 * imshow / pcolormesh 是 `<image href="data:image/png;base64,…">`。整段 innerHTML 一换，
 * 浏览器对这些新 `<image>` 是**异步解码**的——解码完之前那一两帧画的是空白或低清的
 * 中间态。用户那张三联图几乎整张都是位图，于是看上去就是「整体糊了一下」。
 *
 * 做法：新字符串到了先不交出去，用离屏 `Image` 把它引用的每张 data URI 解码一遍
 * （浏览器按 URL 共用解码结果），解码完 / 超时再交出新字符串。拖动的预览位移挂在
 * 旧 DOM 上，所以这段等待里用户看到的就是「拖到的位置」，不是弹回、不是空白。
 *
 * 边界：
 * - **第一次挂载不等**：之前什么都没有，等待只会让图晚出来；
 * - 没有 data URI 位图（纯矢量图）、或者运行环境没有 `Image.decode`（jsdom）→ 立即换；
 * - 等待有上限 `SWAP_DECODE_CAP_MS`：解码失败或极慢也绝不把新图扣住；
 * - 换回 null（退出编辑 / 转去位图链路）立即生效——那不是「换一版」，是撤下。
 */
export const SWAP_DECODE_CAP_MS = 250

const DATA_IMAGE_HREF = /<image\b[^>]*?\bhref="(data:image\/[^"]+)"/g

/** 一段 SVG 里引用的 data URI 位图（去重） */
export function embeddedRasterHrefs(svg: string): string[] {
  const out = new Set<string>()
  for (const m of svg.matchAll(DATA_IMAGE_HREF)) out.add(m[1])
  return [...out]
}

function decodeAll(hrefs: string[]): Promise<void> | null {
  if (typeof Image === 'undefined' || typeof Image.prototype.decode !== 'function') return null
  return Promise.all(
    hrefs.map((href) => {
      const img = new Image()
      img.src = href
      return img.decode().catch(() => undefined)
    }),
  ).then(() => undefined)
}

export function useDecodedSvg(html: string | null): string | null {
  const [shown, setShown] = useState(html)
  // 渲染期同步判定「不用等」的情形，避免多挂一帧旧图
  const immediate =
    html === shown || html == null || shown == null || embeddedRasterHrefs(html).length === 0
  if (immediate && html !== shown) setShown(html)

  useEffect(() => {
    if (immediate || html == null) return
    let done = false
    const finish = () => {
      if (done) return
      done = true
      setShown(html)
    }
    const decoding = decodeAll(embeddedRasterHrefs(html))
    if (!decoding) {
      finish()
      return
    }
    const cap = window.setTimeout(finish, SWAP_DECODE_CAP_MS)
    void decoding.then(finish)
    return () => {
      done = true
      window.clearTimeout(cap)
    }
  }, [html, immediate])

  return immediate ? html : shown
}
