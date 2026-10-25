<script setup lang="ts">
import { bindPageRefresh } from '../utils/pageRefresh'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AnalyticsChart from '../components/AnalyticsChart.vue'
import { getDashboard, type Dashboard } from '../api/analytics'
import { ApiError } from '../api/client'
import { getWorkspace, listWorkspaces, type Workspace } from '../api/workspaces'
import { useAuthStore } from '../stores/auth'
import {
  positiveCategories,
  presetPeriod,
  spendingCategories,
  type PeriodPreset,
} from '../utils/analytics'
import { categoryChart, dailyChart } from '../utils/charts'
import { localDateTime, money } from '../utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const workspaceId = String(route.params.workspaceId)
const workspace = ref<Workspace | null>(null)
const workspaces = ref<Workspace[]>([])
const report = ref<Dashboard | null>(null)
const loading = ref(true)
const error = ref('')
const preset = ref<PeriodPreset | 'custom'>('month')
const period = reactive(presetPeriod('month', localDateTime().slice(0, 10)))
const business = computed(() => workspace.value?.type === 'business')
const base = `/workspaces/${workspaceId}`
let generation = 0
let controller: AbortController | undefined
let disposed = false
const categoryRows = computed(() =>
  spendingCategories(report.value?.categories || []),
)
const hasPositiveCategories = computed(
  () => positiveCategories(report.value?.categories || []).length > 0,
)
const overviewChart = computed(() =>
  report.value
    ? dailyChart(report.value, [
        {
          name: business.value ? '营业收入' : '净收入',
          key: 'income',
          type: 'bar',
        },
        {
          name: business.value ? '成本与费用' : '净支出',
          key: 'expense',
          type: 'bar',
        },
        {
          name: business.value ? '简化利润' : '结余',
          key: 'net',
          type: 'line',
        },
      ])
    : {},
)
const flowChart = computed(() =>
  report.value
    ? dailyChart(report.value, [
        { name: '实际收款', key: 'cash_in', type: 'bar' },
        { name: '实际付款', key: 'cash_out', type: 'bar' },
        { name: '净资金变动', key: 'net_cash_flow', type: 'line' },
      ])
    : {},
)
const balanceChart = computed(() =>
  report.value
    ? dailyChart(report.value, [
        { name: '日末总余额', key: 'closing_balance', type: 'line' },
      ])
    : {},
)
const distributionChart = computed(() =>
  report.value ? categoryChart(report.value) : {},
)
const metrics = computed(() => {
  const r = report.value
  if (!r) return []
  return [
    {
      label: business.value ? '营业收入' : '净收入',
      value: r.income,
      hint: business.value ? '按业务确认口径' : '已扣收入退款',
      color: 'accent',
    },
    {
      label: business.value ? '成本与费用' : '净支出',
      value: r.expense,
      hint: business.value
        ? `成本 ${money(r.cost!)} · 费用 ${money(r.operating_expense!)}`
        : '已扣支出退款',
      color: '',
    },
    {
      label: business.value ? '简化利润' : '期间结余',
      value: r.net,
      hint: business.value ? '营业收入 − 成本 − 费用' : '净收入 − 净支出',
      color: 'accent',
    },
    {
      label: '净资金变动',
      value: r.net_cash_flow,
      hint: '实际收款 − 实际付款 + 调整',
      color: '',
    },
  ]
})

function showError(cause: unknown) {
  report.value = null
  error.value = cause instanceof Error ? cause.message : '加载失败，请重试'
  if (cause instanceof ApiError && cause.status === 401) {
    auth.user = null
    auth.csrfToken = ''
    void router.replace('/')
  }
}

async function loadData() {
  if (!workspace.value || disposed) return
  const request = ++generation
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const result = await getDashboard(
      workspaceId,
      period.start,
      period.end,
      AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    )
    if (request === generation && !disposed) report.value = result
  } catch (cause) {
    if (request === generation && !disposed) showError(cause)
  } finally {
    if (request === generation && !disposed) loading.value = false
  }
}

function choosePreset(value: PeriodPreset) {
  preset.value = value
  Object.assign(period, presetPeriod(value, localDateTime().slice(0, 10)))
  void loadData()
}
function switchWorkspace(event: Event) {
  const id = (event.target as HTMLSelectElement).value
  if (id !== workspaceId) void router.push(`/workspaces/${id}/dashboard`)
}
let unbindRefresh = () => {}
function refreshVisible() {
  if (document.visibilityState === 'visible' && !loading.value) void loadData()
}
onMounted(async () => {
  try {
    if (!auth.initialized) await auth.initialize()
    if (!auth.user) {
      await router.replace('/')
      return
    }
    const [current, all] = await Promise.all([
      getWorkspace(workspaceId),
      listWorkspaces(),
    ])
    if (disposed) return
    workspace.value = current
    workspaces.value = all
    await loadData()
  } catch (cause) {
    if (!disposed) {
      showError(cause)
      loading.value = false
    }
  }
  if (!disposed) {
    unbindRefresh = bindPageRefresh(
      window,
      document,
      () => document.visibilityState === 'visible' && navigator.onLine,
      () => !loading.value,
      refreshVisible,
    )
  }
})
onUnmounted(() => {
  disposed = true
  generation++
  controller?.abort()
  unbindRefresh()
})
</script>

