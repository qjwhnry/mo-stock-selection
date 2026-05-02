const AUTH_STORAGE_KEY = 'mo_stock_basic_auth'

export interface AuthSession {
  authorization: string
  username: string
}

function encodeBase64(value: string): string {
  const bytes = new TextEncoder().encode(value)
  let binary = ''
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return window.btoa(binary)
}

export function buildBasicAuth(username: string, password: string): string {
  return `Basic ${encodeBase64(`${username}:${password}`)}`
}

export function getAuthSession(): AuthSession | null {
  const stored = localStorage.getItem(AUTH_STORAGE_KEY) || sessionStorage.getItem(AUTH_STORAGE_KEY)
  if (!stored) return null

  try {
    return JSON.parse(stored) as AuthSession
  } catch {
    clearAuthSession()
    return null
  }
}

export function isLoggedIn(): boolean {
  return getAuthSession() !== null
}

export function setAuthSession(username: string, authorization: string, remember: boolean): void {
  const payload = JSON.stringify({ username, authorization })
  const target = remember ? localStorage : sessionStorage
  const staleTarget = remember ? sessionStorage : localStorage

  target.setItem(AUTH_STORAGE_KEY, payload)
  staleTarget.removeItem(AUTH_STORAGE_KEY)
}

export function clearAuthSession(): void {
  localStorage.removeItem(AUTH_STORAGE_KEY)
  sessionStorage.removeItem(AUTH_STORAGE_KEY)
}
