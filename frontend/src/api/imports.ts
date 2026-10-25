import { apiRequest } from './client'

export interface ImportMapping {
  account_id: string
  source_name: string
  date_column: number
  amount_column: number
  type_column: number | null
  category_column: number | null
  description_column: number | null
  merchant_column: number | null
  external_id_column: number | null
  currency_column: number | null
  fixed_type: 'income' | 'expense'
  date_format: 'ymd' | 'dmy' | 'mdy'
  default_income_category_id: string | null
  default_expense_category_id: string | null
  category_map: Record<string, string>
  type_map: Record<string, 'income' | 'expense'>
}
export interface ImportSummary {
  id: string
  filename: string
  status: string
  version: number
  created_at: string
  expires_at: string
  total: number
  counts: Record<string, number>
}
export interface ImportRow {
  id: string
  row_number: number
  raw: string[] | null
  normalized: null | {
    type: string
    amount: string
    occurred_at: string
    category_name: string
    merchant_name: string
    description: string
    external_id: string
  }
  status: string
  messages: string[]
  transaction_id: string | null
}
export interface ImportBatch extends ImportSummary {
  format: string
  encoding: string
  sheet_name: string
  headers: string[]
  mapping: ImportMapping | null
  rows: ImportRow[]
}
export interface ConfirmInput {
  version: number
  row_ids: string[]
}
const path = (id: string, tail = '') => `/workspaces/${id}/imports${tail}`
export const uploadImport = (id: string, file: File, csrf: string) =>
  apiRequest<ImportBatch>(
    path(id),
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        'X-File-Name': encodeURIComponent(file.name),
      },
      body: file,
      signal: AbortSignal.timeout(60000),
    },
    csrf,
  )
export const listImports = (id: string) => apiRequest<ImportSummary[]>(path(id))
export const getImport = (id: string, batchId: string) =>
  apiRequest<ImportBatch>(path(id, `/${batchId}`))
export const previewImport = (
  id: string,
  batchId: string,
  version: number,
  mapping: ImportMapping,
  csrf: string,
) =>
  apiRequest<ImportBatch>(
    path(id, `/${batchId}/preview`),
    {
      method: 'PUT',
      body: JSON.stringify({ version, mapping }),
      signal: AbortSignal.timeout(60000),
    },
    csrf,
  )
export const confirmImport = (
  id: string,
  batchId: string,
  input: ConfirmInput,
  key: string,
  csrf: string,
) =>
  apiRequest<ImportSummary>(
    path(id, `/${batchId}/confirm`),
    {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify(input),
      signal: AbortSignal.timeout(60000),
    },
    csrf,
  )
export const cancelImport = (
  id: string,
  batchId: string,
  version: number,
  csrf: string,
) =>
  apiRequest<ImportSummary>(
    path(id, `/${batchId}/cancel`),
    { method: 'POST', body: JSON.stringify({ version }) },
    csrf,
  )
