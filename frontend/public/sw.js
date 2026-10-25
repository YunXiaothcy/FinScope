/* FinScope online-only worker v1: no financial or static response caches. */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(async (keys) => {
      await Promise.all(
        keys
          .filter((key) => key.startsWith('finscope-'))
          .map((key) => caches.delete(key)),
      )
      await self.clients.claim()
    }),
  )
})

self.addEventListener('message', (event) => {
  if (event.data?.type === 'ACTIVATE_UPDATE') self.skipWaiting()
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  const url = new URL(request.url)
  // API、图片、静态资源与所有写请求均交还浏览器，绝不离线缓存或重放。
  if (
    request.method !== 'GET' ||
    url.origin !== self.location.origin ||
    url.pathname === '/api' ||
    url.pathname.startsWith('/api/')
  )
    return
  if (request.mode !== 'navigate') return
  event.respondWith(
    fetch(request, { cache: 'no-store' }).catch(
      () =>
        new Response(
          `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#102032"><title>FinScope · 需要联网</title><body><main><h1>FinScope 需要网络连接</h1><p>当前无法连接服务。这里不保存离线账本，也不会自动提交交易。</p><p>请恢复网络后重新打开；如有提交结果待确认，请使用原请求编号安全重试。</p><p><a href="/">重新连接 FinScope</a></p></main></body></html>`,
          {
            status: 503,
            headers: {
              'Content-Type': 'text/html; charset=utf-8',
              'Cache-Control': 'no-store',
              'Content-Security-Policy':
                "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            },
          },
        ),
    ),
  )
})
