<script setup lang="ts">
import { bindPageRefresh } from '../utils/pageRefresh'
import { requestKey } from '../utils/requestKey'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import {
  cancelObligation,
  createObligation,
  createSettlement,
  getBusinessReport,
  getObligation,
  getObligations,
  getSettlements,
  type BusinessKind,
  type BusinessReport,
  type Obligation,
  type ObligationInput,
  type SettlementInput,
} from '../api/business'
import {
  createTransaction,
  getAccounts,
  getCategories,
  voidRecord,
  type Account,
  type Category,
  type Transaction,
  type TransactionInput,
} from '../api/finance'
import { getWorkspace, type Workspace } from '../api/workspaces'
import { useAuthStore } from '../stores/auth'
import { displayDateTime, localDateTime, money, toUTC } from '../utils/format'

type Pending =
  | { kind: 'cash'; key: string; payload: TransactionInput }
  | { kind: 'obligation'; key: string; payload: ObligationInput }
  | {
      kind: 'settlement'
      key: string
      itemId: string
      payload: SettlementInput
    }
type Confirmation =
  | { kind: 'obligation'; item: Obligation }
  | { kind: 'settlement'; transaction: Transaction }
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const workspaceId = String(route.params.workspaceId)
const workspace = ref<Workspace | null>(null)
const readonly = computed(() => workspace.value?.role === 'viewer')
const businessReady = computed(() => workspace.value?.type === 'business')
const tab = ref<'report' | 'cash' | 'obligations'>('report')
const report = ref<BusinessReport | null>(null)
const accounts = ref<Account[]>([])
const categories = ref<Category[]>([])
const items = ref<Obligation[]>([])
const selected = ref<Obligation | null>(null)
const settlements = ref<Transaction[]>([])
const pending = ref<Pending | null>(null)
const confirmation = ref<Confirmation | null>(null)
const busy = ref(false)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const offset = ref(0)
const today = localDateTime().slice(0, 10)
const period = reactive({ start: `${today.slice(0, 7)}-01`, end: today })
const filters = reactive({ kind: '', state: '' })
const cashDraft = reactive({
  kind: 'revenue' as BusinessKind,
  category_id: '',
  account_id: '',
  amount: '',
  localTime: localDateTime(),
  counterparty: '',
  description: '',
})
const invoiceDraft = reactive({
  kind: 'receivable' as 'receivable' | 'payable',
  category_id: '',
  amount: '',
  localTime: localDateTime(),
  due_date: today,
  counterparty: '',
  description: '',
})
const settleDraft = reactive({
  account_id: '',
  amount: '',
  localTime: localDateTime(),
})
const activeAccounts = computed(() => accounts.value.filter((a) => a.is_active))
const cashCategories = computed(() =>
  categories.value.filter((c) => c.business_kind === cashDraft.kind),
)
const invoiceCategories = computed(() =>
  categories.value.filter(
    (c) =>
      c.type === (invoiceDraft.kind === 'receivable' ? 'income' : 'expense'),
  ),
)
const kindLabels: Record<string, string> = {
  revenue: '营业收入',
  cost: '经营成本',
  expense: '经营费用',
  receivable: '应收',
  payable: '应付',
  unsettled: '未结算',
  partial: '部分结算',
  settled: '已结清',
  voided: '已撤销',
}
let generation = 0
let disposed = false
let unbindRefresh = () => {}
const storageKey = () =>
  `finscope.pending.business.${auth.user?.id}.${workspaceId}`
const categoryName = (id: string) =>
  categories.value.find((c) => c.id === id)?.name || '未知分类'
const accountName = (id: string) =>
  accounts.value.find((a) => a.id === id)?.name || '未知账户'

function showError(cause: unknown) {
  error.value = cause instanceof Error ? cause.message : '操作失败，请重试'
  if (cause instanceof ApiError && cause.status === 401) {
    auth.user = null
    auth.csrfToken = ''
    void router.replace('/')
  }
}

async function loadSelected(itemId: string) {
  const [item, payments] = await Promise.all([
    getObligation(workspaceId, itemId),
    getSettlements(workspaceId, itemId),
  ])
  selected.value = item
  settlements.value = payments
}

