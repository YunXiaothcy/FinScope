import type { Receipt } from '../api/receipts'
import { localDateTime } from './format.ts'

export function receiptDraft(receipt: Receipt) {
  const saved = receipt.reviewed_payload?.transaction
  const candidate = receipt.candidates
  return {
    account_id: saved?.account_id || '',
    category_id: saved?.category_id || '',
    type: (saved?.type || candidate?.transaction_type || '') as
      '' | 'income' | 'expense',
    amount: saved?.amount || candidate?.amount || '',
    localTime: saved
      ? localDateTime(saved.occurred_at)
      : candidate?.date
        ? `${candidate.date}T${candidate.time || '00:00'}`
        : '',
    merchant_name: saved?.merchant_name || candidate?.merchant_name || '',
    description: saved?.description || '票据入账',
    currency_hint_corrected:
      receipt.reviewed_payload?.currency_hint_corrected || false,
  }
}
