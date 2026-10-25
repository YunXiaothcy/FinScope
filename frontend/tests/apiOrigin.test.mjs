import assert from 'node:assert/strict'
import test from 'node:test'
import { apiBase } from '../src/utils/apiOrigin.ts'

test('API 配置仅接受同源前缀，阻止财务请求和 CSRF 令牌发往外站', () => {
  assert.equal(apiBase(undefined), '/api/v1')
  assert.equal(apiBase('/api/v1'), '/api/v1')
  for (const value of [
    'https://evil.example/api/v1',
    '//evil.example',
    '\\\\evil.example',
    '/api/v1?target=x',
    '/other',
  ]) {
    assert.throws(() => apiBase(value))
  }
})
