import { create } from 'zustand'
import { discardAiRefImage, uploadAiRefImage } from '@/lib/api'
import { pickImageFiles, prepareRefImage, REF_IMAGE_MAX } from '@/lib/aiRefImages'

/**
 * 助手输入框里附的参考图（ADR 0175）。按「项目 × 面板文件」分槽，与草稿同一把键：切页签 / 切面板再回来还在。
 * 只活在页面内存里：图本身一加进来就传给后端暂存（`/api/ai/ref-images`），这里只记后端给的 id 和一张几 KB 的缩略图；
 * 刷新页面就没了（后端的暂存一小时后自己清）。发送时由 `settle` 等还在上传的图收尾，再把 id 交给 `aiStore.start`。
 */
export interface RefItem {
  /** 本地键（上传前就有，列表渲染与去除都认它） */
  key: string
  name: string
  thumb: string
  status: 'uploading' | 'ready'
  /** 后端暂存 id；`status === 'ready'` 才有 */
  serverId?: string
}

/** 加图的结局：只给张数，说什么话由调用方（界面）定。没有「失败」这一档会让流程停下的结果 */
export interface AddSummary {
  /** 超过 4 张被丢掉的 */
  overflow: number
  /** 不是图片的 */
  notImage: number
  /** 浏览器解不了的（多半是 HEIC） */
  undecodable: number
  /** 传给后端没成功的 */
  uploadFailed: number
}

interface RefState {
  byTarget: Record<string, RefItem[]>
  add: (target: string, pj: string | null, files: readonly File[]) => Promise<AddSummary>
  remove: (target: string, pj: string | null, key: string) => void
  /** 等这个槽里还在上传的图落定，回已经就绪的（发送前调） */
  settle: (target: string) => Promise<RefItem[]>
  /** 发送交给了提交之后：清槽，**不**删后端暂存（图已经被任务拿走 / 提交自己持有 id） */
  release: (target: string) => void
}

let seq = 0
/** 上传中的承诺：`settle` 等它们 */
const inflight = new Map<string, Promise<void>>()

export const refTargetKey = (project: string | null, fileId: string | null): string => `${project ?? ''}\u0000${fileId ?? ''}`

export const useAiRefStore = create<RefState>((set, get) => ({
  byTarget: {},

  add: async (target, pj, files) => {
    const room = REF_IMAGE_MAX - (get().byTarget[target]?.length ?? 0)
    const { take, overflow, notImage } = pickImageFiles(files, room)
    const summary: AddSummary = { overflow, notImage, undecodable: 0, uploadFailed: 0 }
    await Promise.all(
      take.map(async (file) => {
        const prepared = await prepareRefImage(file)
        if (!prepared.ok) {
          summary.undecodable += 1
          return
        }
        // 解码是异步的：等的途中别的图可能已经占满了名额
        if ((get().byTarget[target]?.length ?? 0) >= REF_IMAGE_MAX) {
          summary.overflow += 1
          return
        }
        const key = `ref${++seq}`
        const item: RefItem = { key, name: prepared.image.name, thumb: prepared.image.thumb, status: 'uploading' }
        set((s) => ({ byTarget: { ...s.byTarget, [target]: [...(s.byTarget[target] ?? []), item] } }))
        const job = uploadAiRefImage(prepared.image.blob, pj).then(
          (res) => {
            // 上传期间用户已经点 × 去掉了：后端那份也删掉
            const still = get().byTarget[target]?.some((x) => x.key === key)
            if (!still) {
              void discardAiRefImage(res.id, pj).catch(() => undefined)
              return
            }
            set((s) => ({
              byTarget: {
                ...s.byTarget,
                [target]: (s.byTarget[target] ?? []).map((x) => (x.key === key ? { ...x, status: 'ready', serverId: res.id } : x)),
              },
            }))
          },
          () => {
            summary.uploadFailed += 1
            set((s) => ({ byTarget: { ...s.byTarget, [target]: (s.byTarget[target] ?? []).filter((x) => x.key !== key) } }))
          },
        )
        inflight.set(key, job)
        await job
        inflight.delete(key)
      }),
    )
    return summary
  },

  remove: (target, pj, key) => {
    const item = get().byTarget[target]?.find((x) => x.key === key)
    set((s) => ({ byTarget: { ...s.byTarget, [target]: (s.byTarget[target] ?? []).filter((x) => x.key !== key) } }))
    if (item?.serverId) void discardAiRefImage(item.serverId, pj).catch(() => undefined)
  },

  settle: async (target) => {
    const keys = (get().byTarget[target] ?? []).map((x) => x.key)
    await Promise.all(keys.map((k) => inflight.get(k)).filter((p): p is Promise<void> => !!p))
    return (get().byTarget[target] ?? []).filter((x) => x.status === 'ready' && x.serverId)
  },

  release: (target) =>
    set((s) => {
      const { [target]: _gone, ...rest } = s.byTarget
      return { byTarget: rest }
    }),
}))
