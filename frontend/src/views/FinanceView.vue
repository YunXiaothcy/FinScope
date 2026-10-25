<script setup lang="ts">
import { bindPageRefresh } from '../utils/pageRefresh'
import { requestKey } from '../utils/requestKey'
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import {
  createTransaction,
  createTransfer,
  editTransaction,
  getAccounts,
  getAudits,
  getCategories,
  getMovements,
  getSummary,
  getTransactions,
  getTransfers,
  saveAccount,
  saveCategory,
  voidRecord,
  type Account,
  type Audit,
  type Category,
  type Movement,
  type Summary,
  type Transaction,
  type TransactionInput,
  type Transfer,
  type TransferInput,
} from '../api/finance'
import { getWorkspace, type Workspace } from '../api/workspaces'
import { useAuthStore } from '../stores/auth'
import { displayDateTime, localDateTime, money, toUTC } from '../utils/format'

type Pending =
  | { kind: 'transaction'; key: string; payload: TransactionInput }
  | { kind: 'transfer'; key: string; payload: TransferInput }
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const workspaceId = String(route.params.workspaceId)
const workspace = ref<Workspace | null>(null)
const readonly = computed(() => workspace.value?.role === 'viewer')
const tab = ref<'ledger' | 'accounts' | 'transfers' | 'audit'>(
  route.query.tab === 'accounts' ? 'accounts' : 'ledger',
)
const accounts = ref<Account[]>([])
const categories = ref<Category[]>([])
const transactions = ref<Transaction[]>([])
const transfers = ref<Transfer[]>([])
const audits = ref<Audit[]>([])
const movements = ref<Movement[]>([])
const movementTitle = ref('')
const summary = ref<Summary | null>(null)
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
let generation = 0
let disposed = false
let unbindRefresh = () => {}
const today = localDateTime().slice(0, 10)
const filters = reactive({
  start_date: `${today.slice(0, 7)}-01`,
  end_date: today,
  type: '',
  account_id: '',
  category_id: '',
  min_amount: '',
  max_amount: '',
  q: '',
})
const draft = reactive({
  type: 'expense' as TransactionInput['type'],
  account_id: '',
  category_id: '',
  amount: '',
  localTime: localDateTime(),
  description: '',
  merchant_name: '',
  related_transaction_id: '',
  adjustment_direction: 'increase' as 'increase' | 'decrease',
})
const editing = ref<Transaction | null>(null)
const transferDraft = reactive({
  from_account_id: '',
  to_account_id: '',
  amount: '',
  localTime: localDateTime(),
  description: '',
})
const accountDraft = reactive({
  name: '',
  type: 'bank',
  opening_balance: '0.00',
  is_active: true,
})
const accountEditing = ref<string | null>(null)
const categoryDraft = reactive({
  name: '',
  type: 'expense' as 'income' | 'expense',
  business_kind: 'expense' as Category['business_kind'],
})
const categoryEditing = ref<string | null>(null)
const pending = ref<Pending | null>(null)
const voidTarget = ref<{
  kind: 'transactions' | 'transfers'
  id: string
  version: number
  label: string
} | null>(null)
const activeAccounts = computed(() => accounts.value.filter((a) => a.is_active))
const availableCategories = computed(() =>
  categories.value.filter((c) => c.type === draft.type),
)
const refundOriginals = computed(() =>
  transactions.value.filter(
    (t) =>
      t.status === 'posted' &&
      t.source !== 'settlement' &&
      ['income', 'expense'].includes(t.type),
  ),
)
const labels: Record<string, string> = {
  settle: '结算',
  reopen: '恢复未结',
  obligation: '应收应付',
  business_obligation: '应收应付',
  business_settlement: '商户结算',
  income: '收入',
  expense: '支出',
  refund: '退款',
  adjustment: '余额调整',
  posted: '已入账',
  voided: '已撤销',
  cash: '现金',
  bank: '银行账户',
  ewallet: '电子钱包',
  credit: '信用账户',
  create: '创建',
  edit: '编辑',
  update: '更新',
  void: '撤销',
  account: '账户',
  category: '分类',
  transaction: '交易',
  transfer: '转账',
}
const accountName = (id: string) =>
  accounts.value.find((a) => a.id === id)?.name || '未知账户'
const categoryName = (id: string | null) =>
  categories.value.find((c) => c.id === id)?.name || '—'
const storageKey = () => `finscope.pending.${auth.user?.id}.${workspaceId}`

function report(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) {
    auth.user = null
    auth.csrfToken = ''
    void router.replace('/')
  }
  error.value = cause instanceof Error ? cause.message : '操作失败，请重试'
}

