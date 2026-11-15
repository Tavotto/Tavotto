import { useEffect, useId, useRef, useState } from 'react'
import { outlineText } from '@/lib/api'
import { pathTextExport } from '@/lib/exportPayload'
import { arcPathOf, clampOffset, startHandle } from '@/lib/pathText'
import { pathD, pathGeometry, type PathSeg } from '@/lib/pathGeom'
import { hitStrokeWidth } from '@/lib/shapeGeometry'
import { MM_PER_PT } from '@/lib/units'
import { commitPathText } from '@/store/pathTextActions'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld, useViewportStore } from '@/store/viewportStore'
import type { PathObject, PathTextObject } from '@/types/document'
import { PathBody } from './PathView'

/**
 * 路径文字（ADR 0190）。字落在哪只由后端算（`rendercore/pathtext`，与导出同一次排字）：画布把对象挪到原点、不带旋转
 * 发给文字转轮廓的接口（`/api/text/outline`），拿回字形轮廓照着画——画布与导出逐点同源（`tests/test_path_text.py`
 * 按像素比过）。等结果的这一下（第一次、或改了字 / 路径）先用浏览器的 `<textPath>` 顶着，结果回来就换掉；旧结果在新结果
 * 回来前一直留着，打字时不闪。
 *
 * 命中：外层 div 让位（ObjectView），交给沿路径的透明命中带（带宽按字号），点字附近就能选中。
 */

type Outline = { paths: PathObject[] }
const cache = new Map<string, Outline>()
const pending = new Map<string, Promise<Outline | null>>()
const CACHE_MAX = 200

function localPayload(obj: PathTextObject) {
  return { ...pathTextExport({ ...obj, x: 0, y: 0, rotationDeg: undefined }), id: obj.id }
}

export function fetchPathTextOutline(obj: PathTextObject): Promise<Outline | null> {
  const body = localPayload(obj)
  const key = JSON.stringify(body)
  const hit = cache.get(key)
  if (hit) return Promise.resolve(hit)
  const inflight = pending.get(key)
  if (inflight) return inflight
  const p = outlineText([body])
    .then((r) => {
      const one = r.results[0]
      if (!one || !('paths' in one)) return null
      const out = { paths: one.paths as PathObject[] }
      if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string)
      cache.set(key, out)
      return out
    })
    .catch(() => null)
    .finally(() => pending.delete(key))
  pending.set(key, p)
  return p
}

export function usePathTextOutline(obj: PathTextObject): { outline: Outline | null; fresh: boolean } {
  const key = JSON.stringify(localPayload(obj))
  const [state, setState] = useState<{ key: string; outline: Outline } | null>(() => {
    const hit = cache.get(key)
    return hit ? { key, outline: hit } : null
  })
  useEffect(() => {
    if (cache.has(key)) {
      setState({ key, outline: cache.get(key)! })
      return
    }
    let alive = true
    // 打字 / 拖起点时连着变：歇 120 ms 再问后端
    const timer = setTimeout(() => {
      void fetchPathTextOutline(obj).then((o) => {
        if (alive && o) setState({ key, outline: o })
      })
    }, 120)
    return () => {
      alive = false
      clearTimeout(timer)
    }
    // obj 的有关字段都在 key 里
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return { outline: state?.outline ?? null, fresh: state?.key === key }
}

export function PathTextView({ obj, hit = 'none' }: { obj: PathTextObject; hit?: 'stroke' | 'none' }) {
  const zoom = useViewportStore((s) => s.zoom)
  const w = Math.max(mmToWorld(obj.w), 0.001)
  const h = Math.max(mmToWorld(obj.h), 0.001)
  const { outline } = usePathTextOutline(obj)
  const id = `ptext-${useId().replace(/:/g, '')}`
  const d = pathD(pathGeometry({ subpaths: obj.subpaths }, w, h, 0).fill)
  const sizeW = mmToWorld(obj.sizePt * MM_PER_PT)
  const s = mmToWorld(1)
  const editing = useUiStore((st) => st.editingTextId === obj.id)

  return (
    <>
    {editing && <PathTextInput obj={obj} w={w} h={h} sizeW={sizeW} />}
    <svg
      data-path-text=""
      className="pointer-events-none absolute left-0 top-0 overflow-visible"
      width={Math.max(w, 1)}
      height={Math.max(h, 1)}
    >
      {outline ? (
        // 就地输入时字淡下去：看的是输入框里要写的字，提交后后端排好再换上
        <g data-path-text-glyphs="" transform={`scale(${s})`} opacity={editing ? 0.25 : undefined}>
          {outline.paths.map((p, i) => (
            <g key={i} transform={`translate(${p.x} ${p.y})`}>
              <PathBody obj={p} sw={(p.strokePt ?? 0) * MM_PER_PT} w={p.w} h={p.h} />
            </g>
          ))}
        </g>
      ) : (
        d && <FallbackText obj={obj} id={id} d={d} sizeW={sizeW} w={w} h={h} />
      )}
      {hit !== 'none' && d && (
        <path
          data-hit-path=""
          d={d}
          fill="none"
          stroke="transparent"
          strokeWidth={Math.max(hitStrokeWidth(0, zoom, 0), sizeW * 1.6)}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ pointerEvents: 'stroke' }}
        />
      )}
    </svg>
    </>
  )
}