async function loadData() {
  if (!businessReady.value || !auth.user) return
  const request = ++generation
  loading.value = true
  try {
    const query = new URLSearchParams({
      limit: '50',
      offset: String(offset.value),
    })
    if (filters.kind) query.set('kind', filters.kind)
    if (filters.state) query.set('state', filters.state)
    const result = await Promise.all([
      getBusinessReport(workspaceId, period.start, period.end),
      getAccounts(workspaceId),
      getCategories(workspaceId),
      getObligations(workspaceId, query),
    ])
    if (request !== generation) return
    report.value = result[0]
    accounts.value = result[1]
    categories.value = result[2]
    items.value = result[3]
    if (!cashDraft.account_id)
      cashDraft.account_id = activeAccounts.value[0]?.id || ''
    if (!settleDraft.account_id)
      settleDraft.account_id = activeAccounts.value[0]?.id || ''
    if (selected.value) await loadSelected(selected.value.id)
  } catch (cause) {
    if (request === generation) showError(cause)
  } finally {
    if (request === generation) loading.value = false
  }
}

function remember(value: Pending | null) {
  if (value) sessionStorage.setItem(storageKey(), JSON.stringify(value))
  else sessionStorage.removeItem(storageKey())
  pending.value = value
}

async function retryPending() {
  const request = pending.value
  if (!request) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    if (request.kind === 'cash')
      await createTransaction(
        workspaceId,
        request.payload,
        request.key,
        auth.csrfToken,
      )
    else if (request.kind === 'obligation')
      await createObligation(
        workspaceId,
        request.payload,
        request.key,
        auth.csrfToken,
      )
    else
      await createSettlement(
        workspaceId,
        request.itemId,
        request.payload,
        request.key,
        auth.csrfToken,
      )
    remember(null)
    if (request.kind === 'cash') {
      cashDraft.amount = ''
      cashDraft.description = ''
    }
    if (request.kind === 'obligation') {
      invoiceDraft.amount = ''
      invoiceDraft.description = ''
    }
    if (request.kind === 'settlement') settleDraft.amount = ''
    notice.value =
      request.kind === 'obligation'
        ? '业务已确认，利润已更新；实际收付款请使用“结算”，避免重复登记。'
        : '记录已保存，重复重试不会再次入账。'
    await loadData()
  } catch (cause) {
    if (
      cause instanceof ApiError &&
      cause.status >= 400 &&
      cause.status < 500 &&
      (cause.status !== 409 ||
        cause.message.includes('应收应付已') ||
        cause.message.includes('同一请求'))
    )
      remember(null)
    showError(cause)
    if (cause instanceof ApiError && cause.status === 409 && selected.value) {
      try {
        await loadSelected(selected.value.id)
      } catch {
        /* 保留原错误提示。 */
      }
    }
  } finally {
    busy.value = false
  }
}

async function submitCash() {
  if (pending.value) return
  try {
    remember({
      kind: 'cash',
      key: requestKey(),
      payload: {
        type: cashDraft.kind === 'revenue' ? 'income' : 'expense',
        account_id: cashDraft.account_id,
        category_id: cashDraft.category_id,
        amount: cashDraft.amount,
        occurred_at: toUTC(cashDraft.localTime),
        description: cashDraft.description,
        merchant_name: cashDraft.counterparty,
        related_transaction_id: null,
        adjustment_direction: null,
      },
    })
    await retryPending()
  } catch (cause) {
    showError(cause)
  }
}

async function submitObligation() {
  if (pending.value) return
  try {
    remember({
      kind: 'obligation',
      key: requestKey(),
      payload: {
        kind: invoiceDraft.kind,
        category_id: invoiceDraft.category_id,
        amount: invoiceDraft.amount,
        recognized_at: toUTC(invoiceDraft.localTime),
        due_date: invoiceDraft.due_date,
        counterparty: invoiceDraft.counterparty,
        description: invoiceDraft.description,
      },
    })
    await retryPending()
  } catch (cause) {
    showError(cause)
  }
}

