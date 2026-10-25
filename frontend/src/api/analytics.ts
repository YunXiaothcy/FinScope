import { apiRequest } from './client'
import type { Account } from './finance'

export interface DashboardDay {
  date: string
  income: string
  expense: string
  net: string
  cash_in: string
  cash_out: string
  adjustment_net: string
  net_cash_flow: string
  closing_balance: string
}
export interface DashboardCategory {
  category_id: string
  name: string
  kind: string
  amount: string
}
export interface Dashboard {
  workspace_type: 'personal' | 'business'
  currency: string
  timezone: string
  start_date: string
  end_date: string
  basis: string
  formula: string
  has_activity: boolean
  income: string
  expense: string
  net: string
  cost: string | null
  operating_expense: string | null
  cash_in: string
  cash_out: string
  adjustment_net: string
  net_cash_flow: string
  opening_balance: string
  closing_balance: string
  current_balance: string
  receivable_balance: string | null
  payable_balance: string | null
  accounts: Account[]
  categories: DashboardCategory[]
  daily: DashboardDay[]
}

export const getDashboard = (
  id: string,
  start: string,
  end: string,
  signal?: AbortSignal,
) =>
  apiRequest<Dashboard>(
    `/workspaces/${id}/analytics/dashboard?${new URLSearchParams({ start_date: start, end_date: end })}`,
    { signal },
  )
