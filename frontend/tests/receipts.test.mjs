import assert from 'node:assert/strict'
import test from 'node:test'
import { receiptDraft } from '../src/utils/receipts.ts'

test('receipt candidates preserve exact amounts and leave unknown direction and account blank', () => {
  const result = receiptDraft({
    reviewed_payload: null,
    candidates: {
      amount: '9999999999999999.99',
      date: '2026-10-24',
      time: null,
      transaction_type: null,
      merchant_name: '候选商户',
    },
  })
  assert.equal(result.amount, '9999999999999999.99')
  assert.equal(result.type, '')
  assert.equal(result.account_id, '')
  assert.equal(result.category_id, '')
  assert.equal(result.localTime, '2026-10-24T00:00')
})
test('server reviewed draft takes priority over OCR and uses Hong Kong time', () => {
  const result = receiptDraft({
    reviewed_payload: {
      transaction: {
        type: 'income',
        amount: '0.10',
        account_id: 'a',
        category_id: 'c',
        occurred_at: '2026-10-23T16:30:00Z',
        merchant_name: '人工核对',
        description: '已修正',
      },
    },
    candidates: {
      amount: '999.99',
      date: '2020-01-01',
      merchant_name: '错误候选',
    },
  })
  assert.equal(result.amount, '0.10')
  assert.equal(result.localTime, '2026-10-24T00:30:00')
  assert.equal(result.merchant_name, '人工核对')
  assert.equal(result.type, 'income')
})
