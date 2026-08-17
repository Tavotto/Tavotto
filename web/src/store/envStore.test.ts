import { beforeEach, describe, expect, it } from 'vitest'
import { useEnvStore } from './envStore'
import { ENVIRONMENT_CODES } from '@/lib/api'

/**
 * 渲染环境状态。这里盯的是**界面据以决定给什么出口**的那几个字段——
 * 判错一个，用户看到的就是完全错误的引导：明明什么都不缺却被劝去装 Python，
 * 或者安装包不完整却让他自己去下载几十 MB。
 */

const BUNDLED = {
  ok: true,
  python: 'C:\\Program Files\\Magplot\\_internal\\runtime\\python.exe',
  source: 'bundled',
  matplotlib: '3.11.1',
  managed: false,
  bundled: true,
  state: 'idle',
  runtime: {
    present: true, valid: true, expected: true, python: '3.13.15',
    packages: { numpy: '2.5.2', matplotlib: '3.11.1', scipy: '1.18.0' },
    build: { id: 'ci-42' }, code: '', error: null,
  },
}

const BROKEN_DESKTOP = {
  ok: false,
  python: null,
  source: '',
  matplotlib: null,
  managed: false,
  bundled: false,
  state: 'idle',
  code: 'bundled_runtime_missing',
  can_install: false,
  runtime: {
    present: false, valid: false, expected: true, python: null,
    packages: {}, build: {}, code: 'bundled_runtime_missing',
    error: '安装包里的内置渲染环境不见了',
  },
}

const NO_PYTHON_SOURCE_MODE = {
  ok: false,
  python: null,
  source: '',
  matplotlib: null,
  managed: false,
  bundled: false,
  state: 'idle',
  code: 'no_worker_python',
  can_install: true,
  base_python: '/usr/bin/python3',
  runtime: {
    present: false, valid: false, expected: false, python: null,
    packages: {}, build: {}, code: '', error: null,
  },
}

let reply: unknown = BUNDLED

globalThis.fetch = (async (url: unknown) => {
  if (String(url).includes('/api/engine/environment')) {
    return new Response(JSON.stringify(reply), { status: 200 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

beforeEach(() => {
  useEnvStore.setState({ env: null, log: '', installing: false })
})

describe('envStore', () => {
  it('Windows 桌面版：内置环境可用时报 bundled，且没有任何要用户动手的入口', async () => {
    reply = BUNDLED
    await useEnvStore.getState().refresh()
    const env = useEnvStore.getState().env!
    expect(env.ok).toBe(true)
    expect(env.source).toBe('bundled')
    expect(env.bundled).toBe(true)
    // can_install 一旦为真，界面就会冒出「自动安装」按钮——什么都不缺的时候
    // 那是纯粹的噪音，也会让人以为出了问题
    expect(env.can_install).toBeFalsy()
  })

  it('内置包版本要能报出来（设置页展示、诊断包也靠它）', async () => {
    reply = BUNDLED
    await useEnvStore.getState().refresh()
    const rt = useEnvStore.getState().env!.runtime
    expect(rt.python).toBe('3.13.15')
    expect(rt.packages.numpy).toBe('2.5.2')
    expect(Object.keys(rt.packages)).toContain('scipy')
  })

  it('内置环境缺失：给的是「重装」而不是「自己去装 Python」', async () => {
    reply = BROKEN_DESKTOP
    await useEnvStore.getState().refresh()
    const env = useEnvStore.getState().env!
    // expected=true 是界面区分这两种局面的依据：本该带却没带 = 我们的包有问题
    expect(env.runtime.expected).toBe(true)
    expect(env.code).toBe('bundled_runtime_missing')
    expect(env.can_install).toBe(false)
  })

  it('源码 / pip 安装模式不受影响：仍然提供自动安装', async () => {
    reply = NO_PYTHON_SOURCE_MODE
    await useEnvStore.getState().refresh()
    const env = useEnvStore.getState().env!
    expect(env.runtime.expected).toBe(false)
    expect(env.can_install).toBe(true)
    expect(env.code).toBe('no_worker_python')
  })

  it('探测失败不打扰用户（真要渲染时自然会报错）', async () => {
    const original = globalThis.fetch
    globalThis.fetch = (async () => {
      throw new Error('offline')
    }) as typeof fetch
    await useEnvStore.getState().refresh()
    expect(useEnvStore.getState().env).toBeNull()
    globalThis.fetch = original
  })

  it('安装进度推到 done 时结束 installing 状态', async () => {
    reply = BUNDLED
    useEnvStore.setState({ installing: true })
    useEnvStore.getState().onProgress({ state: 'running', log: '装着…', error: null })
    expect(useEnvStore.getState().installing).toBe(true)
    useEnvStore.getState().onProgress({ state: 'done', log: '✓', error: null })
    expect(useEnvStore.getState().installing).toBe(false)
  })
})

describe('环境类错误码', () => {
  it('缺件类的错误码都在清单里——界面据此给出口而不是甩 traceback', () => {
    expect(ENVIRONMENT_CODES).toContain('no_worker_python')
    expect(ENVIRONMENT_CODES).toContain('bundled_runtime_missing')
    expect(ENVIRONMENT_CODES).toContain('bundled_runtime_invalid')
    expect(ENVIRONMENT_CODES).toContain('missing_dependency')
  })
})
