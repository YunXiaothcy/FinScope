export interface HealthResponse {
  status: 'ok'
}

const apiBasePath = import.meta.env.VITE_API_BASE_PATH || '/api/v1'

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${apiBasePath}/health`, { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(`健康检查失败：HTTP ${response.status}`)
  }
  return (await response.json()) as HealthResponse
}