async function loadData() {
  if (!auth.user) return
  const request = ++generation
  loading.value = true
  try {
    const query = new URLSearchParams()
    for (const [name, value] of Object.entries(filters))
      if (value) query.set(name, value)
    query.set('limit', '50')
    query.set('offset', String(offset.value))
    const period = new URLSearchParams({
      start_date: filters.start_date,
      end_date: filters.end_date,
    })
    const result = await Promise.all([
      getAccounts(workspaceId),
      getCategories(workspaceId),
      getTransactions(workspaceId, query),
      getSummary(workspaceId, period),
      getTransfers(workspaceId),
      getAudits(workspaceId),
    ])
    if (request !== generation) return
    ;[accounts.value, categories.value] = result
    transactions.value = result[2].items
    total.value = result[2].total
    summary.value = result[3]
    transfers.value = result[4]
    audits.value = result[5]
    if (!draft.account_id) draft.account_id = activeAccounts.value[0]?.id || ''
    if (!transferDraft.from_account_id)
      transferDraft.from_account_id = activeAccounts.value[0]?.id || ''
    if (!transferDraft.to_account_id)
      transferDraft.to_account_id = activeAccounts.value[1]?.id || ''
  } catch (cause) {
    report(cause)
  } finally {
    if (request === generation) loading.value = false
  }
}

async function mutate(action: () => Promise<unknown>, message: string) {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await action()
    notice.value = message
    await loadData()
  } catch (cause) {
    report(cause)
  } finally {
    busy.value = false
  }
}

function resetTransaction() {
  editing.value = null
  Object.assign(draft, {
    type: 'expense',
    category_id: '',
    amount: '',
    localTime: localDateTime(),
    description: '',
    merchant_name: '',
    related_transaction_id: '',
    adjustment_direction: 'increase',
  })
}

function changeType() {
  draft.category_id = ''
  draft.related_transaction_id = ''
}

