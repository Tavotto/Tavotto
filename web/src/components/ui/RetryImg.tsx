/**
 * 带有界重试的 `<img>`（`lib/imgRetry`）：`/api/render` 的一次失败可能只是候选后端的
 * 背压（503 + Retry-After），按退避表再取几次；别的地址失败照旧。属性原样透传。
 */
import type { ImgHTMLAttributes } from 'react'

import { useRetryingSrc } from '@/lib/imgRetry'

export function RetryImg({ src, ...rest }: ImgHTMLAttributes<HTMLImageElement> & { src: string }) {
  const retry = useRetryingSrc(src)
  return <img {...rest} src={retry.src} onError={retry.onError} />
}
