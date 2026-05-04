// localStorage/SessionStorage 中存储认证信息的 key
const AUTH_STORAGE_KEY = 'mo_stock_basic_auth'

/**
 * 认证会话数据结构
 */
export interface AuthSession {
  authorization: string   // Basic Auth 字符串，格式：'Basic base64(username:password)'
  username: string        // 用户名
}

/**
 * 将字符串转换为 Base64 编码（兼容浏览器环境）
 * @param value 待编码的字符串
 * @returns Base64 编码结果
 */
function encodeBase64(value: string): string {
  const bytes = new TextEncoder().encode(value)
  let binary = ''
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return window.btoa(binary)
}

/**
 * 构建 Basic Auth 字符串
 * @param username 用户名
 * @param password 密码
 * @returns 格式化的 Authorization 头值，如 'Basic dXNlcjpwYXNz'
 */
export function buildBasicAuth(username: string, password: string): string {
  return `Basic ${encodeBase64(`${username}:${password}`)}`
}

/**
 * 从 localStorage 或 sessionStorage 读取认证会话
 * @returns 认证会话对象，若无则返回 null
 */
export function getAuthSession(): AuthSession | null {
  const stored = localStorage.getItem(AUTH_STORAGE_KEY) || sessionStorage.getItem(AUTH_STORAGE_KEY)
  if (!stored) return null

  try {
    return JSON.parse(stored) as AuthSession
  } catch {
    // JSON 解析失败说明存储数据损坏，清除它
    clearAuthSession()
    return null
  }
}

/**
 * 检查用户是否已登录（认证会话是否存在）
 */
export function isLoggedIn(): boolean {
  return getAuthSession() !== null
}

/**
 * 保存认证会话到存储
 * @param username 用户名
 * @param authorization Basic Auth 字符串
 * @param remember 是否记住登录（true=localStorage 持久化，false=sessionStorage 会话级）
 *
 * 注意：同时操作两个存储目标——记住登录时同时写 localStorage 并清除 sessionStorage，
 * 否则相反——这样可以确保切换"记住"状态时不会残留旧数据
 */
export function setAuthSession(username: string, authorization: string, remember: boolean): void {
  const payload = JSON.stringify({ username, authorization })
  const target = remember ? localStorage : sessionStorage
  const staleTarget = remember ? sessionStorage : localStorage

  target.setItem(AUTH_STORAGE_KEY, payload)
  staleTarget.removeItem(AUTH_STORAGE_KEY)
}

/**
 * 清除所有存储中的认证会话（退出登录）
 */
export function clearAuthSession(): void {
  localStorage.removeItem(AUTH_STORAGE_KEY)
  sessionStorage.removeItem(AUTH_STORAGE_KEY)
}