import { apiRequest } from './client'

export interface Account {
  id: string
  name: string
  type: string
  currency: string
  opening_balance: string
  balance: string
  is_active: boolean
}
export interface Category {
  id: string
  name: string
  type: 'income' | 'expense'
  business_kind: 'revenue' | 'cost' | 'expense'
}
export interface TransactionInput {
  account_id: string
  category_id: string | null
  type: 'income' | 'expense' | 'refund' | 'adjustment'
  amount: string
  occurred_at: string
  description: string
  merchant_name: string
  related_transaction_id: string | null
  adjustment_direction: 'increase' | 'decrease' | null
}
export interface Transaction extends TransactionInput {
  id: string
  currency: string
  status: string
  version: number
  source: string
}
export interface TransactionsPage {
  items: Transaction[]
  total: number
  limit: number
  offset: number
}
export interface TransferInput {
  from_account_id: string
  to_account_id: string
  amount: string
  occurred_at: string
  description: string
}
export interface Transfer extends TransferInput {
  id: string
  currency: string
  status: string
  version: number
}
export interface Summary {
  currency: string
  start_date: string
  end_date: string
  timezone: string
  income: string
  expense: string
  net: string
  adjustment_net: string
  cash_flow: string
  total_balance: string
  categories: {
    category_id: string
    name: string
    type: string
    amount: string
  }[]
}
export interface Audit {
  id: string
  action: string
  entity_type: string
  entity_id: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  created_at: string
}
export interface Movement {
  id: string
  amount: string
  transaction_id: string | null
  transfer_id: string | null
  reverses_id: string | null
  created_at: string
}

const path = (workspaceId: string, resource: string) =>
  `/workspaces/${workspaceId}/finance/${resource}`
export const getAccounts = (id: string) =>
  apiRequest<Account[]>(path(id, 'accounts'))
export const getCategories = (id: string) =>
  apiRequest<Category[]>(path(id, 'categories'))
export const getTransactions = (id: string, query: URLSearchParams) =>
  apiRequest<TransactionsPage>(`${path(id, 'transactions')}?${query}`)
export const getSummary = (id: string, query: URLSearchParams) =>
  apiRequest<Summary>(`${path(id, 'summary')}?${query}`)
export const getTransfers = (id: string) =>
  apiRequest<Transfer[]>(path(id, 'transfers'))
export const getAudits = (id: string) =>
  apiRequest<Audit[]>(path(id, 'audit-logs'))
export const getMovements = (id: string, accountId: string) =>
  apiRequest<Movement[]>(path(id, `accounts/${accountId}/movements`))
export const saveAccount = (
  id: string,
  input: {
    name: string
    type: string
    opening_balance: string
    is_active: boolean
  },
  accountId: string | null,
  csrf: string,
) =>
  apiRequest<Account>(
    path(id, accountId ? `accounts/${accountId}` : 'accounts'),
    {
      method: accountId ? 'PATCH' : 'POST',
      body: JSON.stringify(
        accountId
          ? { name: input.name, is_active: input.is_active }
          : {
              name: input.name,
              type: input.type,
              opening_balance: input.opening_balance,
            },
      ),
    },
    csrf,
  )
export const saveCategory = (
  id: string,
  input: { name: string; type: string; business_kind?: string },
  categoryId: string | null,
  csrf: string,
) =>
  apiRequest<Category>(
    path(id, categoryId ? `categories/${categoryId}` : 'categories'),
    { method: categoryId ? 'PATCH' : 'POST', body: JSON.stringify(input) },
    csrf,
  )
export const createTransaction = (
  id: string,
  payload: TransactionInput,
  key: string,
  csrf: string,
) =>
  apiRequest<Transaction>(
    path(id, 'transactions'),
    {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify(payload),
    },
    csrf,
  )
export const editTransaction = (
  id: string,
  transactionId: string,
  payload: TransactionInput & { version: number },
  csrf: string,
) =>
  apiRequest<Transaction>(
    path(id, `transactions/${transactionId}`),
    { method: 'PUT', body: JSON.stringify(payload) },
    csrf,
  )
export const createTransfer = (
  id: string,
  payload: TransferInput,
  key: string,
  csrf: string,
) =>
  apiRequest<Transfer>(
    path(id, 'transfers'),
    {
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify(payload),
    },
    csrf,
  )
export const voidRecord = (
  id: string,
  kind: 'transactions' | 'transfers',
  recordId: string,
  version: number,
  csrf: string,
) =>
  apiRequest<Transaction | Transfer>(
    path(id, `${kind}/${recordId}/void`),
    { method: 'POST', body: JSON.stringify({ version }) },
    csrf,
  )
