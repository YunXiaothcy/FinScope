import assert from 'node:assert/strict'
import test from 'node:test'
import {
  sampleCSV,
  sourceLabels,
  suggestColumns,
} from '../src/utils/imports.ts'

test('column suggestions use exact known labels and preserve missing required columns', () => {
  const result = suggestColumns([
    ' 日期 ',
    '收支',
    '金额',
    '类别',
    '备注',
    'merchant',
    '订单号',
    '币种',
  ])
  assert.deepEqual(result, {
    date_column: 0,
    amount_column: 2,
    type_column: 1,
    category_column: 3,
    merchant_column: 5,
    description_column: 4,
    external_id_column: 6,
    currency_column: 7,
  })
  assert.equal(suggestColumns(['金额说明']).amount_column, -1)
  assert.equal(suggestColumns(['日期']).category_column, null)
})
test('source labels normalize width, omit empty cells and retain special text as data', () => {
  assert.deepEqual(
    sourceLabels(
      [
        { raw: [' 餐饮 '] },
        { raw: ['餐饮'] },
        { raw: null },
        { raw: ['__proto__'] },
        { raw: ['Ａ'] },
      ],
      0,
    ),
    ['餐饮', '__proto__', 'A'],
  )
  assert.deepEqual(sourceLabels([], null), [])
})
test('sample CSV includes BOM, a duplicate source ID and an invalid amount for review', () => {
  const csv = sampleCSV('2026-10-24')
  assert.equal(csv.charCodeAt(0), 0xfeff)
  assert.equal(csv.match(/DEMO-002/g).length, 2)
  assert.match(csv, /2026-10-24,支出,abc/)
})
