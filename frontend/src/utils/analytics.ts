import type { DashboardCategory } from '../api/analytics'

export type PeriodPreset = 'month' | 'last-month' | '90-days'

// 这里只做日历运算；先取得香港日期，再使用 UTC 避免浏览器所在时区的夏令时影响。
export function presetPeriod(preset: PeriodPreset, today: string) {
  const date = new Date(`${today}T00:00:00Z`)
  if (preset === 'month')
    return { start: `${today.slice(0, 7)}-01`, end: today }
  if (preset === 'last-month') {
    const end = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 0))
    return {
      start: `${end.toISOString().slice(0, 7)}-01`,
      end: end.toISOString().slice(0, 10),
    }
  }
  date.setUTCDate(date.getUTCDate() - 89)
  return { start: date.toISOString().slice(0, 10), end: today }
}

export function cents(value: string): bigint {
  const negative = value.startsWith('-')
  const [whole = '0', fraction = ''] = value.replace(/^-/, '').split('.')
  const result = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
  return negative ? -result : result
}

export function spendingCategories(categories: DashboardCategory[]) {
  return categories
    .filter((item) => ['expense', 'cost'].includes(item.kind))
    .sort((a, b) =>
      cents(a.amount) > cents(b.amount)
        ? -1
        : cents(a.amount) < cents(b.amount)
          ? 1
          : 0,
    )
}

export function positiveCategories(categories: DashboardCategory[]) {
  return spendingCategories(categories).filter(
    (item) => cents(item.amount) > 0n,
  )
}
