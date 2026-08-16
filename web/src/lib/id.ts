const ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'

/** 对象 id：短、可读、无需依赖 crypto */
export function newId(prefix = 'o'): string {
  let s = ''
  for (let i = 0; i < 8; i++) s += ALPHABET[(Math.random() * ALPHABET.length) | 0]
  return `${prefix}_${s}`
}
