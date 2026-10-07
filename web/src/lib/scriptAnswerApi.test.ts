import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { forgetScriptAnswer, probeScript, updateScriptAnswer } from './api'
import { setCurrentProjectId } from './session'

beforeEach(() => setCurrentProjectId('answer-project'))
afterEach(() => { vi.restoreAllMocks(); setCurrentProjectId(null) })

it.each(['rc_a', 'rc_b', null])('management and rerun HTTP payloads retain config %s', async (runConfig) => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }))
  await updateScriptAnswer('s.py', 1, 'chosen', runConfig)
  await forgetScriptAnswer('s.py', 1, runConfig)
  await probeScript('s.py', undefined, { run_config: runConfig })
  expect(fetch.mock.calls.map((call) => JSON.parse(String(call[1]?.body)))).toEqual([
    { script: 's.py', index: 1, answer: 'chosen', run_config: runConfig },
    { script: 's.py', index: 1, forget: true, run_config: runConfig },
    { script: 's.py', run_config: runConfig },
  ])
  for (const call of fetch.mock.calls) {
    expect(new Headers(call[1]?.headers).get('X-Tavotto-Project')).toBe('answer-project')
  }
})
