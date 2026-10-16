import type { NativeSessionInfo } from '@/lib/api'

export interface NativeExecutionResponse {
  acceptSession: (session: NativeSessionInfo) => void
  acceptError: (error: unknown) => void
}

let capture: ((sessionId: string) => NativeExecutionResponse) | undefined

/** The session owner supplies admission; input UI never imports the owner back. */
export function registerNativeExecutionCapture(owner: (sessionId: string) => NativeExecutionResponse): void {
  capture = owner
}

export function captureNativeExecutionResponse(sessionId: string): NativeExecutionResponse {
  return capture?.(sessionId) ?? { acceptSession: () => {}, acceptError: () => {} }
}
