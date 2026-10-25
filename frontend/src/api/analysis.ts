import { apiRequest } from './client'

export interface AnalysisFact {
  id: string
  code: string
  label: string
  value: string
  unit: string
  start_date: string
  end_date: string
  formula: string
}
export interface Analysis {
  id: string
  status: 'local' | 'running' | 'complete' | 'fallback' | 'expired'
  intent: string
  title: string
  provider: string
  target: string
  created_at: string
  expires_at: string
  facts: AnalysisFact[]
  observations: string[]
  advice: string[]
  notices: string[]
  tools: Record<string, unknown>[]
  cloud_payload: Record<string, unknown>
  snapshot_hash: string
}
export interface AnalysisCapabilities {
  ai_enabled: boolean
  target: string
  retention_days: number
  disclosure: string
}
export interface Question {
  question: string
  start_date?: string
  end_date?: string
}
const base = (id: string) => `/workspaces/${id}/analysis`
export const getAnalysisCapabilities = (id: string) =>
  apiRequest<AnalysisCapabilities>(`${base(id)}/capabilities`)
export const listAnalyses = (id: string) => apiRequest<Analysis[]>(base(id))
export const getAnalysis = (id: string, recordId: string) =>
  apiRequest<Analysis>(`${base(id)}/${recordId}`)
export const askAnalysis = (
  id: string,
  payload: Question,
  key: string,
  csrf: string,
  signal: AbortSignal,
) =>
  apiRequest<Analysis>(
    base(id),
    {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: { 'Idempotency-Key': key },
      signal,
    },
    csrf,
  )
export const clearAnalysis = (id: string, recordId: string, csrf: string) =>
  apiRequest<void>(`${base(id)}/${recordId}`, { method: 'DELETE' }, csrf)