function startEdit(record: Transaction) {
  editing.value = record
  Object.assign(draft, record, {
    category_id: record.category_id || '',
    related_transaction_id: record.related_transaction_id || '',
    localTime: localDateTime(record.occurred_at),
    adjustment_direction: record.adjustment_direction || 'increase',
  })
  tab.value = 'ledger'
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function startRefund(record: Transaction) {
  resetTransaction()
  Object.assign(draft, {
    type: 'refund',
    account_id: record.account_id,
    category_id: record.category_id || '',
    related_transaction_id: record.id,
  })
  tab.value = 'ledger'
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function chooseOriginal() {
  const original = refundOriginals.value.find(
    (t) => t.id === draft.related_transaction_id,
  )
  if (original) {
    draft.account_id = original.account_id
    draft.category_id = original.category_id || ''
  }
}

function persistPending(value: Pending | null) {
  // 只暂存尚未确认的提交草稿，成功或退出登录后清除；服务端仍是正式数据来源。
  if (value) sessionStorage.setItem(storageKey(), JSON.stringify(value))
  else sessionStorage.removeItem(storageKey())
  pending.value = value
}

async function submitPending() {
  const request = pending.value
  if (!request) return
  busy.value = true
  error.value = ''
  try {
    if (request.kind === 'transaction')
      await createTransaction(
        workspaceId,
        request.payload,
        request.key,
        auth.csrfToken,
      )
    else
      await createTransfer(
        workspaceId,
        request.payload,
        request.key,
        auth.csrfToken,
      )
    persistPending(null)
    if (request.kind === 'transaction') resetTransaction()
    else {
      transferDraft.amount = ''
      transferDraft.description = ''
    }
    notice.value = '已成功入账。重复重试不会创建第二笔记录。'
    await loadData()
  } catch (cause) {
    // 明确的输入或权限拒绝可修改草稿；网络失败与 409 保留原编号供安全重试。
    if (
      cause instanceof ApiError &&
      cause.status >= 400 &&
      cause.status < 500 &&
      cause.status !== 409
    )
      persistPending(null)
    report(cause)
  } finally {
    busy.value = false
  }
}

async function submitTransaction() {
  if (pending.value) return
  try {
    const payload: TransactionInput = {
      type: draft.type,
      account_id: draft.account_id,
      category_id: ['income', 'expense', 'refund'].includes(draft.type)
        ? draft.category_id || null
        : null,
      amount: draft.amount,
      occurred_at: toUTC(draft.localTime),
      description: draft.description,
      merchant_name: draft.merchant_name,
      related_transaction_id:
        draft.type === 'refund' ? draft.related_transaction_id : null,
      adjustment_direction:
        draft.type === 'adjustment' ? draft.adjustment_direction : null,
    }
    if (editing.value) {
      const record = editing.value
      await mutate(async () => {
        await editTransaction(
          workspaceId,
          record.id,
          { ...payload, version: record.version },
          auth.csrfToken,
        )
        resetTransaction()
      }, '交易已更新，原资金变动已保留并冲销。')
    } else {
      persistPending({ kind: 'transaction', key: requestKey(), payload })
      await submitPending()
    }
  } catch (cause) {
    report(cause)
  }
}

async function submitTransfer() {
  if (pending.value) return
  try {
    persistPending({
      kind: 'transfer',
      key: requestKey(),
      payload: {
        from_account_id: transferDraft.from_account_id,
        to_account_id: transferDraft.to_account_id,
        amount: transferDraft.amount,
        occurred_at: toUTC(transferDraft.localTime),
        description: transferDraft.description,
      },
    })
    await submitPending()
  } catch (cause) {
    report(cause)
  }
}

async function submitAccount() {
  await mutate(async () => {
    await saveAccount(
      workspaceId,
      accountDraft,
      accountEditing.value,
      auth.csrfToken,
    )
    accountEditing.value = null
    Object.assign(accountDraft, {
      name: '',
      type: 'bank',
      opening_balance: '0.00',
      is_active: true,
    })
  }, '账户已保存。期初余额创建后保持不变。')
}

async function submitCategory() {
  await mutate(async () => {
    await saveCategory(
      workspaceId,
      {
        name: categoryDraft.name,
        type: categoryDraft.type,
        business_kind:
          categoryDraft.type === 'income'
            ? 'revenue'
            : workspace.value?.type === 'business' &&
                categoryDraft.business_kind === 'cost'
              ? 'cost'
              : 'expense',
      },
      categoryEditing.value,
      auth.csrfToken,
    )
    categoryEditing.value = null
    categoryDraft.name = ''
  }, '分类已保存。')
}

function beginAccountEdit(account: Account) {
  accountEditing.value = account.id
  Object.assign(accountDraft, account)
}

function cancelAccountEdit() {
  accountEditing.value = null
  Object.assign(accountDraft, {
    name: '',
    type: 'bank',
    opening_balance: '0.00',
    is_active: true,
  })
}

function beginCategoryEdit(category: Category) {
  categoryEditing.value = category.id
  Object.assign(categoryDraft, category)
}

function cancelCategoryEdit() {
  categoryEditing.value = null
  categoryDraft.name = ''
  categoryDraft.type = 'expense'
  categoryDraft.business_kind = 'expense'
}

function categoryTypeChanged() {
  categoryDraft.business_kind =
    categoryDraft.type === 'income' ? 'revenue' : 'expense'
}

async function confirmVoid() {
  const target = voidTarget.value
  if (!target) return
  await mutate(async () => {
    await voidRecord(
      workspaceId,
      target.kind,
      target.id,
      target.version,
      auth.csrfToken,
    )
    voidTarget.value = null
  }, '已撤销，并追加反向资金变动。')
}

async function showMovements(account: Account) {
  try {
    movements.value = await getMovements(workspaceId, account.id)
    movementTitle.value = account.name
    tab.value = 'audit'
  } catch (cause) {
    report(cause)
  }
}

async function applyFilters() {
  offset.value = 0
  error.value = ''
  await loadData()
}
async function page(delta: number) {
  offset.value += delta
  await loadData()
}
function focusRefresh() {
  if (!busy.value) void loadData()
}

async function quickEntry() {
  if (readonly.value) return
  tab.value = 'ledger'
  await nextTick()
  document
    .getElementById('quick-entry')
    ?.scrollIntoView({ block: 'start', behavior: 'auto' })
}
function showLedger() {
  tab.value = 'ledger'
}
watch(
  () => route.query.tab,
  (value) => {
    tab.value = value === 'accounts' ? 'accounts' : 'ledger'
  },
)
watch(
  () => route.query.quick,
  (value) => {
    if (value === '1') void quickEntry()
  },
)

onMounted(async () => {
  try {
    await auth.initialize()
    if (!auth.user) {
      await router.replace('/')
      return
    }
    workspace.value = await getWorkspace(workspaceId)
    const saved = sessionStorage.getItem(storageKey())
    if (saved) {
      try {
        pending.value = JSON.parse(saved) as Pending
      } catch {
        sessionStorage.removeItem(storageKey())
      }
    }
    await loadData()
    if (!disposed && route.query.quick === '1') await quickEntry()
    if (!disposed) window.addEventListener('finscope:quick-entry', quickEntry)
    if (!disposed) window.addEventListener('finscope:ledger', showLedger)
    if (!disposed)
      unbindRefresh = bindPageRefresh(
        window,
        document,
        () => document.visibilityState === 'visible' && navigator.onLine,
        () => !busy.value && !loading.value,
        focusRefresh,
      )
  } catch (cause) {
    report(cause)
  }
})
onUnmounted(() => {
  generation++
  disposed = true
  unbindRefresh()
  window.removeEventListener('finscope:quick-entry', quickEntry)
  window.removeEventListener('finscope:ledger', showLedger)
})
</script>

<template>
  <main class="min-h-screen px-4 py-7 sm:px-7">
    <div class="mx-auto max-w-6xl space-y-6">
      <header class="flex flex-wrap items-center justify-between gap-4">
        <div class="flex items-center gap-3">
          <div>
            <h1 class="text-xl font-semibold">账本</h1>
            <p class="text-sm muted">
              {{ workspace?.name }} · {{ workspace?.base_currency }} · 香港时间
              UTC+8{{ readonly ? ' · 只读权限' : '' }}
            </p>
          </div>
        </div>
        <button class="secondary-button" :disabled="loading" @click="loadData">
          {{ loading ? '刷新中…' : '刷新账本' }}
        </button>
      </header>
      <p v-if="error" class="message-error" role="alert">{{ error }}</p>
      <p v-if="notice" class="message-success" role="status">{{ notice }}</p>
      <div v-if="pending" class="pending-panel rounded-xl p-4" role="status">
        <p>
          上次{{
            pending.kind === 'transaction' ? '记账' : '转账'
          }}尚未确认。重试会使用原内容和同一个请求编号。
        </p>
        <button
          class="primary-button mt-3"
          :disabled="busy"
          @click="submitPending"
        >
          {{ busy ? '确认中…' : '安全重试上次提交' }}
        </button>
      </div>

      <form
        class="panel flex flex-wrap items-end gap-3 rounded-2xl p-4"
        @submit.prevent="applyFilters"
      >
        <label class="field-label">
          开始日期
          <input
            v-model="filters.start_date"
            class="field"
            type="date"
            required
          />
        </label>
        <label class="field-label">
          结束日期
          <input
            v-model="filters.end_date"
            class="field"
            type="date"
            required
          />
        </label>
        <button class="primary-button" :disabled="loading">应用统计周期</button>
        <p class="text-xs muted">收支按所选周期；余额显示当前值。</p>
      </form>
      <section
        v-if="summary"
        class="grid grid-cols-2 gap-3 lg:grid-cols-4"
        aria-label="财务汇总"
      >
        <article
          v-for="metric in [
            { label: '本期收入（扣除退款）', value: summary.income },
            { label: '本期支出（扣除退款）', value: summary.expense },
            { label: '本期结余', value: summary.net },
            { label: '当前账户总余额', value: summary.total_balance },
          ]"
          :key="metric.label"
          class="panel metric-card rounded-2xl p-4 sm:p-5"
        >
          <p class="text-sm muted">{{ metric.label }}</p>
          <p class="mt-2 break-all text-xl font-semibold accent">
            {{ money(metric.value) }}
          </p>
          <span class="text-xs muted">{{ summary.currency }}</span>
        </article>
      </section>
      <p v-if="summary" class="text-xs muted">
        当前显示 {{ summary.start_date }} 至
        {{ summary.end_date }}。本期余额调整
        {{ money(summary.adjustment_net) }}；本期净资金变动
        {{
          money(summary.cash_flow)
        }}。内部转账不计入收支，期初余额不计入本期收入。
      </p>
      <p v-if="workspace?.type === 'business'" class="text-sm accent">
        这里的收入与支出按实际收付款显示，包含应收应付结算；营业收入、成本、费用和利润请查看“商户经营分析”。
      </p>
      <nav class="flex flex-wrap gap-2" aria-label="账本功能">
        <button
          v-for="item in [
            { id: 'ledger', label: '收支流水' },
            { id: 'accounts', label: '账户与分类' },
            { id: 'transfers', label: '内部转账' },
            { id: 'audit', label: '操作记录' },
          ]"
          :key="item.id"
          class="tab"
          :class="{ active: tab === item.id }"
          :aria-pressed="tab === item.id"
          @click="tab = item.id as typeof tab"
        >
          {{ item.label }}
        </button>
      </nav>

      <section v-if="tab === 'ledger'" class="space-y-6">
        <form
          v-if="!readonly"
          class="panel rounded-2xl p-5 sm:p-7"
          id="quick-entry"
          @submit.prevent="submitTransaction"
        >
          <div class="mb-5 flex items-center justify-between">
            <h2 class="text-lg font-semibold">
              {{ editing ? '编辑交易' : '记一笔' }}
            </h2>
            <button
              v-if="editing"
              class="text-sm muted"
              type="button"
              @click="resetTransaction"
            >
              取消编辑
            </button>
          </div>
          <div
            v-if="accounts.length === 0 || categories.length === 0"
            class="mb-4 text-sm muted"
          >
            先在“账户与分类”创建账户和收支分类，再回来记账。
          </div>
          <div class="form-grid">
            <label class="field-label">
              类型
              <select
                v-model="draft.type"
                class="field"
                :disabled="!!editing"
                @change="changeType"
              >
                <option value="expense">支出</option>
                <option value="income">收入</option>
                <option value="refund">原交易退款</option>
                <option value="adjustment">余额调整</option>
              </select>
            </label>
            <label v-if="draft.type === 'refund'" class="field-label">
              原交易
              <select
                v-model="draft.related_transaction_id"
                class="field"
                required
                @change="chooseOriginal"
              >
                <option value="">选择原收入或支出</option>
                <option
                  v-for="record in refundOriginals"
                  :key="record.id"
                  :value="record.id"
                >
                  {{ labels[record.type] }} {{ record.amount }} ·
                  {{
                    record.description || displayDateTime(record.occurred_at)
                  }}
                </option>
              </select>
            </label>
            <label class="field-label">
              账户
              <select
                v-model="draft.account_id"
                class="field"
                required
                :disabled="draft.type === 'refund'"
              >
                <option value="">选择账户</option>
                <option
                  v-for="account in activeAccounts"
                  :key="account.id"
                  :value="account.id"
                >
                  {{ account.name }}
                </option>
              </select>
            </label>
            <label
              v-if="draft.type === 'income' || draft.type === 'expense'"
              class="field-label"
            >
              分类
              <select v-model="draft.category_id" class="field" required>
                <option value="">选择分类</option>
                <option
                  v-for="category in availableCategories"
                  :key="category.id"
                  :value="category.id"
                >
                  {{ category.name }}
                </option>
              </select>
            </label>
            <label v-if="draft.type === 'adjustment'" class="field-label">
              调整方向
              <select v-model="draft.adjustment_direction" class="field">
                <option value="increase">增加余额</option>
                <option value="decrease">减少余额</option>
              </select>
            </label>
            <label class="field-label">
              金额（{{ workspace?.base_currency }}）
              <input
                v-model="draft.amount"
                class="field"
                inputmode="decimal"
                pattern="[0-9]+(\.[0-9]{1,2})?"
                required
                placeholder="0.00"
              />
            </label>
            <label class="field-label">
              发生时间（香港）
              <input
                v-model="draft.localTime"
                class="field"
                type="datetime-local"
                step="1"
                required
              />
            </label>
            <label class="field-label">
              商户名称
              <input
                v-model="draft.merchant_name"
                class="field"
                maxlength="120"
                placeholder="可选"
              />
            </label>
            <label class="field-label">
              备注
              <input
                v-model="draft.description"
                class="field"
                maxlength="500"
                placeholder="例如：午餐、工资"
              />
            </label>
          </div>
          <p v-if="draft.type === 'refund'" class="mt-4 text-xs muted">
            退款沿用原账户和分类，累计不能超过原金额。需要其他日期的原交易时，请先调整流水筛选。
          </p>
          <p v-if="draft.type === 'adjustment'" class="mt-4 text-xs muted">
            用于对账修正，不计收入或支出；请填写原因。借款等非经营流入也不应当作经营收入。
          </p>
          <button
            class="primary-button mt-5"
            :disabled="busy || !!pending"
            type="submit"
          >
            {{ busy ? '处理中…' : editing ? '保存修改' : '确认入账' }}
          </button>
        </form>

        <section class="panel rounded-2xl p-5 sm:p-7">
          <h2 class="text-lg font-semibold">交易流水</h2>
          <form class="form-grid mt-5" @submit.prevent="applyFilters">
            <label class="field-label">
              类型
              <select v-model="filters.type" class="field">
                <option value="">全部</option>
                <option value="income">收入</option>
                <option value="expense">支出</option>
                <option value="refund">退款</option>
                <option value="adjustment">调整</option>
              </select>
            </label>
            <label class="field-label">
              账户
              <select v-model="filters.account_id" class="field">
                <option value="">全部账户</option>
                <option v-for="a in accounts" :key="a.id" :value="a.id">
                  {{ a.name }}
                </option>
              </select>
            </label>
            <label class="field-label">
              分类
              <select v-model="filters.category_id" class="field">
                <option value="">全部分类</option>
                <option v-for="c in categories" :key="c.id" :value="c.id">
                  {{ c.name }}
                </option>
              </select>
            </label>
            <label class="field-label">
              最低金额
              <input
                v-model="filters.min_amount"
                class="field"
                inputmode="decimal"
                placeholder="不限"
              />
            </label>
            <label class="field-label">
              最高金额
              <input
                v-model="filters.max_amount"
                class="field"
                inputmode="decimal"
                placeholder="不限"
              />
            </label>
            <label class="field-label">
              备注或商户
              <input
                v-model="filters.q"
                class="field"
                maxlength="120"
                placeholder="搜索关键词"
              />
            </label>
            <button class="primary-button self-end" :disabled="loading">
              筛选流水
            </button>
          </form>
          <p v-if="transactions.length === 0" class="py-8 muted">
            当前筛选下没有交易。
          </p>
          <div
            v-for="record in transactions"
            :key="record.id"
            class="ledger-row"
          >
            <div>
              <p class="font-medium">
                {{ labels[record.type] }} ·
                {{ categoryName(record.category_id) }}
                <span v-if="record.status === 'voided'" class="error text-xs">
                  已撤销
                </span>
              </p>
              <p class="mt-1 text-sm muted">
                {{ accountName(record.account_id) }} ·
                {{ displayDateTime(record.occurred_at) }}
              </p>
              <p class="mt-1 break-words text-sm muted">
                {{ record.description }} {{ record.merchant_name }}
                <span v-if="record.source === 'settlement'" class="accent">
                  （应收应付结算，仅影响现金流）
                </span>
                <span v-if="record.source === 'import'" class="accent">
                  （账单导入）
                </span>
                <span v-if="record.source === 'ocr'" class="accent">
                  （票据审核入账）
                </span>
              </p>
            </div>
            <div class="text-right">
              <p class="text-lg font-semibold">
                {{ money(record.amount) }}
                <span class="text-xs muted">{{ record.currency }}</span>
              </p>
              <div
                v-if="!readonly && record.status === 'posted'"
                class="mt-2 flex flex-wrap justify-end gap-3 text-sm"
              >
                <button
                  v-if="
                    record.type !== 'refund' && record.source !== 'settlement'
                  "
                  class="accent"
                  :disabled="busy || !!pending"
                  @click="startEdit(record)"
                >
                  编辑
                </button>
                <button
                  v-if="
                    record.source !== 'settlement' &&
                    (record.type === 'income' || record.type === 'expense')
                  "
                  class="accent"
                  :disabled="busy || !!pending"
                  @click="startRefund(record)"
                >
                  退款
                </button>
                <button
                  class="error"
                  :disabled="busy"
                  @click="
                    voidTarget = {
                      kind: 'transactions',
                      id: record.id,
                      version: record.version,
                      label: `${labels[record.type]} ${record.amount}`,
                    }
                  "
                >
                  撤销
                </button>
              </div>
            </div>
          </div>
          <div class="mt-5 flex items-center justify-between gap-3 text-sm">
            <span class="muted">
              共 {{ total }} 条 · 已显示
              {{ offset + (transactions.length ? 1 : 0) }}–{{
                offset + transactions.length
              }}
            </span>
            <div class="flex gap-4">
              <button :disabled="offset === 0 || loading" @click="page(-50)">
                上一页
              </button>
              <button
                :disabled="offset + 50 >= total || loading"
                @click="page(50)"
              >
                下一页
              </button>
            </div>
          </div>
          <div
            v-if="summary?.categories.length"
            class="mt-7 border-t border-slate-700 pt-5"
          >
            <h3 class="mb-3 font-semibold">本期分类汇总</h3>
            <div class="grid gap-2 sm:grid-cols-2">
              <p
                v-for="c in summary.categories"
                :key="c.category_id"
                class="flex justify-between gap-4 text-sm"
              >
                <span class="muted">{{ labels[c.type] }} · {{ c.name }}</span>
                <span>{{ money(c.amount) }}</span>
              </p>
            </div>
          </div>
        </section>
      </section>

      <section v-if="tab === 'accounts'" class="grid gap-6 lg:grid-cols-2">
        <section class="panel rounded-2xl p-5 sm:p-7">
          <h2 class="text-lg font-semibold">账户</h2>
          <div v-for="account in accounts" :key="account.id" class="ledger-row">
            <div>
              <p>
                {{ account.name }}
                <span class="text-xs muted">
                  {{ labels[account.type]
                  }}{{ account.is_active ? '' : ' · 已停用' }}
                </span>
              </p>
              <p class="mt-1 text-lg accent">
                {{ money(account.balance) }} {{ account.currency }}
              </p>
              <p class="text-xs muted">
                期初 {{ money(account.opening_balance) }}
              </p>
            </div>
            <div class="flex flex-col gap-2 text-sm">
              <button class="accent" @click="showMovements(account)">
                资金变动
              </button>
              <button
                v-if="!readonly"
                class="muted"
                @click="beginAccountEdit(account)"
              >
                编辑账户
              </button>
            </div>
          </div>
          <p v-if="!accounts.length" class="my-5 muted">
            先添加现金或银行账户。
          </p>
          <form
            v-if="!readonly"
            class="mt-6 space-y-4"
            @submit.prevent="submitAccount"
          >
            <h3 class="font-semibold">
              {{ accountEditing ? '编辑账户' : '新增账户' }}
            </h3>
            <label class="field-label">
              账户名称
              <input
                v-model="accountDraft.name"
                class="field"
                required
                maxlength="80"
              />
            </label>
            <label class="field-label">
              账户类型
              <select
                v-model="accountDraft.type"
                class="field"
                :disabled="!!accountEditing"
              >
                <option value="bank">银行账户</option>
                <option value="cash">现金</option>
                <option value="ewallet">电子钱包</option>
                <option value="credit">信用账户</option>
              </select>
            </label>
            <label class="field-label">
              期初余额（{{ workspace?.base_currency }}）
              <input
                v-model="accountDraft.opening_balance"
                class="field"
                inputmode="decimal"
                required
                :disabled="!!accountEditing"
              />
            </label>
            <label
              v-if="accountEditing"
              class="flex items-center gap-2 text-sm"
            >
              <input v-model="accountDraft.is_active" type="checkbox" />
              允许新交易
            </label>
            <p class="text-xs muted">
              期初余额创建后不可直接改写；余额修正请登记调整并填写原因。
            </p>
            <div class="flex gap-4">
              <button class="primary-button" :disabled="busy">保存账户</button>
              <button
                v-if="accountEditing"
                type="button"
                @click="cancelAccountEdit"
              >
                取消
              </button>
            </div>
          </form>
        </section>
        <section class="panel rounded-2xl p-5 sm:p-7">
          <h2 class="text-lg font-semibold">收支分类</h2>
          <div
            v-for="category in categories"
            :key="category.id"
            class="member-row"
          >
            <span>
              {{ category.name }}
              <span class="muted text-sm">
                {{
                  workspace?.type === 'business'
                    ? {
                        revenue: '营业收入',
                        cost: '经营成本',
                        expense: '经营费用',
                      }[category.business_kind]
                    : labels[category.type]
                }}
              </span>
            </span>
            <button
              v-if="!readonly"
              class="accent text-sm"
              @click="beginCategoryEdit(category)"
            >
              重命名
            </button>
          </div>
          <p v-if="!categories.length" class="my-5 muted">
            例如：工资（收入）、餐饮（支出）。
          </p>
          <form
            v-if="!readonly"
            class="mt-6 space-y-4"
            @submit.prevent="submitCategory"
          >
            <h3 class="font-semibold">
              {{ categoryEditing ? '重命名分类' : '新增分类' }}
            </h3>
            <label class="field-label">
              分类名称
              <input
                v-model="categoryDraft.name"
                class="field"
                required
                maxlength="80"
              />
            </label>
            <label class="field-label">
              分类类型
              <select
                v-model="categoryDraft.type"
                class="field"
                :disabled="!!categoryEditing"
                @change="categoryTypeChanged"
              >
                <option value="expense">支出</option>
                <option value="income">收入</option>
              </select>
            </label>
            <label
              v-if="
                workspace?.type === 'business' &&
                categoryDraft.type === 'expense'
              "
              class="field-label"
            >
              经营口径
              <select
                v-model="categoryDraft.business_kind"
                class="field"
                :disabled="!!categoryEditing"
              >
                <option value="expense">经营费用（例如租金、办公支出）</option>
                <option value="cost">
                  经营成本（例如已确认的采购或服务成本）
                </option>
              </select>
            </label>
            <p v-if="workspace?.type === 'business'" class="text-xs muted">
              经营口径创建后固定，避免已有报表被直接改写；如需更正请新增分类并编辑相关交易。
            </p>
            <div class="flex gap-4">
              <button class="primary-button" :disabled="busy">保存分类</button>
              <button
                v-if="categoryEditing"
                type="button"
                @click="cancelCategoryEdit"
              >
                取消
              </button>
            </div>
          </form>
        </section>
      </section>

      <section v-if="tab === 'transfers'" class="space-y-6">
        <form
          v-if="!readonly"
          class="panel rounded-2xl p-5 sm:p-7"
          @submit.prevent="submitTransfer"
        >
          <h2 class="text-lg font-semibold">账户间转账</h2>
          <p class="mt-2 text-sm muted">
            从一个账户转入另一个同币种账户，不增加收入或支出。
          </p>
          <div class="form-grid mt-5">
            <label class="field-label">
              转出账户
              <select
                v-model="transferDraft.from_account_id"
                class="field"
                required
              >
                <option value="">选择账户</option>
                <option v-for="a in activeAccounts" :key="a.id" :value="a.id">
                  {{ a.name }}
                </option>
              </select>
            </label>
            <label class="field-label">
              转入账户
              <select
                v-model="transferDraft.to_account_id"
                class="field"
                required
              >
                <option value="">选择账户</option>
                <option v-for="a in activeAccounts" :key="a.id" :value="a.id">
                  {{ a.name }}
                </option>
              </select>
            </label>
            <label class="field-label">
              金额
              <input
                v-model="transferDraft.amount"
                class="field"
                inputmode="decimal"
                pattern="[0-9]+(\.[0-9]{1,2})?"
                required
              />
            </label>
            <label class="field-label">
              发生时间（香港）
              <input
                v-model="transferDraft.localTime"
                class="field"
                type="datetime-local"
                step="1"
                required
              />
            </label>
            <label class="field-label">
              备注
              <input
                v-model="transferDraft.description"
                class="field"
                maxlength="500"
              />
            </label>
          </div>
          <button class="primary-button mt-5" :disabled="busy || !!pending">
            确认转账
          </button>
        </form>
        <section class="panel rounded-2xl p-5 sm:p-7">
          <h2 class="text-lg font-semibold">转账记录</h2>
          <p class="mt-2 text-xs muted">最近 200 条，包含撤销记录。</p>
          <p v-if="!transfers.length" class="my-5 muted">暂无转账。</p>
          <div
            v-for="transfer in transfers"
            :key="transfer.id"
            class="ledger-row"
          >
            <div>
              <p>
                {{ accountName(transfer.from_account_id) }} →
                {{ accountName(transfer.to_account_id) }}
              </p>
              <p class="mt-1 text-sm muted">
                {{ displayDateTime(transfer.occurred_at) }} ·
                {{ transfer.description }}
              </p>
              <span v-if="transfer.status === 'voided'" class="error text-xs">
                已撤销
              </span>
            </div>
            <div class="text-right">
              <p class="font-semibold">
                {{ money(transfer.amount) }} {{ transfer.currency }}
              </p>
              <button
                v-if="!readonly && transfer.status === 'posted'"
                class="mt-2 text-sm error"
                :disabled="busy"
                @click="
                  voidTarget = {
                    kind: 'transfers',
                    id: transfer.id,
                    version: transfer.version,
                    label: `转账 ${transfer.amount}`,
                  }
                "
              >
                撤销转账
              </button>
            </div>
          </div>
        </section>
      </section>

      <section
        v-if="tab === 'audit'"
        class="panel space-y-6 rounded-2xl p-5 sm:p-7"
      >
        <div v-if="movementTitle">
          <h2 class="text-lg font-semibold">
            {{ movementTitle }} · 最近资金变动
          </h2>
          <p class="mt-2 text-sm muted">
            期初余额 + 全部资金变动 = 当前余额。此处显示最近 200
            条；带“冲销”的条目保留撤销或编辑痕迹。
          </p>
          <div v-for="entry in movements" :key="entry.id" class="member-row">
            <span class="text-sm muted">
              {{ displayDateTime(entry.created_at) }} ·
              {{
                entry.reverses_id ? '冲销' : entry.transfer_id ? '转账' : '交易'
              }}
            </span>
            <span>{{ money(entry.amount) }}</span>
          </div>
        </div>
        <h2 class="text-lg font-semibold">最近 100 条操作记录</h2>
        <div
          v-for="entry in audits"
          :key="entry.id"
          class="border-b border-slate-700 py-3"
        >
          <p class="text-sm">
            {{ labels[entry.action] }}{{ labels[entry.entity_type] }} ·
            {{ displayDateTime(entry.created_at) }}
          </p>
          <details class="mt-2 text-xs muted">
            <summary class="cursor-pointer">查看修改前后</summary>
            <pre class="mt-2 whitespace-pre-wrap break-all">
修改前：{{ JSON.stringify(entry.before, null, 2) }}
修改后：{{ JSON.stringify(entry.after, null, 2) }}</pre>
          </details>
        </div>
        <p v-if="!audits.length" class="muted">暂无操作记录。</p>
      </section>
    </div>
    <div
      v-if="voidTarget"
      class="modal-backdrop"
      @click.self="voidTarget = null"
    >
      <section
        class="panel w-full max-w-md rounded-2xl p-6"
        role="dialog"
        aria-modal="true"
        aria-label="确认撤销"
      >
        <h2 class="text-xl font-semibold">撤销 {{ voidTarget.label }}？</h2>
        <p class="mt-4 leading-7 muted">
          将保留原记录并追加反向资金变动，余额和汇总随之更新。
        </p>
        <div class="mt-6 flex gap-4">
          <button class="primary-button" :disabled="busy" @click="confirmVoid">
            确认撤销
          </button>
          <button :disabled="busy" @click="voidTarget = null">取消</button>
        </div>
      </section>
    </div>
  </main>
</template>
