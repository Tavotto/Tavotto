/**
 * 原图字体在**本机**的状态与预览字体（ADR 0164）。
 *
 * - 状态：问本机服务 `/api/source-fonts/status`（判据与导出是同一个函数），按身份缓存；同一拍要的合成一次请求。
 * - 字节：状态为 `ok` 才向本机服务取（`/api/source-fonts/<sha256>/<face>`，**只来自本机、不走网络**），
 *   用 `FontFace` API 注册成一个由身份派生的 CSS 族名（`cssFamilyOf`）。取不到 / 浏览器拒收 → 状态保持 `ok`
 *   但没有预览字体，画面按 `fontFamily` 的批准族显示（导出不受影响，导出读的是本机文件）。
 * - 没有本机服务（浏览器 playground / 旧后端：404 或网络错误）→ `unsupported`，不重试轰炸。
 *
 * 加载字体字节的代码**只许在本文件**（`tests/test_font_provenance.py::test_font_bytes_are_loaded_only_from_the_local_source_font_endpoint`）。
 */
import { useSyncExternalStore } from 'react'
import { fetchSourceFontBytes, fetchSourceFontStatus } from './api'
import { cssFamilyOf, faceKey, type SourceFontState } from './sourceFont'
import type { SourceFace, SourceFont } from '@/types/document'

/** 一次最多问几张（与后端 `SOURCE_FONTS_MAX_ITEMS` 同值） */
export const SOURCE_FONTS_MAX_ITEMS = 64

const states = new Map<string, SourceFontState>()
/** 已经向浏览器注册成功的预览族名 */
const loaded = new Set<string>()
const loading = new Set<string>()
const asked = new Set<string>()
/**
 * 这张脸自己的粗斜：注册 FontFace 时按它声明，浏览器就不会在它上面再合成一次粗体 / 斜体。
 * `unicodeRange` 限定这张脸只画哪些字（批准的中文粗体不许接管英文）。
 */
const styles = new Map<string, { bold: boolean; italic: boolean; unicodeRange?: string }>()
let queued: SourceFace[] = []
let scheduled = false
let version = 0
const listeners = new Set<() => void>()

function notify() {
  version++
  for (const l of listeners) l()
}

async function loadPreview(f: SourceFace) {
  const key = faceKey(f)
  if (loaded.has(key) || loading.has(key)) return
  loading.add(key)
  try {
    if (typeof FontFace === 'undefined' || typeof document === 'undefined' || !document.fonts) return
    const buf = await fetchSourceFontBytes(f.sha256, f.face)
    const st = styles.get(key)
    const face = new FontFace(cssFamilyOf(f), buf, {
      weight: st?.bold ? '700' : '400',
      style: st?.italic ? 'italic' : 'normal',
      ...(st?.unicodeRange ? { unicodeRange: st.unicodeRange } : {}),
    })
    await face.load()
    document.fonts.add(face)
    loaded.add(key)
  } catch {
    /* 预览取不到不是错误：画面退回批准族，导出照常用本机文件 */
  } finally {
    loading.delete(key)
    notify()
  }
}

async function flush() {
  scheduled = false
  const batch = queued
  queued = []
  for (let i = 0; i < batch.length; i += SOURCE_FONTS_MAX_ITEMS) {
    const chunk = batch.slice(i, i + SOURCE_FONTS_MAX_ITEMS)
    try {
      const got = (await fetchSourceFontStatus(chunk.map((f) => ({ sha256: f.sha256, face: f.face }))))
        ?.results
      if (!Array.isArray(got)) throw new Error('shape')
      for (const r of got) {
        const key = faceKey(r)
        const state = r.state as SourceFontState
        states.set(key, state)
        if (state === 'ok') {
          const f = chunk.find((c) => faceKey(c) === key)
          if (f) void loadPreview(f)
        }
      }
    } catch {
      // 没有这组端点（playground / 旧后端）或后端暂时问不到：记 unsupported，界面按「退到批准族」显示
      for (const f of chunk) states.set(faceKey(f), 'unsupported')
    }
    notify()
  }
}

/** 取一张脸的本机状态；还没问过就排进下一次请求，先回 `unknown`。 */
export function sourceFontState(
  f: SourceFace,
  style?: Pick<SourceFont, 'bold' | 'italic'> & { unicodeRange?: string },
): SourceFontState {
  const key = faceKey(f)
  const hit = states.get(key)
  if (hit) return hit
  if (!asked.has(key)) {
    asked.add(key)
    if (style) {
      styles.set(key, {
        bold: style.bold === true,
        italic: style.italic === true,
        ...(style.unicodeRange ? { unicodeRange: style.unicodeRange } : {}),
      })
    }
    queued.push(f)
    if (!scheduled) {
      scheduled = true
      queueMicrotask(() => void flush())
    }
  }
  return 'unknown'
}

/** 这张脸的预览 CSS 族名；没有加载成功就 undefined（画面用批准族）。 */
export const previewFamilyOf = (f: SourceFace): string | undefined =>
  loaded.has(faceKey(f)) ? cssFamilyOf(f) : undefined

export function subscribeSourceFonts(l: () => void): () => void {
  listeners.add(l)
  return () => listeners.delete(l)
}

/** React：状态 / 预览字体到达时重渲染。 */
export function useSourceFonts(): number {
  return useSyncExternalStore(subscribeSourceFonts, () => version)
}

/** 测试用：清空缓存。 */
export function resetSourceFontCache(): void {
  states.clear()
  loaded.clear()
  loading.clear()
  asked.clear()
  styles.clear()
  queued = []
  scheduled = false
  version++
}
