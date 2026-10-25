export function requestKey(source: Crypto = crypto): string {
  if (typeof source.randomUUID === 'function') return source.randomUUID()
  // 局域网 HTTP 不能使用 randomUUID；仍使用加密随机数，保留幂等重试能力。
  const bytes = source.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join(
    '',
  )
}
