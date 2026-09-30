import { useCallback, useRef } from 'react'

/**
 * 「同一件事在路上时，再按一次什么都不做」——**同步**的在途标记（Codex #679）。
 *
 * 忙态是 React state，要等下一次渲染才进闭包：第一次请求还没回来时连按两下回车，
 * 第二下读到的仍是旧的 `busy === false`，于是同一个命名节点被 POST 两次（后端不去重，
 * 两个都是受保护的命名节点，还吃掉字节预算）。ref 在调用的那一刻就改，挡得住。
 *
 * 按 `key` 记（调用方传时间线上下文）：A 那一次还在路上时换到 B，B 自己的那一次照常
 * 能发——在途的是 A 的请求，不该把 B 锁住。抽屉的「存为命名节点」与顶栏命名浮层共用这一份。
 */
export function useInFlight() {
  const keys = useRef(new Set<string>())
  return useCallback(async (key: string, run: () => Promise<void>): Promise<void> => {
    if (keys.current.has(key)) return
    keys.current.add(key)
    try {
      await run()
    } finally {
      keys.current.delete(key)
    }
  }, [])
}
