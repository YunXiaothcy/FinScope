import { apiBase } from '../utils/apiOrigin'
const apiBasePath = apiBase(import.meta.env.VITE_API_BASE_PATH)

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message)
  }
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  csrfToken?: string,
): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${apiBasePath}${path}`, {
      ...options,
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error',
      signal: options.signal || AbortSignal.timeout(15000),
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
        ...options.headers,
      },
    })
  } catch {
    throw new ApiError(
      navigator.onLine === false
        ? '当前已离线，请恢复网络后核对提交结果；不要重复手工录入'
        : '无法连接后端，请确认 API 服务已启动',
      0,
    )
  }
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { error?: { message?: string } }
      message = body.error?.message || message
    } catch {
      /* 非 JSON 错误仍显示状态码。 */
    }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
