import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { forgetScriptAnswer, probeScript, resetEngineFeatures, updateScriptAnswer } from './api'
import { setCurrentProjectId } from './session'

beforeEach(() => { resetEngineFeatures(); setCurrentProjectId('answer-project') })
afterEach(() => { vi.restoreAllMocks(); setCurrentProjectId(null) })

it.each(['rc_a', 'rc_b', null])('management and rerun HTTP payloads retain config %s', async (runConfig) => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => new Response(
    String(url).includes('/api/version') ? '{"features":["script-argv"]}' : '{}',
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  ))
  await updateScriptAnswer('s.py', 1, 'chosen', runConfig)
  await forgetScriptAnswer('s.py', 1, runConfig)
  await probeScript('s.py', undefined, { run_config: runConfig })
  const calls = fetch.mock.calls.filter((call) => !String(call[0]).includes('/api/version'))
  expect(calls.map((call) => JSON.parse(String(call[1]?.body)))).toEqual([
    { script: 's.py', index: 1, answer: 'chosen', run_config: runConfig },
    { script: 's.py', index: 1, forget: true, run_config: runConfig },
    { script: 's.py', run_config: runConfig },
  ])
  for (const call of calls) {
    expect(new Headers(call[1]?.headers).get('X-Tavotto-Project')).toBe('answer-project')
  }
})