/**
 * 就地输入：起点处一个单行输入框（随对象一起转），Enter / 失焦提交成一条历史，Esc 放弃。字在路径上的样子等提交后
 * 后端排好再换上——输入框里看的是要写的字，不是排版结果。
 */
function PathTextInput({ obj, w, h, sizeW }: { obj: PathTextObject; w: number; h: number; sizeW: number }) {
  const ref = useRef<HTMLInputElement>(null)
  const done = useRef(false)
  const [value, setValue] = useState(obj.text)
  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])
  const at = startHandle(obj, w, h) ?? { x: 0, y: 0, angle: 0 }
  const finish = (save: boolean) => {
    if (done.current) return
    done.current = true
    if (save) commitPathText(obj.id, value)
    useUiStore.getState().setEditingText(null)
  }
  const fontPx = Math.max(sizeW, 10)
  return (
    <input
      ref={ref}
      data-path-text-input=""
      aria-label={obj.text}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter') finish(true)
        else if (e.key === 'Escape') finish(false)
      }}
      onBlur={() => finish(true)}
      className="absolute rounded-sm border border-sel bg-surface px-1 text-ink-1 outline-none"
      style={{
        left: at.x,
        top: at.y - fontPx * 1.6,
        fontSize: fontPx,
        lineHeight: 1.2,
        width: Math.max(80, value.length * fontPx * 0.62 + 16),
        pointerEvents: 'auto',
      }}
    />
  )
}

/** 等后端时的顶替：浏览器自己的 `<textPath>`（字形与间距可能和导出差一点，结果回来就换掉） */
function FallbackText({ obj, id, d, sizeW, w, h }: { obj: PathTextObject; id: string; d: string; sizeW: number; w: number; h: number }) {
  const a = arcPathOf(obj, w, h)
  const fd = obj.flip ? pathD(reversedSegs(a.pts)) : d
  const anchor = obj.textAlign === 'center' ? 'middle' : obj.textAlign === 'end' ? 'end' : 'start'
  return (
    <>
      <defs>
        <path id={id} d={fd} />
      </defs>
      <text
        data-path-text-fallback=""
        fill={obj.color}
        fontSize={sizeW}
        fontWeight={obj.bold ? 700 : 400}
        fontStyle={obj.italic ? 'italic' : undefined}
        fontFamily={obj.fontFamily ?? 'serif'}
      >
        <textPath href={`#${id}`} startOffset={`${clampOffset(obj.startOffset) * 100}%`} textAnchor={anchor}>
          {obj.text.replace(/\s+/g, ' ')}
        </textPath>
      </text>
    </>
  )
}

function reversedSegs(pts: [number, number][]): PathSeg[] {
  return pts.map((p, i): PathSeg => (i === 0 ? ['M', p[0], p[1]] : ['L', p[0], p[1]]))
}