<template>
  <div class="dashboard-shell">
    <main class="dashboard-main">
      <header class="dashboard-header">
        <div>
          <p class="eyebrow">FINSCOPE / OVERVIEW</p>
          <h1 class="mt-2 text-2xl font-bold">
            {{ business ? '商户经营总览' : '个人财务总览' }}
          </h1>
          <p class="muted mt-2 text-sm">看清收支、资金与每一天的变化。</p>
        </div>
        <label class="field-label dashboard-switch">
          工作空间
          <select
            class="field"
            :value="workspaceId"
            :disabled="!workspaces.length"
            @change="switchWorkspace"
          >
            <option v-for="item in workspaces" :key="item.id" :value="item.id">
              {{ item.name }} · {{ item.type === 'business' ? '商户' : '个人' }}
            </option>
          </select>
        </label>
      </header>

      <section class="panel dashboard-section" aria-label="统计时间筛选">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-wrap gap-2">
            <button
              v-for="item in [
                ['month', '本月'],
                ['last-month', '上月'],
                ['90-days', '近 90 天'],
              ] as const"
              :key="item[0]"
              class="tab"
              :class="{ active: preset === item[0] }"
              :aria-pressed="preset === item[0]"
              @click="choosePreset(item[0])"
            >
              {{ item[1] }}
            </button>
          </div>
          <RouterLink
            :to="
              workspace?.role === 'viewer'
                ? `${base}/finance`
                : `${base}/finance?quick=1#quick-entry`
            "
            class="primary-button"
          >
            {{ workspace?.role === 'viewer' ? '查看流水' : '+ 记一笔' }}
          </RouterLink>
        </div>
        <form class="dashboard-dates mt-4" @submit.prevent="loadData">
          <label class="field-label">
            开始日期
            <input
              v-model="period.start"
              class="field"
              type="date"
              required
              @input="preset = 'custom'"
            />
          </label>
          <label class="field-label">
            结束日期
            <input
              v-model="period.end"
              class="field"
              type="date"
              required
              :min="period.start"
              @input="preset = 'custom'"
            />
          </label>
          <button class="primary-button" :disabled="loading">
            {{ loading ? '加载中…' : '查询 / 刷新' }}
          </button>
        </form>
        <p class="muted mt-3 text-xs">
          香港时间 · 含首尾日期 · 单次最多 366 天
        </p>
      </section>
      <p v-if="loading" role="status" class="status-panel rounded-xl p-6">
        正在获取统计与图表…
      </p>
      <section v-else-if="error" role="alert" class="message-error">
        <p>{{ error }}</p>
        <button v-if="workspace" class="mt-3 underline" @click="loadData">
          重试加载
        </button>
        <RouterLink v-else to="/" class="mt-3 block underline">
          返回工作空间
        </RouterLink>
      </section>
      <template v-else-if="report">
        <p class="muted text-sm" aria-live="polite">
          当前结果：{{ report.start_date }} — {{ report.end_date }} ·
          {{ report.currency }}
        </p>
        <section
          v-if="!report.has_activity"
          class="status-panel rounded-xl p-5"
        >
          <h2 class="font-bold">所选期间暂无收支或业务记录</h2>
          <p class="muted mt-2 text-sm">
            可以调整日期，或点击“记一笔”开始。已有账户余额仍会展示；内部转账不会计入收支。
          </p>
        </section>
        <section class="dashboard-metrics" aria-label="期间核心指标">
          <article
            v-for="metric in metrics"
            :key="metric.label"
            class="panel dashboard-section metric-card"
          >
            <h2 class="muted text-sm">{{ metric.label }}</h2>
            <p class="metric-value" :class="metric.color">
              {{ money(metric.value) }}
            </p>
            <p class="muted text-xs">{{ metric.hint }}</p>
          </article>
        </section>
        <div class="dashboard-chart-grid">
          <section class="panel dashboard-section">
            <h2 class="font-bold">
              {{ business ? '营业收入与利润趋势' : '收支与结余趋势' }}
            </h2>
            <p class="muted mt-2 text-xs">
              按日汇总 · 可拖动底部滑块查看局部日期
            </p>
            <AnalyticsChart
              :option="overviewChart"
              :label="
                business
                  ? '每日营业收入、成本费用和利润趋势'
                  : '每日净收入、净支出和结余趋势'
              "
            />
          </section>
          <section class="panel dashboard-section">
            <h2 class="font-bold">
              {{ business ? '成本与费用分类占比' : '支出分类占比' }}
            </h2>
            <AnalyticsChart
              v-if="hasPositiveCategories"
              :option="distributionChart"
              label="正数净支出分类占比，精确金额见下方分类明细"
            />
            <p v-else class="muted py-16 text-center">暂无正数净支出分类</p>
            <p class="muted text-xs">
              圆环仅展示正数分类；退款可能使分类净额为零或负数，全部金额见分类明细。
            </p>
          </section>
          <section class="panel dashboard-section">
            <h2 class="font-bold">实际收付款与资金变动</h2>
            <p class="muted mt-2 text-xs">
              含退款实际收付，余额调整计入净资金变动
            </p>
            <AnalyticsChart
              :option="flowChart"
              label="每日实际收款、付款与净资金变动"
            />
          </section>
          <section class="panel dashboard-section">
            <h2 class="font-bold">账户总余额变化</h2>
            <p class="muted mt-2 text-xs">
              期初 {{ money(report.opening_balance) }} → 期末
              {{ money(report.closing_balance) }} {{ report.currency }}
            </p>
            <AnalyticsChart
              :option="balanceChart"
              label="所选期间每日日末账户总余额"
            />
          </section>
        </div>
        <div class="dashboard-chart-grid">
          <section class="panel dashboard-section">
            <h2 class="font-bold">
              {{ business ? '成本与费用分类明细' : '支出分类明细' }}
            </h2>
            <p v-if="!categoryRows.length" class="muted mt-5">
              所选期间暂无分类支出。
            </p>
            <div
              v-for="item in categoryRows"
              :key="item.category_id"
              class="ledger-row"
            >
              <span class="break-words">
                {{ item.name }}
                <small v-if="business" class="muted ml-2">
                  {{ item.kind === 'cost' ? '成本' : '费用' }}
                </small>
              </span>
              <strong class="shrink-0">
                {{ money(item.amount) }} {{ report.currency }}
              </strong>
            </div>
          </section>
          <section class="panel dashboard-section">
            <h2 class="font-bold">当前账户</h2>
            <p class="muted mt-2 text-xs">
              当前全部有效流水计算的余额，不受日期筛选影响，含已停用账户。
            </p>
            <p class="metric-value accent">
              {{ money(report.current_balance) }}
              <small class="text-sm">{{ report.currency }}</small>
            </p>
            <p v-if="!report.accounts.length" class="muted">
              还没有账户，前往“账户与分类”创建。
            </p>
            <div
              v-for="item in report.accounts"
              :key="item.id"
              class="ledger-row"
            >
              <span class="break-words">
                {{ item.name }}
                <small v-if="!item.is_active" class="muted ml-2">已停用</small>
              </span>
              <strong class="shrink-0">
                {{ money(item.balance) }} {{ item.currency }}
              </strong>
            </div>
          </section>
        </div>
        <section v-if="business" class="panel dashboard-section">
          <h2 class="font-bold">期末往来余额</h2>
          <div class="dashboard-metrics mt-4">
            <div>
              <p class="muted">应收未收</p>
              <p class="metric-value">
                {{ money(report.receivable_balance!) }}
              </p>
            </div>
            <div>
              <p class="muted">应付未付</p>
              <p class="metric-value">{{ money(report.payable_balance!) }}</p>
            </div>
          </div>
          <RouterLink :to="`${base}/business`" class="accent text-sm">
            管理应收应付与结算 →
          </RouterLink>
        </section>
        <section class="panel dashboard-section">
          <h2 class="font-bold">统计口径与余额核对</h2>
          <p class="muted mt-3 text-sm leading-7">
            {{ report.basis }}
            <br />
            {{ report.formula }}
          </p>
          <p class="mt-3 text-sm leading-7 break-words">
            期初 {{ money(report.opening_balance) }} + 净资金变动
            {{ money(report.net_cash_flow) }} = 期末
            {{ money(report.closing_balance) }} {{ report.currency }}
            <br />
            <span class="muted">
              其中余额调整
              {{
                money(report.adjustment_net)
              }}；内部转账不影响空间总余额。历史报表按当前有效记录重算。
            </span>
          </p>
        </section>
        <details class="panel dashboard-section">
          <summary class="cursor-pointer font-bold">
            查看每日精确金额（{{ report.daily.length }} 天）
          </summary>
          <p class="muted mt-3 text-xs">
            图表坐标仅作趋势参考，金额以卡片、提示和此处明细为准。
          </p>
          <article
            v-for="day in report.daily"
            :key="day.date"
            class="dashboard-day"
          >
            <h3 class="font-bold">{{ day.date }}</h3>
            <span>
              {{ business ? '营业收入' : '净收入' }} {{ money(day.income) }}
            </span>
            <span>
              {{ business ? '成本费用' : '净支出' }} {{ money(day.expense) }}
            </span>
            <span>{{ business ? '利润' : '结余' }} {{ money(day.net) }}</span>
            <span>净资金变动 {{ money(day.net_cash_flow) }}</span>
            <span>日末余额 {{ money(day.closing_balance) }}</span>
          </article>
        </details>
      </template>
    </main>
  </div>
</template>
