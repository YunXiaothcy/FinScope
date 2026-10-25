import assert from 'node:assert/strict'
import test from 'node:test'
import { localDateTime, money, toUTC } from '../src/utils/format.ts'

test('money display keeps all decimal digits without Number conversion', () => {
  assert.equal(money('9999999999999999.99'), '9,999,999,999,999,999.99')
  assert.equal(money('-10.20'), '-10.20')
  assert.equal(money('0.00'), '0.00')
})

test('form times use Hong Kong independently of the computer timezone', () => {
  assert.equal(localDateTime('2026-09-30T16:00:00Z'), '2026-10-01T00:00:00')
  assert.equal(toUTC('2026-10-01T00:00:00'), '2026-09-30T16:00:00.000Z')
})
