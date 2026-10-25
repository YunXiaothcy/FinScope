/** All authenticated financial traffic stays on the application's origin. */
export function apiBase(value: string | undefined): string {
  if (value && value !== '/api/v1') {
    throw new Error('API 地址必须为同源 /api/v1，请通过服务端反向代理连接后端')
  }
  return '/api/v1'
}
