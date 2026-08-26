/**
 * 诊断用的**不可逆短 hash**（ADR 0016 §5）。
 *
 * 要回答的问题只有一个：「这两个状态是不是同一个状态」。为此完全不需要知道
 * 内容——document / panel / file / 渲染变体 / preview 会话 / 布局版本一律换成
 * 一个带前缀的短串，原值一个字节都不进诊断包。
 *
 * 三条设计约束：
 *   * **同步**。调用点在 commit / undo / 渲染回调里，`crypto.subtle.digest`
 *     是异步的，用它等于把诊断塞进每一条业务路径的 await 链。
 *   * **无依赖**。为了几个诊断标识引一整个 crypto 库不划算。
 *   * **不是安全原语**。它不做认证、不防篡改，只做同一性判断。
 *
 * **每会话一个随机 salt**。要求只说「同一次会话中稳定」，salt 完全满足，并且
 * 额外买到两件事：① 12 hex 的非加密 hash 对**已知候选**是可暴力的——没有
 * salt，拿一本常见路径字典去撞用户的 file hash 是可行的；② 两份不同会话的
 * 诊断包无法靠 hash 相互关联，跨包画像不成立。
 */

/** 输出长度（hex 字符）。48 bit 对「一次会话里几十个变体」绰绰有余 */
const HEX_LEN = 12

const FNV_OFFSET = 0x811c9dc5
const FNV_PRIME = 0x01000193
/** 第二轮的 offset basis：黄金比例常数，与第一轮独立 */
const SECOND_OFFSET = 0x9e3779b1

let salt = freshSalt()

function freshSalt(): string {
  try {
    const buf = new Uint32Array(2)
    globalThis.crypto?.getRandomValues?.(buf)
    if (buf[0] || buf[1]) return `${buf[0].toString(36)}${buf[1].toString(36)}`
  } catch {
    /* 没有 Web Crypto（老 jsdom、个别嵌入环境）时退到下面那条 */
  }
  // 退路只要求「本会话内稳定且不可预测性够用」，不要求密码学质量
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

function fold(input: string, seed: number): number {
  let h = seed >>> 0
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i)
    h = Math.imul(h, FNV_PRIME)
  }
  return h >>> 0
}

/**
 * 任意值 → 12 位十六进制。对象走 JSON.stringify——**它只在这里出现**，
 * 结果立刻被折成数字，序列化出来的字符串不会流到任何别的地方。
 */
export function diagnosticHash(value: unknown): string {
  let text: string
  if (typeof value === 'string') text = value
  else if (value == null) text = ` ${String(value)}`
  else {
    try {
      text = JSON.stringify(value) ?? String(value)
    } catch {
      text = String(value) // 循环引用 / BigInt：退到 toString，同一性判断照旧成立
    }
  }
  const salted = `${salt}${text}`
  const a = fold(salted, FNV_OFFSET).toString(16).padStart(8, '0')
  const b = (fold(salted, SECOND_OFFSET) & 0xffff).toString(16).padStart(4, '0')
  return `${a}${b}`.slice(0, HEX_LEN)
}

/** `前缀:hash`。前缀让人一眼看出这个身份是哪一类，不用回头查字段名。 */
export function tagged(prefix: string, value: unknown): string {
  return `${prefix}:${diagnosticHash(value)}`
}

/** 诊断包里合法 hash 的形状——sanitize 的 `hash` kind 用同一条正则把关 */
export const HASH_PATTERN = /^[a-z_]+:[0-9a-f]{8,16}$/

/* ------------------------------ 类别化封装 ------------------------------ */
/* 全部经 tagged()，调用点因此不可能「忘了 hash 直接写原值」——它们手里
   压根没有一个返回原值的函数。 */

/** 文档状态摘要（规范化后的，见 documentDigest） */
export const docHash = (v: unknown): string => tagged('doc', v)
/** 面板 id */
export const panelHash = (v: unknown): string => tagged('panel', v)
/** 素材文件 id。**fileId 可能就是一条路径，绝不许原样进 trace** */
export const fileHash = (v: unknown): string => tagged('file', v)
/** 渲染变体键（`fileId + JSON.stringify(overrides)`，两头都可能带用户内容） */
export const variantHash = (v: unknown): string => tagged('var', v)
/** preview 会话 */
export const previewHash = (v: unknown): string => tagged('prev', v)
/** 布局版本 id。**版本名是用户输入的，只 hash id，名字一个字都不取** */
export const versionHash = (v: unknown): string => tagged('ver', v)
/** 画布对象 id（画布标注等） */
export const objectHash = (v: unknown): string => tagged('obj', v)

/** null 进 null 出的变体 hash：调用点少一层三元表达式，也少一处漏 hash 的机会 */
export const variantHashOrNull = (v: string | null | undefined): string | null =>
  v == null ? null : variantHash(v)

/**
 * 只给测试用：把 salt 钉成一个已知值，好断言「相同输入 → 相同 hash」
 * 与「不同输入 → 不同 hash」。产品代码没有任何一条路径调它。
 */
export function __setDiagnosticSaltForTests(value: string | null): void {
  salt = value ?? freshSalt()
}