async function submitSettlement() {
  if (!selected.value || pending.value) return
  try {
    remember({
      kind: 'settlement',
      key: requestKey(),
      itemId: selected.value.id,
      payload: {
        account_id: settleDraft.account_id,
        amount: settleDraft.amount,
        occurred_at: toUTC(settleDraft.localTime),
        version: selected.value.version,
      },
    })
    await retryPending()
  } catch (cause) {
    showError(cause)
  }
}

async function openItem(item: Obligation) {
  error.value = ''
  try {
    await loadSelected(item.id)
    settleDraft.amount = ''
    settleDraft.localTime = localDateTime()
  } catch (cause) {
    showError(cause)
  }
}

async function confirmVoid() {
  const target = confirmation.value
  if (!target) return
  busy.value = true
  error.value = ''
  try {
    if (target.kind === 'obligation')
      await cancelObligation(
        workspaceId,
        target.item.id,
        target.item.version,
        auth.csrfToken,
      )
    else
      await voidRecord(
        workspaceId,
        'transactions',
        target.transaction.id,
        target.transaction.version,
        auth.csrfToken,
      )
    confirmation.value = null
    notice.value =
      target.kind === 'obligation'
        ? '应收应付已撤销，利润已重算。'
        : '结算已撤销，余额已冲销，未结金额已恢复。'
    await loadData()
  } catch (cause) {
    showError(cause)
  } finally {
    busy.value = false
  }
}

async function filterItems() {
  offset.value = 0
  await loadData()
}
async function page(delta: number) {
  offset.value += delta
  await loadData()
}
function focusRefresh() {
  if (!busy.value) void loadData()
}

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
    if (!disposed)
      unbindRefresh = bindPageRefresh(
        window,
        document,
        () => document.visibilityState === 'visible' && navigator.onLine,
        () => !busy.value && !loading.value,
        focusRefresh,
      )
  } catch (cause) {
    showError(cause)
  }
})
onUnmounted(() => {
  generation++
  disposed = true
  unbindRefresh()
})
</script>

