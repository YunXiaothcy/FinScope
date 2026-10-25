import { apiRequest } from './client'
import type { Transaction } from './finance'

export type BusinessKind = 'revenue' | 'cost' | 'expense'
export interface ObligationInput {
  kind: 'receivable' | 'payable'
  category_id: string
  amount: string
  recognized_at: string
  due_date: string
  counterparty: string
  description: string
}
export interface Obligation extends ObligationInput {
  id: string
  currency: string
  status: string
  version: number
  paid_amount: string
  remaining_amount: string
  settlement_status: 'unsettled' | 'partial' | 'settled' | 'voided'
  is_overdue: boolean
}
export interface SettlementInput {
  account_id: string
  amount: string
  occurred_at: string
  version: number
}
export interface BusinessDay {
  date: string
  revenue: string
  cost: string
  expense: string
  profit: string
  cash_in: string
  cash_out: string
  adjustment_net: string
  net_cash_flow: string
}
export interface BusinessReport {
  currency: string
  start_date: string
  end_date: string
  timezone: string
  basis: string
  formula: string
  revenue: string
  cost: string
  expense: string
  profit: string
  cash_in: string
  cash_out: string
  adjustment_net: string
  operating_cash_flow: string
  net_cash_flow: string
  opening_balance: string
  closing_balance: string
  current_balance: string
  receivable_balance: string
  payable_balance: string
  overdue_receivable: string
  overdue_payable: string
  categories: {
    category_id: string
    name: string
    kind: BusinessKind
    amount: string
  }[]
  daily: BusinessDay[]
}

const path = (id: string, resource: string) =>
  `/workspaces/${id}/business/${resource}`
export const getBusinessReport = (id: string, start: string, end: string) =>
  apiRequest<BusinessReport>(
    `${path(id, 'report')}?${new URLSearchParams({ start_date: start, end_date: end })}`,
  )
export const getObligations = (id: string, query: URLSearchParams) =>
  apiRequest<Obligation[]>(`${path(id, 'obligations')}?${query}`)
export const getObligation = (id: string, itemId: string) =>
  apiRequest<Obligation>(path(id, `obligations/${itemId}`))
export const getSettlements = (id: string, itemId: string) =>
  apiRequest<Transaction[]>(path(id, `obligations/${itemId}/settlements`))
export const createObligation = (
  id: string,
  input: ObligationInput,
  key: string,
  csrf: string,
) =>
  apiRequest<Obligation>(
    path(id, 'obligations'),
    {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify(input),
    },
    csrf,
  )
export const createSettlement = (
  id: string,
  itemId: string,
  input: SettlementInput,
  key: string,
  csrf: string,
) =>
  apiRequest<Transaction>(
    path(id, `obligations/${itemId}/settlements`),
    {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify(input),
    },
    csrf,
  )
export const cancelObligation = (
  id: string,
  itemId: string,
  version: number,
  csrf: string,
) =>
  apiRequest<Obligation>(
    path(id, `obligations/${itemId}/void`),
    { method: 'POST', body: JSON.stringify({ version }) },
    csrf,
  )
