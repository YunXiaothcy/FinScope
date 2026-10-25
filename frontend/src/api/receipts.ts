import { apiRequest } from './client'
import type { TransactionInput } from './finance'

export interface ReceiptCandidates {
  amount: string | null
  date: string | null
  time: string | null
  merchant_name: string | null
  category_hint: string | null
  transaction_type: 'income' | 'expense' | null
  currency: string | null
  confidence: Record<string, 'unknown' | 'low' | 'medium'>
  warnings: string[]
}
export interface ReceiptSummary {
  id: string
  filename: string
  status: string
  ocr_status: string
  version: number
  created_at: string
  expires_at: string
  transaction_id: string | null
}
export interface ReviewInput {
  version: number
  reviewed: true
  currency: string
  currency_hint_corrected: boolean
  transaction: TransactionInput
}
export interface Receipt extends ReceiptSummary {
  media_type: string
  byte_size: number
  width: number
  height: number
  image_available: boolean
  ocr_provider: string
  ocr_text: string | null
  extraction_provider: string
  candidates: ReceiptCandidates | null
  reviewed_payload: Omit<ReviewInput, 'version'> | null
  notices: string[]
  external_text_preview: string
}
export interface ReceiptCapabilities {
  ai_enabled: boolean
  ai_host: string
  ai_model: string
  ai_target: string
  max_bytes: number
  retention_hours: number
  disclosure: string
}
const path = (id: string, tail = '') => `/workspaces/${id}/receipts${tail}`
export const receiptImageURL = (id: string, receiptId: string) =>
  `${import.meta.env.VITE_API_BASE_PATH || '/api/v1'}${path(id, `/${receiptId}/image`)}`
export const receiptSampleURL = (id: string) =>
  `${import.meta.env.VITE_API_BASE_PATH || '/api/v1'}${path(id, '/sample')}`
export const getReceiptCapabilities = (id: string) =>
  apiRequest<ReceiptCapabilities>(path(id, '/capabilities'))
export const listReceipts = (id: string) =>
  apiRequest<ReceiptSummary[]>(path(id))
export const getReceipt = (id: string, receiptId: string) =>
  apiRequest<Receipt>(path(id, `/${receiptId}`))
export const uploadReceipt = (id: string, file: File, csrf: string) =>
  apiRequest<Receipt>(
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
export const recognizeReceipt = (
  id: string,
  receiptId: string,
  version: number,
  csrf: string,
) =>
  apiRequest<Receipt>(
    path(id, `/${receiptId}/recognize`),
    {
      method: 'POST',
      body: JSON.stringify({ version }),
      signal: AbortSignal.timeout(60000),
    },
    csrf,
  )
export const structureReceipt = (
  id: string,
  receiptId: string,
  input: {
    version: number
    text?: string
    use_ai?: boolean
    consent?: boolean
    consent_target?: string
  },
  csrf: string,
) =>
  apiRequest<Receipt>(
    path(id, `/${receiptId}/structure`),
    {
      method: 'POST',
      body: JSON.stringify(input),
      signal: AbortSignal.timeout(60000),
    },
    csrf,
  )
export const reviewReceipt = (
  id: string,
  receiptId: string,
  input: ReviewInput,
  csrf: string,
) =>
  apiRequest<Receipt>(
    path(id, `/${receiptId}/review`),
    { method: 'PUT', body: JSON.stringify(input) },
    csrf,
  )
export const confirmReceipt = (
  id: string,
  receiptId: string,
  version: number,
  key: string,
  csrf: string,
) =>
  apiRequest<ReceiptSummary>(
    path(id, `/${receiptId}/confirm`),
    {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify({ version, reviewed: true }),
    },
    csrf,
  )
export const clearReceipt = (
  id: string,
  receiptId: string,
  version: number,
  csrf: string,
) =>
  apiRequest<Receipt>(
    path(id, `/${receiptId}/clear`),
    { method: 'POST', body: JSON.stringify({ version }) },
    csrf,
  )
