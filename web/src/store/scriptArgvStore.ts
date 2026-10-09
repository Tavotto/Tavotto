import { create } from 'zustand'
import type { probeScript, ScriptArgs } from '@/lib/api'

/**
 * 每个脚本的**运行参数草稿**（T03）：用户手动给的精确 token 列表。
 *
 * 纪律：
 * - **token 是数组，不是一串字符**。永远不 `split(' ')` / `join(' ')`：空串、含空格的值、中文、
 *   重复、负数、粘连选项（`--k=v`）、`--`、子命令都是用户的原话，一个不改。
 * - 草稿只在内存里（含密码 / 令牌的值不进 localStorage、不进 store 持久化）。后端按"这一串 token"
 *   登记运行配置并把引用冻结进产物；草稿之后再怎么改，在途的运行和已有的图都不受影响
 *   （`snapshot()` 在**运行开始那一刻**取一份拷贝）。
 * - 空草稿 = 不带参数，请求体里没有 `argv` 字段（旧后端 / 旧行为一字不变）。
 */
export interface ScriptArgvDraft {
  tokens: string[]
  /** 含密码 / 令牌：后端只在内存里保留，重启后需要重新输入 */
  sensitive: boolean
}

interface ScriptArgvStore {
  drafts: Record<string, ScriptArgvDraft>
  setToken: (script: string, index: number, value: string) => void
  /** 整串换掉（T07：表单编辑 / 粘贴命令算出的新 token 列表；表单不另存一份意图） */
  setTokens: (script: string, tokens: readonly string[]) => void
  addToken: (script: string, value?: string) => void
  removeToken: (script: string, index: number) => void
  moveToken: (script: string, index: number, delta: -1 | 1) => void
  setSensitive: (script: string, sensitive: boolean) => void
  clearScript: (script: string) => void
  clear: () => void
}

const EMPTY: ScriptArgvDraft = { tokens: [], sensitive: false }

const update = (
  s: ScriptArgvStore,
  script: string,
  fn: (d: ScriptArgvDraft) => ScriptArgvDraft,
): Pick<ScriptArgvStore, 'drafts'> => ({
  drafts: { ...s.drafts, [script]: fn(s.drafts[script] ?? EMPTY) },
})

export const useScriptArgvStore = create<ScriptArgvStore>((set) => ({
  drafts: {},
  setToken: (script, index, value) =>
    set((s) =>
      update(s, script, (d) =>
        index < 0 || index >= d.tokens.length
          ? d
          : { ...d, tokens: d.tokens.map((t, i) => (i === index ? value : t)) },
      ),
    ),
  setTokens: (script, tokens) => set((s) => update(s, script, (d) => ({ ...d, tokens: [...tokens] }))),
  addToken: (script, value = '') =>
    set((s) => update(s, script, (d) => ({ ...d, tokens: [...d.tokens, value] }))),
  removeToken: (script, index) =>
    set((s) =>
      update(s, script, (d) => ({ ...d, tokens: d.tokens.filter((_, i) => i !== index) })),
    ),
  moveToken: (script, index, delta) =>
    set((s) =>
      update(s, script, (d) => {
        const to = index + delta
        if (index < 0 || index >= d.tokens.length || to < 0 || to >= d.tokens.length) return d
        const tokens = [...d.tokens]
        ;[tokens[index], tokens[to]] = [tokens[to], tokens[index]]
        return { ...d, tokens }
      }),
    ),
  setSensitive: (script, sensitive) => set((s) => update(s, script, (d) => ({ ...d, sensitive }))),
  clearScript: (script) =>
    set((s) => {
      const drafts = { ...s.drafts }
      delete drafts[script]
      return { drafts }
    }),
  clear: () => set({ drafts: {} }),
}))

/**
 * 运行开始那一刻的参数快照（给 `probeScript` 的第三个参数）。空草稿 → `undefined`（不带参数）。
 * 返回的是拷贝：之后再编辑草稿不会改动已经发出去的这一次。
 */
export const snapshotScriptArgs = (script: string): ScriptArgs | undefined => {
  const d = useScriptArgvStore.getState().drafts[script]
  if (!d || d.tokens.length === 0) return undefined
  return { argv: [...d.tokens], sensitive: d.sensitive }
}

/**
 * 按草稿调 `probeScript`：没有参数 → 调用形状与 T03 之前一致（只有脚本名）；有参数 → 第三个参数是**此刻**的拷贝。
 * 旧试运行只有一个入口（`scriptRunStore.run`；T09b 起接入中心委派到它）：参数快照也只在这一处取。
 */
export const probeWithDraft = (probe: typeof probeScript, script: string) => {
  const args = snapshotScriptArgs(script)
  return args ? probe(script, undefined, args) : probe(script)
}
