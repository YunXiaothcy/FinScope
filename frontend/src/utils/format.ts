export function money(value: string) {
  const [integer, fraction = '00'] = value.split('.')
  return `${integer!.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`
}

export function localDateTime(value?: string) {
  const date = value ? new Date(value) : new Date()
  const parts = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Hong_Kong',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
  return parts.replace(' ', 'T')
}

export function displayDateTime(value: string) {
  const aware = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Hong_Kong',
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(aware))
}

export const toUTC = (hongKongTime: string) =>
  new Date(`${hongKongTime}+08:00`).toISOString()
