export function bindPageRefresh(
  target: EventTarget,
  documentTarget: EventTarget,
  visible: () => boolean,
  allowed: () => boolean,
  refresh: () => void,
  now: () => number = Date.now,
) {
  let last = -Infinity
  const handler = () => {
    if (!visible() || !allowed() || now() - last < 1000) return
    last = now()
    refresh()
  }
  const events = ['focus', 'online', 'finscope:refresh']
  for (const name of events) target.addEventListener(name, handler)
  documentTarget.addEventListener('visibilitychange', handler)
  return () => {
    for (const name of events) target.removeEventListener(name, handler)
    documentTarget.removeEventListener('visibilitychange', handler)
  }
}
