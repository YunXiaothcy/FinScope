import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { requestKey } from '../src/utils/requestKey.ts'
import { bindPageRefresh } from '../src/utils/pageRefresh.ts'
import { registerOnlineWorker } from '../src/utils/pwa.ts'

const workerSource = await readFile(
  new URL('../public/sw.js', import.meta.url),
  'utf8',
)
function worker(fetch = async () => new Response('network')) {
  const handlers = {}
  const removed = []
  const self = {
    location: { origin: 'https://finscope.example' },
    clients: { claim: async () => {} },
    addEventListener: (name, handler) => {
      handlers[name] = handler
    },
    skipWaiting: () => {
      self.activated = true
    },
  }
  runInNewContext(workerSource, {
    self,
    URL,
    Response,
    fetch,
    caches: {
      keys: async () => ['finscope-old', 'another-app'],
      delete: async (name) => {
        removed.push(name)
      },
    },
  })
  return { handlers, self, removed }
}

test('service worker never intercepts API, receipt images, writes or external origins', async () => {
  let calls = 0
  const { handlers } = worker(async () => {
    calls++
    return new Response('network')
  })
  for (const request of [
    {
      url: 'https://finscope.example/api/v1/finance',
      method: 'GET',
      mode: 'navigate',
    },
    {
      url: 'https://finscope.example/api/v1/receipts/secret/image',
      method: 'GET',
      mode: 'same-origin',
    },
    { url: 'https://finscope.example/api', method: 'GET', mode: 'navigate' },
    {
      url: 'https://finscope.example/anywhere',
      method: 'POST',
      mode: 'navigate',
    },
    { url: 'https://elsewhere.example/page', method: 'GET', mode: 'navigate' },
    {
      url: 'https://finscope.example/assets/main.js',
      method: 'GET',
      mode: 'same-origin',
    },
  ])
    handlers.fetch({
      request,
      respondWith() {
        assert.fail('Sensitive requests must stay with browser network')
      },
    })
  assert.equal(calls, 0)
})

test('offline navigation returns only a generic no-store page, no ledger data or queue', async () => {
  const { handlers } = worker(async () => {
    throw new Error('offline')
  })
  let response
  handlers.fetch({
    request: {
      url: 'https://finscope.example/workspaces/private-id/finance',
      method: 'GET',
      mode: 'navigate',
    },
    respondWith(value) {
      response = value
    },
  })
  const result = await response
  assert.equal(result.status, 503)
  assert.equal(result.headers.get('Cache-Control'), 'no-store')
  assert.match(await result.text(), /需要网络连接/)
  assert.equal(handlers.sync, undefined)
  assert.equal(handlers.periodicsync, undefined)
})

test('worker clears only FinScope caches and activates an update only by explicit message', async () => {
  const { handlers, self, removed } = worker()
  let done
  handlers.activate({
    waitUntil(value) {
      done = value
    },
  })
  await done
  assert.deepEqual(removed, ['finscope-old'])
  assert.equal(self.activated, undefined)
  handlers.message({ data: { type: 'unrelated' } })
  assert.equal(self.activated, undefined)
  handlers.message({ data: { type: 'ACTIVATE_UPDATE' } })
  assert.equal(self.activated, true)
})

test('worker registration is limited to secure production and uses fresh update checks', async () => {
  const calls = []
  const registration = { waiting: null, addEventListener() {} }
  const container = {
    register: async (...args) => {
      calls.push(args)
      return registration
    },
  }
  assert.equal(
    await registerOnlineWorker(false, true, container, () => {}),
    null,
  )
  assert.equal(
    await registerOnlineWorker(true, false, container, () => {}),
    null,
  )
  assert.equal(
    await registerOnlineWorker(true, true, undefined, () => {}),
    null,
  )
  assert.equal(calls.length, 0)
  assert.equal(
    await registerOnlineWorker(true, true, container, () => {}),
    registration,
  )
  assert.deepEqual(calls[0], ['/sw.js', { scope: '/', updateViaCache: 'none' }])
})

test('visibility, reconnect and focus refresh without duplicating work or writing data', () => {
  const target = new EventTarget(),
    documentTarget = new EventTarget()
  let visible = false,
    allowed = true,
    time = 0,
    refreshes = 0
  const cleanup = bindPageRefresh(
    target,
    documentTarget,
    () => visible,
    () => allowed,
    () => {
      refreshes++
    },
    () => time,
  )
  target.dispatchEvent(new Event('focus'))
  assert.equal(refreshes, 0)
  visible = true
  target.dispatchEvent(new Event('online'))
  target.dispatchEvent(new Event('focus'))
  assert.equal(refreshes, 1)
  time += 1500
  allowed = false
  documentTarget.dispatchEvent(new Event('visibilitychange'))
  assert.equal(refreshes, 1)
  allowed = true
  documentTarget.dispatchEvent(new Event('visibilitychange'))
  assert.equal(refreshes, 2)
  cleanup()
  time += 1500
  target.dispatchEvent(new Event('finscope:refresh'))
  assert.equal(refreshes, 2)
})

test('HTTP LAN still creates cryptographically random idempotency keys without randomUUID', () => {
  const source = {
    getRandomValues: (array) => {
      array.set(Array.from({ length: 16 }, (_, index) => index))
      return array
    },
  }
  assert.equal(requestKey(source), '000102030405060708090a0b0c0d0e0f')
  assert.match(requestKey(), /^[a-f0-9-]{32,36}$/)
})

test('manifest opens the workspace selector and includes all standard icon sizes', async () => {
  const manifest = JSON.parse(
    await readFile(
      new URL('../public/manifest.webmanifest', import.meta.url),
      'utf8',
    ),
  )
  assert.equal(manifest.start_url, '/')
  assert.equal(manifest.display, 'standalone')
  for (const size of ['192x192', '512x512'])
    assert.ok(
      manifest.icons.some(
        (icon) => icon.sizes === size && icon.purpose === 'any',
      ),
    )
  for (const icon of manifest.icons) {
    const png = await readFile(new URL('../public' + icon.src, import.meta.url))
    const [width, height] = icon.sizes.split('x').map(Number)
    assert.equal(png.readUInt32BE(16), width)
    assert.equal(png.readUInt32BE(20), height)
  }
})
