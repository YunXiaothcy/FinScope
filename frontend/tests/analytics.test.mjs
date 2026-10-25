import assert from 'node:assert/strict'
import test from 'node:test'
import {
  cents,
  positiveCategories,
  presetPeriod,
  spendingCategories,
} from '../src/utils/analytics.ts'

test('calendar presets cover year boundaries, leap years and inclusive 90 days', () => {
  assert.deepEqual(presetPeriod('last-month', '2026-01-10'), {
    start: '2025-12-01',
    end: '2025-12-31',
  })
  assert.deepEqual(presetPeriod('last-month', '2024-03-01'), {
    start: '2024-02-01',
    end: '2024-02-29',
  })
  assert.deepEqual(presetPeriod('month', '2026-10-24'), {
    start: '2026-10-01',
    end: '2026-10-24',
  })
  assert.deepEqual(presetPeriod('90-days', '2026-01-01'), {
    start: '2025-10-04',
    end: '2026-01-01',
  })
})

test('category sorting preserves cents beyond Number precision and separates refunds from pie', () => {
  const data = [
    {
      category_id: 'a',
      name: '成本',
      kind: 'cost',
      amount: '9999999999999999.98',
    },
    {
      category_id: 'b',
      name: '费用',
      kind: 'expense',
      amount: '9999999999999999.99',
    },
    { category_id: 'c', name: '退款', kind: 'expense', amount: '-12.34' },
    { category_id: 'd', name: '收入', kind: 'revenue', amount: '9.00' },
    { category_id: 'e', name: '零', kind: 'expense', amount: '0.00' },
  ]
  assert.equal(cents('-0.01'), -1n)
  assert.deepEqual(
    spendingCategories(data).map((item) => item.category_id),
    ['b', 'a', 'e', 'c'],
  )
  assert.deepEqual(
    positiveCategories(data).map((item) => item.category_id),
    ['b', 'a'],
  )
  assert.equal(data[0].category_id, 'a')
})