<template>
  <main class="min-h-screen px-4 py-7 sm:px-7">
    <div class="mx-auto max-w-6xl space-y-6">
      <header class="flex flex-wrap items-center justify-between gap-4">
        <div class="flex items-center gap-3">
          <div>
            <h1 class="text-xl font-semibold">
              {{ workspace?.name || '正在加载…' }} · 商户经营
            </h1>
            <p class="mt-1 text-sm muted">
              简化管理口径 · {{ workspace?.base_currency }} · 香港时间{{
                readonly ? ' · 只读' : ''
              }}
            </p>
          </div>
        </div>
      </header>
      <p v-if="error" class="message-error" role="alert">{{ error }}</p>
      <p v-if="notice" class="message-success" role="status">{{ notice }}</p>
      <p v-if="workspace && !businessReady" class="panel rounded-2xl p-6">
        此功能用于商户工作空间。请返回工作空间页面创建或选择商户空间。
      </p>
      <template v-if="businessReady">
        <div v-if="pending" class="pending-panel rounded-xl p-4">
          <p>上次提交尚未确认；重试将使用原内容和同一个请求编号。</p>
          <button
            class="primary-button mt-3"
            :disabled="busy"
            @click="retryPending"
          >
            安全重试上次提交
          </button>
        </div>
        <form
          class="panel flex flex-wrap items-end gap-3 rounded-2xl p-4"
          @submit.prevent="loadData"
        >
          <label class="field-label">
            开始日期
            <input v-model="period.start" class="field" type="date" required />
          </label>
          <label class="field-label">
            结束日期
            <input v-model="period.end" class="field" type="date" required />
          </label>
          <button class="primary-button" :disabled="loading">
            {{ loading ? '更新中…' : '更新经营报表' }}
          </button>
        </form>
        <section v-if="report" class="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <article
            v-for="metric in [
              { label: '营业收入', value: report.revenue },
              { label: '经营成本', value: report.cost },
              { label: '经营费用', value: report.expense },
              { label: '简化利润', value: report.profit },
            ]"
            :key="metric.label"
            class="panel metric-card rounded-2xl p-5"
          >
            <p class="text-sm muted">{{ metric.label }}</p>
            <p class="mt-2 break-all text-xl font-semibold accent">
              {{ money(metric.value) }}
            </p>
            <span class="text-xs muted">{{ report.currency }}</span>
          </article>
        </section>
        <p v-if="report" class="text-sm muted">
          {{ report.start_date }} 至 {{ report.end_date }}。{{
            report.formula
          }}。{{ report.basis }}
        </p>
        <nav class="flex flex-wrap gap-2" aria-label="商户功能">
          <button
            class="tab"
            :class="{ active: tab === 'report' }"
            @click="tab = 'report'"
          >
            利润与现金流
          </button>
          <button
            class="tab"
            :class="{ active: tab === 'cash' }"
            @click="tab = 'cash'"
          >
            登记即时收支
          </button>
          <button
            class="tab"
            :class="{ active: tab === 'obligations' }"
            @click="tab = 'obligations'"
          >
            应收应付
          </button>
        </nav>

        <section v-if="tab === 'report' && report" class="space-y-6">
          <section class="panel rounded-2xl p-5 sm:p-7">
            <h2 class="text-lg font-semibold">现金流与余额核对</h2>
            <div class="mt-5 grid gap-4 sm:grid-cols-3">
              <div
                v-for="metric in [
                  { label: '本期实际流入', value: report.cash_in },
                  { label: '本期实际流出', value: report.cash_out },
                  { label: '收付净现金流', value: report.operating_cash_flow },
                  { label: '余额调整净额', value: report.adjustment_net },
                  { label: '本期净资金变动', value: report.net_cash_flow },
                  { label: '当前账面总余额', value: report.current_balance },
                ]"
                :key="metric.label"
              >
                <p class="text-sm muted">{{ metric.label }}</p>
                <p class="mt-1 text-lg">{{ money(metric.value) }}</p>
              </div>
            </div>
            <p class="mt-5 text-sm accent">
              期初 {{ money(report.opening_balance) }} + 本期净资金变动
              {{ money(report.net_cash_flow) }} = 期末
              {{ money(report.closing_balance) }}
            </p>
            <p class="mt-2 text-xs muted">
              内部转账净额为零；余额调整不计利润。未收回的应收款可以产生利润，但尚未形成现金流。
            </p>
          </section>
          <section class="grid gap-4 sm:grid-cols-2">
            <article class="panel rounded-2xl p-5">
              <h2 class="font-semibold">截至报表结束日未收回</h2>
              <p class="mt-3 text-2xl accent">
                {{ money(report.receivable_balance) }} {{ report.currency }}
              </p>
              <p class="mt-2 text-sm muted">
                其中到期日早于结束日：{{ money(report.overdue_receivable) }}
              </p>
            </article>
            <article class="panel rounded-2xl p-5">
              <h2 class="font-semibold">截至报表结束日未支付</h2>
              <p class="mt-3 text-2xl">
                {{ money(report.payable_balance) }} {{ report.currency }}
              </p>
              <p class="mt-2 text-sm muted">
                其中到期日早于结束日：{{ money(report.overdue_payable) }}
              </p>
            </article>
          </section>
          <section class="panel rounded-2xl p-5 sm:p-7">
            <h2 class="text-lg font-semibold">经营分类明细</h2>
            <p v-if="!report.categories.length" class="mt-4 muted">
              该周期没有经营记录。
            </p>
            <div
              v-for="category in report.categories"
              :key="category.category_id"
              class="member-row"
            >
              <span>
                {{ category.name }}
                <span class="text-sm muted">
                  {{ kindLabels[category.kind] }}
                </span>
              </span>
              <span>{{ money(category.amount) }}</span>
            </div>
          </section>
          <section class="panel rounded-2xl p-5 sm:p-7">
            <h2 class="text-lg font-semibold">每日经营明细</h2>
            <p class="mt-2 text-xs muted">
              仅列出有业务确认或资金变动的日期；各日合计与本期指标一致。
            </p>
            <div
              v-for="day in report.daily"
              :key="day.date"
              class="business-day"
            >
              <strong>{{ day.date }}</strong>
              <span>收入 {{ money(day.revenue) }}</span>
              <span>成本 {{ money(day.cost) }}</span>
              <span>费用 {{ money(day.expense) }}</span>
              <span class="accent">利润 {{ money(day.profit) }}</span>
              <span>净资金 {{ money(day.net_cash_flow) }}</span>
            </div>
            <p v-if="!report.daily.length" class="mt-4 muted">暂无明细。</p>
          </section>
        </section>

        <section v-if="tab === 'cash'" class="panel rounded-2xl p-5 sm:p-7">
          <h2 class="text-lg font-semibold">登记已实际收到或支付的款项</h2>
          <p class="mt-2 text-sm muted">
            已登记为应收应付的业务，请在“应收应付”内结算，不要在这里重复登记。
          </p>
          <RouterLink
            v-if="!accounts.length || !categories.length"
            class="accent mt-4 inline-block"
            :to="`/workspaces/${workspaceId}/finance?tab=accounts`"
          >
            先配置账户与经营分类 →
          </RouterLink>
          <form v-if="!readonly" class="mt-6" @submit.prevent="submitCash">
            <div class="form-grid">
              <label class="field-label">
                业务口径
                <select
                  v-model="cashDraft.kind"
                  class="field"
                  @change="cashDraft.category_id = ''"
                >
                  <option value="revenue">营业收入</option>
                  <option value="cost">经营成本</option>
                  <option value="expense">经营费用</option>
                </select>
              </label>
              <label class="field-label">
                分类
                <select v-model="cashDraft.category_id" class="field" required>
                  <option value="">选择分类</option>
                  <option
                    v-for="category in cashCategories"
                    :key="category.id"
                    :value="category.id"
                  >
                    {{ category.name }}
                  </option>
                </select>
              </label>
              <label class="field-label">
                实际收付款账户
                <select v-model="cashDraft.account_id" class="field" required>
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
              <label class="field-label">
                金额
                <input
                  v-model="cashDraft.amount"
                  class="field"
                  inputmode="decimal"
                  pattern="[0-9]+(\.[0-9]{1,2})?"
                  required
                />
              </label>
              <label class="field-label">
                发生时间（香港）
                <input
                  v-model="cashDraft.localTime"
                  class="field"
                  type="datetime-local"
                  step="1"
                  required
                />
              </label>
              <label class="field-label">
                客户或供应商
                <input
                  v-model="cashDraft.counterparty"
                  class="field"
                  maxlength="120"
                />
              </label>
              <label class="field-label">
                业务说明
                <input
                  v-model="cashDraft.description"
                  class="field"
                  maxlength="500"
                />
              </label>
            </div>
            <button class="primary-button mt-5" :disabled="busy || !!pending">
              确认即时收支
            </button>
          </form>
          <p v-else class="mt-4 muted">你具有只读权限。</p>
        </section>

        <section v-if="tab === 'obligations'" class="space-y-6">
          <form
            v-if="!readonly"
            class="panel rounded-2xl p-5 sm:p-7"
            @submit.prevent="submitObligation"
          >
            <h2 class="text-lg font-semibold">登记应收应付</h2>
            <p class="mt-2 text-sm muted">
              表示业务已经确认，但款项尚未收齐或付清。登记会计入利润，实际收付款时再逐次结算。
            </p>
            <div class="form-grid mt-5">
              <label class="field-label">
                类型
                <select
                  v-model="invoiceDraft.kind"
                  class="field"
                  @change="invoiceDraft.category_id = ''"
                >
                  <option value="receivable">应收客户款</option>
                  <option value="payable">应付供应商或费用</option>
                </select>
              </label>
              <label class="field-label">
                经营分类
                <select
                  v-model="invoiceDraft.category_id"
                  class="field"
                  required
                >
                  <option value="">选择分类</option>
                  <option
                    v-for="category in invoiceCategories"
                    :key="category.id"
                    :value="category.id"
                  >
                    {{ category.name }} ·
                    {{ kindLabels[category.business_kind] }}
                  </option>
                </select>
              </label>
              <label class="field-label">
                业务总金额
                <input
                  v-model="invoiceDraft.amount"
                  class="field"
                  inputmode="decimal"
                  pattern="[0-9]+(\.[0-9]{1,2})?"
                  required
                />
              </label>
              <label class="field-label">
                业务确认时间（香港）
                <input
                  v-model="invoiceDraft.localTime"
                  class="field"
                  type="datetime-local"
                  step="1"
                  required
                />
              </label>
              <label class="field-label">
                到期日期
                <input
                  v-model="invoiceDraft.due_date"
                  class="field"
                  type="date"
                  required
                />
              </label>
              <label class="field-label">
                客户或供应商
                <input
                  v-model="invoiceDraft.counterparty"
                  class="field"
                  required
                  maxlength="120"
                />
              </label>
              <label class="field-label">
                业务说明
                <input
                  v-model="invoiceDraft.description"
                  class="field"
                  maxlength="500"
                />
              </label>
            </div>
            <button class="primary-button mt-5" :disabled="busy || !!pending">
              确认登记业务
            </button>
          </form>
          <section class="panel rounded-2xl p-5 sm:p-7">
            <h2 class="text-lg font-semibold">应收应付台账</h2>
            <p class="mt-2 text-xs muted">
              下列状态按全部已登记结算显示；顶部报表按所选结束日计算。
            </p>
            <form
              class="mt-5 flex flex-wrap items-end gap-3"
              @submit.prevent="filterItems"
            >
              <label class="field-label">
                类型
                <select v-model="filters.kind" class="field">
                  <option value="">全部</option>
                  <option value="receivable">应收</option>
                  <option value="payable">应付</option>
                </select>
              </label>
              <label class="field-label">
                结算状态
                <select v-model="filters.state" class="field">
                  <option value="">全部</option>
                  <option value="unsettled">未结算</option>
                  <option value="partial">部分结算</option>
                  <option value="settled">已结清</option>
                  <option value="voided">已撤销</option>
                </select>
              </label>
              <button class="primary-button" :disabled="loading">筛选</button>
            </form>
            <p v-if="!items.length" class="mt-6 muted">
              当前筛选下没有应收应付。
            </p>
            <div v-for="item in items" :key="item.id" class="ledger-row">
              <div>
                <p class="font-semibold">
                  {{ kindLabels[item.kind] }} · {{ item.counterparty }}
                </p>
                <p class="mt-1 text-sm muted">
                  {{ categoryName(item.category_id) }} · 到期
                  {{ item.due_date }} · {{ kindLabels[item.settlement_status] }}
                  <span v-if="item.is_overdue" class="error">已逾期</span>
                </p>
                <p class="mt-1 text-sm muted">
                  业务确认 {{ displayDateTime(item.recognized_at) }} ·
                  {{ item.description }}
                </p>
              </div>
              <div class="text-right">
                <p>
                  未结 {{ money(item.remaining_amount) }} {{ item.currency }}
                </p>
                <p class="mt-1 text-xs muted">
                  总额 {{ money(item.amount) }} · 已结
                  {{ money(item.paid_amount) }}
                </p>
                <button class="accent mt-2 text-sm" @click="openItem(item)">
                  查看与结算
                </button>
              </div>
            </div>
            <div class="mt-5 flex justify-between text-sm">
              <span class="muted">本页 {{ items.length }} 条</span>
              <div class="flex gap-4">
                <button :disabled="offset === 0 || loading" @click="page(-50)">
                  上一页
                </button>
                <button
                  :disabled="items.length < 50 || loading"
                  @click="page(50)"
                >
                  下一页
                </button>
              </div>
            </div>
          </section>
          <section v-if="selected" class="panel rounded-2xl p-5 sm:p-7">
            <div class="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 class="text-xl font-semibold">
                  {{ kindLabels[selected.kind] }} · {{ selected.counterparty }}
                </h2>
                <p class="mt-2 text-sm muted">
                  原金额 {{ money(selected.amount) }} · 未结
                  {{ money(selected.remaining_amount) }} ·
                  {{ kindLabels[selected.settlement_status] }}
                </p>
              </div>
              <button class="text-sm muted" @click="selected = null">
                收起详情
              </button>
            </div>
            <form
              v-if="
                !readonly &&
                selected.status === 'posted' &&
                selected.settlement_status !== 'settled'
              "
              class="mt-6"
              @submit.prevent="submitSettlement"
            >
              <div class="form-grid">
                <label class="field-label">
                  {{ selected.kind === 'receivable' ? '收款' : '付款' }}账户
                  <select
                    v-model="settleDraft.account_id"
                    class="field"
                    required
                  >
                    <option
                      v-for="account in activeAccounts"
                      :key="account.id"
                      :value="account.id"
                    >
                      {{ account.name }}
                    </option>
                  </select>
                </label>
                <label class="field-label">
                  本次结算金额
                  <input
                    v-model="settleDraft.amount"
                    class="field"
                    inputmode="decimal"
                    pattern="[0-9]+(\.[0-9]{1,2})?"
                    required
                  />
                </label>
                <label class="field-label">
                  实际收付款时间（香港）
                  <input
                    v-model="settleDraft.localTime"
                    class="field"
                    type="datetime-local"
                    step="1"
                    required
                  />
                </label>
              </div>
              <button class="primary-button mt-5" :disabled="busy || !!pending">
                确认本次结算
              </button>
            </form>
            <h3 class="mt-7 font-semibold">结算流水</h3>
            <p v-if="!settlements.length" class="mt-3 text-sm muted">
              尚未结算。
            </p>
            <div
              v-for="transaction in settlements"
              :key="transaction.id"
              class="member-row"
            >
              <span class="text-sm muted">
                {{ accountName(transaction.account_id) }} ·
                {{ displayDateTime(transaction.occurred_at) }} ·
                {{ transaction.status === 'posted' ? '有效' : '已撤销' }}
              </span>
              <div class="flex flex-wrap gap-4">
                <span>{{ money(transaction.amount) }}</span>
                <button
                  v-if="!readonly && transaction.status === 'posted'"
                  class="error text-sm"
                  :disabled="busy"
                  @click="confirmation = { kind: 'settlement', transaction }"
                >
                  撤销此结算
                </button>
              </div>
            </div>
            <button
              v-if="!readonly && selected.status === 'posted'"
              class="error mt-6 text-sm"
              :disabled="busy || selected.paid_amount !== '0.00'"
              @click="confirmation = { kind: 'obligation', item: selected }"
            >
              撤销此笔应收应付（须先撤销全部有效结算）
            </button>
          </section>
        </section>
        <p class="text-xs leading-6 muted">
          本阶段采用简化管理口径，不做库存结转、税费拆分、折旧或正式会计报表。借款、注资及对账调整应单独处理，不计营业收入或利润。
        </p>
      </template>
    </div>
    <div
      v-if="confirmation"
      class="modal-backdrop"
      @click.self="confirmation = null"
    >
      <section
        class="panel w-full max-w-md rounded-2xl p-6"
        role="dialog"
        aria-modal="true"
        aria-label="确认撤销商户记录"
      >
        <h2 class="text-xl font-semibold">
          确认撤销{{
            confirmation.kind === 'settlement' ? '这次结算' : '这笔应收应付'
          }}？
        </h2>
        <p class="mt-4 leading-7 muted">
          {{
            confirmation.kind === 'settlement'
              ? '将追加反向资金变动并恢复未结余额，业务确认的利润不变。'
              : '业务金额将从利润中移除，原记录和操作痕迹会保留。'
          }}
        </p>
        <div class="mt-6 flex gap-4">
          <button class="primary-button" :disabled="busy" @click="confirmVoid">
            确认撤销
          </button>
          <button :disabled="busy" @click="confirmation = null">取消</button>
        </div>
      </section>
    </div>
  </main>
</template>
