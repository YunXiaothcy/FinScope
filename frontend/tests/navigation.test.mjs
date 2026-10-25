import assert from 'node:assert/strict'
import test from 'node:test'
import {
  isNavigationActive,
  workspaceNavigation,
} from '../src/utils/navigation.ts'

test('个人和商户空间的导航均不重复且始终指向当前空间', () => {
  for (const business of [false, true]) {
    for (const base of ['/workspaces/first', '/workspaces/second']) {
      const links = workspaceNavigation(base, business)
      assert.equal(new Set(links.map((item) => item.to)).size, links.length)
      assert.equal(
        links.some((item) => item.id === 'business'),
        business,
      )
      assert.ok(links.every((item) => item.to.startsWith(`${base}/`)))
      for (const item of links) {
        assert.equal(
          links.filter((link) => isNavigationActive(item.to, link.to)).length,
          1,
        )
      }
    }
  }
})

test('选中状态支持子页面但不跨空间或误匹配相似路径', () => {
  const target = '/workspaces/first/finance'
  assert.ok(isNavigationActive(target, target))
  assert.ok(isNavigationActive(`${target}/detail`, target))
  assert.equal(isNavigationActive(`${target}-archive`, target), false)
  assert.equal(isNavigationActive('/workspaces/second/finance', target), false)
  assert.equal(isNavigationActive('/workspaces/first/analysis', target), false)
})
