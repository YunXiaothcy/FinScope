<script setup lang="ts">
import { requestKey } from '../utils/requestKey'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import {
  getAccounts,
  getCategories,
  type Account,
  type Category,
} from '../api/finance'
import {
  cancelImport,
  confirmImport,
  getImport,
  listImports,
  previewImport,
  uploadImport,
  type ConfirmInput,
  type ImportBatch,
  type ImportMapping,
  type ImportSummary,
} from '../api/imports'
import { getWorkspace, type Workspace } from '../api/workspaces'
import { useAuthStore } from '../stores/auth'
import { displayDateTime, localDateTime, money } from '../utils/format'
import {
  columnFields,
  sampleCSV,
  sourceLabels,
  suggestColumns,
  type ColumnKey,
} from '../utils/imports'

interface Pending {
  batchId: string
  key: string
  input: ConfirmInput
}
const workspaceId = String(useRoute().params.workspaceId)
const base = `/workspaces/${workspaceId}`
const router = useRouter()
const auth = useAuthStore()
const workspace = ref<Workspace | null>(null)
const accounts = ref<Account[]>([])
const categories = ref<Category[]>([])
const history = ref<ImportSummary[]>([])
const batch = ref<ImportBatch | null>(null)
const file = ref<File | null>(null)
const busy = ref(false)
const initializing = ref(true)
const error = ref('')
const notice = ref('')
const selected = ref<string[]>([])
const pending = ref<Pending | null>(null)
const confirmation = ref<'confirm' | 'cancel' | null>(null)
const previewSignature = ref('')
const rowFilter = ref('all')
const page = ref(0)
const PAGE_SIZE = 30
let disposed = false
const readonly = computed(() => workspace.value?.role === 'viewer')
const activeAccounts = computed(() =>
  accounts.value.filter((item) => item.is_active),
)
const labels: Record<string, string> = {
  draft: '待处理',
  committed: '已完成',
  cancelled: '已取消',
  expired: '已过期',
  uploaded: '未校验',
  ready: '可入账',
  error: '有错误',
  duplicate: '重复跳过',
  imported: '已入账',
  skipped: '未选择',
  income: '收入',
  expense: '支出',
}
function defaultMapping(): ImportMapping {
  return {
    account_id: activeAccounts.value[0]?.id || '',
    source_name: '账单导入',
    date_column: -1,
    amount_column: -1,
    type_column: null,
    category_column: null,
    merchant_column: null,
    description_column: null,
    external_id_column: null,
    currency_column: null,
    fixed_type: 'expense',
    date_format: 'ymd',
    default_income_category_id: null,
    default_expense_category_id: null,
    category_map: Object.create(null),
    type_map: Object.create(null),
  }
}
const mapping = ref<ImportMapping>(defaultMapping())
const dirty = computed(
  () =>
    !batch.value?.mapping ||
    JSON.stringify(mapping.value) !== previewSignature.value,
)
const editable = computed(
  () => batch.value?.status === 'draft' && !readonly.value && !pending.value,
)
const categoryLabels = computed(() =>
  sourceLabels(batch.value?.rows || [], mapping.value.category_column),
)
const typeLabels = computed(() =>
  sourceLabels(batch.value?.rows || [], mapping.value.type_column),
)
const readyRows = computed(
  () => batch.value?.rows.filter((row) => row.status === 'ready') || [],
)
const filteredRows = computed(
  () =>
    batch.value?.rows.filter(
      (row) => rowFilter.value === 'all' || row.status === rowFilter.value,
    ) || [],
)
const visibleRows = computed(() =>
  filteredRows.value.slice(
    page.value * PAGE_SIZE,
    (page.value + 1) * PAGE_SIZE,
  ),
)
const accountName = (id: string) =>
  accounts.value.find((item) => item.id === id)?.name || '账户'
const storageKey = () =>
  `finscope.pending.import.${auth.user?.id}.${workspaceId}`

function showError(cause: unknown) {
  if (disposed) return
  error.value = cause instanceof Error ? cause.message : '操作失败，请重试'
  if (cause instanceof ApiError && cause.status === 401) {
    auth.user = null
    auth.csrfToken = ''
    void router.replace('/')
  }
}
function applyBatch(value: ImportBatch) {
  if (disposed) return
  batch.value = value
  mapping.value = value.mapping
    ? {
        ...value.mapping,
        category_map: Object.assign(
          Object.create(null),
          value.mapping.category_map,
        ),
        type_map: Object.assign(Object.create(null), value.mapping.type_map),
      }
    : { ...defaultMapping(), ...suggestColumns(value.headers) }
  if (!value.mapping) {
    for (const label of sourceLabels(
      value.rows,
      mapping.value.category_column,
    )) {
      const matches = categories.value.filter((item) => item.name === label)
      if (matches.length === 1)
        mapping.value.category_map[label] = matches[0]!.id
    }
  }
  previewSignature.value = value.mapping ? JSON.stringify(mapping.value) : ''
  selected.value = value.rows
    .filter((row) => row.status === 'ready')
    .map((row) => row.id)
  page.value = 0
  rowFilter.value = 'all'
}
async function refreshHistory() {
  const value = await listImports(workspaceId)
  if (!disposed) history.value = value
}
async function openBatch(id: string) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    applyBatch(await getImport(workspaceId, id))
  } catch (cause) {
    showError(cause)
  } finally {
    busy.value = false
  }
}
function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null
}
async function upload() {
  if (!file.value || readonly.value || pending.value || busy.value) return
  if (
    !/\.(csv|xlsx)$/i.test(file.value.name) ||
    file.value.size === 0 ||
    file.value.size > 2 * 1024 * 1024
  ) {
    error.value = '请选择不超过 2 MiB 的非空 CSV/XLSX 文件'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    applyBatch(await uploadImport(workspaceId, file.value, auth.csrfToken))
    await refreshHistory()
    notice.value =
      batch.value?.status === 'committed'
        ? '此文件已经处理过，已打开原导入报告。'
        : '文件已上传，尚未入账。请检查下方字段映射。'
  } catch (cause) {
    showError(cause)
  } finally {
    busy.value = false
  }
}
function setColumn(key: ColumnKey, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (key === 'date_column' || key === 'amount_column') {
    mapping.value[key] = value === '' ? -1 : Number(value)
  } else {
    mapping.value[key] = value === '' ? null : Number(value)
  }
}
async function preview() {
  if (!batch.value || busy.value || !editable.value) return
  if (
    mapping.value.date_column < 0 ||
    mapping.value.amount_column < 0 ||
    !mapping.value.account_id
  ) {
    error.value = '请选择入账账户、日期列和金额列'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const current = batch.value
    const value = await previewImport(
      workspaceId,
      current.id,
      current.version,
      mapping.value,
      auth.csrfToken,
    )
    applyBatch(value)
    await refreshHistory()
    notice.value =
      '预览已更新，尚未入账。请核对可入账行，错误和重复行不会入账。'
  } catch (cause) {
    showError(cause)
  } finally {
    busy.value = false
  }
}
function remember(value: Pending | null) {
  pending.value = value
  try {
    if (value) sessionStorage.setItem(storageKey(), JSON.stringify(value))
    else sessionStorage.removeItem(storageKey())
  } catch {
    if (value)
      notice.value = '浏览器无法暂存重试状态，请在当前页面等待提交结果。'
  }
}
async function retryPending() {
  if (!pending.value || busy.value || readonly.value) return
  const current = pending.value
  busy.value = true
  error.value = ''
  confirmation.value = null
  try {
    const result = await confirmImport(
      workspaceId,
      current.batchId,
      current.input,
      current.key,
      auth.csrfToken,
    )
    remember(null)
    if (batch.value?.id === result.id)
      batch.value = { ...batch.value, ...result }
    notice.value = `处理完成：已入账 ${result.counts.imported || 0} 行，重复 ${result.counts.duplicate || 0} 行，错误 ${result.counts.error || 0} 行，未选择 ${result.counts.skipped || 0} 行。`
    applyBatch(await getImport(workspaceId, result.id))
    await refreshHistory()
  } catch (cause) {
    if (
      cause instanceof ApiError &&
      [403, 404, 409, 410, 422].includes(cause.status)
    )
      remember(null)
    showError(cause)
  } finally {
    busy.value = false
  }
}
async function commit() {
  if (!batch.value || dirty.value || !selected.value.length || !editable.value)
    return
  remember({
    batchId: batch.value.id,
    key: requestKey(),
    input: {
      version: batch.value.version,
      row_ids: [...selected.value].sort(),
    },
  })
  await retryPending()
}
async function cancel() {
  if (!batch.value || !editable.value || busy.value) return
  busy.value = true
  confirmation.value = null
  error.value = ''
  try {
    await cancelImport(
      workspaceId,
      batch.value.id,
      batch.value.version,
      auth.csrfToken,
    )
    applyBatch(await getImport(workspaceId, batch.value.id))
    await refreshHistory()
    notice.value = '批次已取消，暂存行已清理，没有新增账务。'
  } catch (cause) {
    showError(cause)
  } finally {
    busy.value = false
  }
}
function downloadSample() {
  const url = URL.createObjectURL(
    new Blob([sampleCSV(localDateTime().slice(0, 10))], {
      type: 'text/csv;charset=utf-8',
    }),
  )
  const link = document.createElement('a')
  link.href = url
  link.download = 'FinScope-demo.csv'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
onMounted(async () => {
  try {
    if (!auth.initialized) await auth.initialize()
    if (!auth.user) {
      await router.replace('/')
      return
    }
    const result = await Promise.all([
      getWorkspace(workspaceId),
      getAccounts(workspaceId),
      getCategories(workspaceId),
      listImports(workspaceId),
    ])
    if (disposed) return
    workspace.value = result[0]
    accounts.value = result[1]
    categories.value = result[2]
    history.value = result[3]
    const saved = sessionStorage.getItem(storageKey())
    if (saved) {
      try {
        const value = JSON.parse(saved) as Pending
        if (
          /^[a-zA-Z0-9_-]{8,80}$/.test(value.key) &&
          /^[a-f0-9-]{36}$/.test(value.batchId) &&
          Array.isArray(value.input?.row_ids) &&
          value.input.row_ids.length &&
          Number.isInteger(value.input.version)
        ) {
          pending.value = value
          applyBatch(await getImport(workspaceId, value.batchId))
        } else sessionStorage.removeItem(storageKey())
      } catch (cause) {
        if (cause instanceof ApiError) showError(cause)
        else sessionStorage.removeItem(storageKey())
      }
    }
  } catch (cause) {
    showError(cause)
  } finally {
    initializing.value = false
  }
})
onUnmounted(() => {
  disposed = true
})
</script>

<template>
  <main class="mx-auto max-w-7xl px-4 py-8 sm:px-8">
    <header class="mb-6 flex flex-wrap items-center justify-between gap-4">
      <div>
        <p class="eyebrow">FINSCOPE / IMPORT</p>
        <h1 class="mt-2 text-2xl font-bold">账单导入</h1>
        <p class="muted mt-2 text-sm">
          {{ workspace?.name }} · 上传 → 映射 → 预览 → 确认入账
        </p>
      </div>
    </header>
    <p v-if="initializing" role="status" class="status-panel rounded-xl p-5">
      正在加载导入工作台…
    </p>
    <p v-if="error" role="alert" class="message-error mb-5">{{ error }}</p>
    <p v-if="notice" role="status" class="message-success mb-5">{{ notice }}</p>
    <section v-if="pending" class="pending-panel mb-5 rounded-xl p-5">
      <h2 class="font-bold">有一批提交结果待确认</h2>
      <p class="mt-2 text-sm">
        请求编号已保留。请安全重试原提交；重复点击不会重复入账。
      </p>
      <button
        class="primary-button mt-4"
        :disabled="busy || readonly"
        @click="retryPending"
      >
        {{ busy ? '正在核实…' : '安全重试上次确认' }}
      </button>
    </section>
    <template v-if="workspace">
      <section class="panel mb-5 rounded-2xl p-5 sm:p-7">
        <div class="flex flex-wrap justify-between gap-3">
          <h2 class="font-bold">1. 上传账单</h2>
          <button class="accent text-sm" @click="downloadSample">
            下载虚构示例 CSV
          </button>
        </div>
        <p class="muted mt-3 text-sm leading-7">
          支持 UTF-8 / GB18030 CSV 和普通
          XLSX（只读第一张工作表）。首行必须是表头；每批最多 2 MiB、1000
          行数据、40
          列。只导入已实际发生的收入与支出，金额填正数。转账、退款、借还款及应收应付结算请使用对应账务功能。
        </p>
        <p v-if="readonly" class="muted mt-4">
          当前为只读成员，可查看批次和报告，不能上传或确认。
        </p>
        <form
          v-else
          class="mt-4 flex flex-wrap items-center gap-4"
          @submit.prevent="upload"
        >
          <input
            aria-label="选择 CSV 或 XLSX 文件"
            type="file"
            accept=".csv,.xlsx"
            class="field min-w-0 flex-1"
            :disabled="busy || !!pending"
            @change="chooseFile"
          />
          <button class="primary-button" :disabled="!file || busy || !!pending">
            {{ busy ? '处理中…' : '上传并读取' }}
          </button>
        </form>
        <p
          v-if="!readonly && (!activeAccounts.length || !categories.length)"
          class="mt-3 text-sm"
        >
          <RouterLink :to="`${base}/finance?tab=accounts`" class="accent">
            先到账户与分类创建入账账户及收支分类 →
          </RouterLink>
        </p>
        <p class="muted mt-3 text-xs leading-6">
          上传与预览不会记账。原文件不保存；原始行暂存 72
          小时，到期访问导入页时清理。已入账记录和去重标记会保留。
        </p>
      </section>
      <section class="panel mb-5 rounded-2xl p-5 sm:p-7">
        <h2 class="font-bold">最近导入批次</h2>
        <p v-if="!history.length" class="muted mt-4">还没有导入记录。</p>
        <div v-for="item in history" :key="item.id" class="ledger-row">
          <div class="break-words">
            <p>{{ item.filename }}</p>
            <p class="muted mt-1 text-xs">
              {{ displayDateTime(item.created_at) }} ·
              {{ labels[item.status] }} · {{ item.total }} 行 · 已入账
              {{ item.counts.imported || 0 }} 行
            </p>
          </div>
          <button
            class="accent shrink-0"
            :disabled="busy"
            @click="openBatch(item.id)"
          >
            打开{{ item.status === 'committed' ? '报告' : '批次' }}
          </button>
        </div>
      </section>
      <template v-if="batch">
        <section class="status-panel mb-5 rounded-xl p-5">
          <h2 class="break-words font-bold">
            {{ batch.filename }} · {{ labels[batch.status] }}
          </h2>
          <p class="muted mt-2 text-sm">
            {{ batch.total }} 行 · {{ batch.format.toUpperCase() }}
            {{ batch.encoding }}
            {{ batch.sheet_name ? `· 第一张表：${batch.sheet_name}` : '' }}
          </p>
          <p class="muted mt-2 text-xs">
            原始行清理时间：{{
              displayDateTime(batch.expires_at)
            }}（到期后访问触发）
          </p>
        </section>
        <form
          v-if="editable"
          class="panel mb-5 rounded-2xl p-5 sm:p-7"
          @submit.prevent="preview"
        >
          <fieldset :disabled="busy">
            <legend class="mb-5 font-bold">2. 检查字段映射</legend>
            <div class="form-grid">
              <label class="field-label">
                入账账户 *
                <select v-model="mapping.account_id" class="field" required>
                  <option value="">请选择账户</option>
                  <option
                    v-for="item in activeAccounts"
                    :key="item.id"
                    :value="item.id"
                  >
                    {{ item.name }} · {{ item.currency }}
                  </option>
                </select>
              </label>
              <label class="field-label">
                账单来源名称
                <input
                  v-model="mapping.source_name"
                  class="field"
                  required
                  maxlength="80"
                />
                <span class="muted mt-2 block text-xs">
                  同一渠道后续导入保持名称一致，用于单号去重。
                </span>
              </label>
              <label class="field-label">
                日期顺序
                <select v-model="mapping.date_format" class="field">
                  <option value="ymd">年-月-日 / ISO 日期时间</option>
                  <option value="dmy">日-月-年</option>
                  <option value="mdy">月-日-年</option>
                </select>
              </label>
              <label
                v-for="field in columnFields"
                :key="field.key"
                class="field-label"
              >
                {{ field.label }}
                <select
                  class="field"
                  :value="mapping[field.key] ?? ''"
                  @change="setColumn(field.key, $event)"
                >
                  <option
                    :value="
                      field.key === 'date_column' ||
                      field.key === 'amount_column'
                        ? -1
                        : ''
                    "
                  >
                    不映射
                  </option>
                  <option
                    v-for="(header, index) in batch.headers"
                    :key="index"
                    :value="index"
                  >
                    {{ index + 1 }}. {{ header }}
                  </option>
                </select>
              </label>
            </div>
            <label v-if="mapping.type_column === null" class="field-label mt-5">
              没有类型列时，整批按以下类型处理
              <select v-model="mapping.fixed_type" class="field">
                <option value="expense">支出</option>
                <option value="income">收入</option>
              </select>
              <span class="muted mt-2 block text-xs">
                请确认文件每行均属于此类型；有收支混合、转账或退款的文件必须映射类型列后逐行核对。
              </span>
            </label>
            <div v-if="mapping.type_column !== null" class="mt-5">
              <h3 class="font-bold">收支值对应关系</h3>
              <p class="muted mt-2 text-xs">
                收入 / 支出 / income / expense / 收款 /
                付款可以直接识别。其他值请明确选择；转账和退款等仍会被拒绝。
              </p>
              <div class="form-grid mt-3">
                <label
                  v-for="label in typeLabels"
                  :key="label"
                  class="field-label break-words"
                >
                  {{ label }}
                  <select v-model="mapping.type_map[label]" class="field">
                    <option :value="undefined">自动识别 / 未指定</option>
                    <option value="income">收入</option>
                    <option value="expense">支出</option>
                  </select>
                </label>
              </div>
            </div>
            <div class="form-grid mt-5">
              <label
                v-for="kind in ['income', 'expense'] as const"
                :key="kind"
                class="field-label"
              >
                {{ labels[kind] }}默认分类（来源分类为空时）
                <select
                  v-model="
                    mapping[
                      kind === 'income'
                        ? 'default_income_category_id'
                        : 'default_expense_category_id'
                    ]
                  "
                  class="field"
                >
                  <option :value="null">请选择</option>
                  <option
                    v-for="item in categories.filter(
                      (category) => category.type === kind,
                    )"
                    :key="item.id"
                    :value="item.id"
                  >
                    {{ item.name
                    }}{{
                      workspace.type === 'business' &&
                      item.business_kind === 'cost'
                        ? ' · 成本'
                        : ''
                    }}
                  </option>
                </select>
              </label>
            </div>
            <div v-if="categoryLabels.length" class="mt-5">
              <h3 class="font-bold">来源分类对应关系</h3>
              <p class="muted mt-2 text-xs">
                来源分类有值时必须映射，不会悄悄替换成默认分类。
              </p>
              <div class="form-grid mt-3">
                <label
                  v-for="label in categoryLabels"
                  :key="label"
                  class="field-label break-words"
                >
                  {{ label }}
                  <select v-model="mapping.category_map[label]" class="field">
                    <option :value="undefined">未映射（该行会报错）</option>
                    <option
                      v-for="item in categories"
                      :key="item.id"
                      :value="item.id"
                    >
                      {{ labels[item.type] }} · {{ item.name
                      }}{{
                        workspace.type === 'business' &&
                        item.business_kind === 'cost'
                          ? ' · 成本'
                          : ''
                      }}
                    </option>
                  </select>
                </label>
              </div>
            </div>
            <div class="mt-5 flex flex-wrap items-center gap-4">
              <button class="primary-button" :disabled="busy">
                校验并生成预览
              </button>
              <span v-if="dirty && batch.mapping" class="muted text-sm">
                映射已改变，请重新预览后确认。
              </span>
            </div>
          </fieldset>
        </form>
        <section class="panel rounded-2xl p-5 sm:p-7">
          <h2 class="font-bold">
            {{
              batch.status === 'committed'
                ? '导入结果报告'
                : '3. 逐行预览与核对'
            }}
          </h2>
          <div class="mt-4 flex flex-wrap gap-3">
            <span
              v-for="(count, status) in batch.counts"
              :key="status"
              class="pill"
            >
              {{ labels[status] }} {{ count }}
            </span>
          </div>
          <p v-if="batch.status === 'committed'" class="mt-4 text-sm">
            已完成；错误、重复和未选择行没有入账。导入交易可到账本编辑、退款或撤销，原来源去重标记会保留。
          </p>
          <p class="muted mt-3 text-xs leading-6">
            有单号时按账户、来源名称和来源单号去重；无单号时按账户、时间、类型、金额、商户和备注去重。相同内容可能是真实的多笔交易，请先核对并补充不同来源单号。确认时会再次检查重复记录。
          </p>
          <div class="mt-5 flex flex-wrap items-center gap-3">
            <label class="field-label">
              行状态
              <select v-model="rowFilter" class="field" @change="page = 0">
                <option value="all">全部</option>
                <option
                  v-for="status in [
                    'uploaded',
                    'ready',
                    'error',
                    'duplicate',
                    'imported',
                    'skipped',
                  ]"
                  :key="status"
                  :value="status"
                >
                  {{ labels[status] }}
                </option>
              </select>
            </label>
            <button
              v-if="editable && !dirty"
              class="accent"
              :disabled="busy"
              @click="selected = readyRows.map((row) => row.id)"
            >
              选择全部有效行
            </button>
            <button
              v-if="editable && !dirty"
              class="muted"
              :disabled="busy"
              @click="selected = []"
            >
              清空选择
            </button>
          </div>
          <article v-for="row in visibleRows" :key="row.id" class="import-row">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <label class="flex items-center gap-3">
                <input
                  v-if="editable"
                  v-model="selected"
                  type="checkbox"
                  :value="row.id"
                  :disabled="busy || dirty || row.status !== 'ready'"
                  :aria-label="`选择第 ${row.row_number} 行`"
                />
                <strong>第 {{ row.row_number }} 行</strong>
                <span
                  :class="
                    row.status === 'error'
                      ? 'error'
                      : row.status === 'ready' || row.status === 'imported'
                        ? 'accent'
                        : 'muted'
                  "
                >
                  {{ labels[row.status] }}
                </span>
              </label>
              <strong v-if="row.normalized">
                {{ labels[row.normalized.type] }}
                {{ money(row.normalized.amount) }} {{ workspace.base_currency }}
              </strong>
            </div>
            <div v-if="row.normalized" class="import-details">
              <span>{{ displayDateTime(row.normalized.occurred_at) }}</span>
              <span>
                账户：{{
                  accountName(batch.mapping?.account_id || mapping.account_id)
                }}
              </span>
              <span>分类：{{ row.normalized.category_name }}</span>
              <span>对方：{{ row.normalized.merchant_name || '—' }}</span>
              <span>备注：{{ row.normalized.description || '—' }}</span>
              <span>来源单号：{{ row.normalized.external_id || '—' }}</span>
            </div>
            <p
              v-for="message in row.messages"
              :key="message"
              class="mt-3 text-sm"
              :class="row.status === 'error' ? 'error' : 'muted'"
            >
              {{ message }}
            </p>
            <details v-if="row.raw" class="muted mt-3 text-xs">
              <summary class="cursor-pointer">查看原始行</summary>
              <dl class="import-details">
                <div v-for="(value, index) in row.raw" :key="index">
                  <dt>{{ batch.headers[index] || `额外列 ${index + 1}` }}</dt>
                  <dd class="mt-1 whitespace-pre-wrap">
                    {{ value || '（空）' }}
                  </dd>
                </div>
              </dl>
            </details>
            <p v-else class="muted mt-3 text-xs">
              原始行已按保留期限或取消操作清理。
            </p>
          </article>
          <div class="mt-5 flex flex-wrap items-center justify-between gap-3">
            <button class="tab" :disabled="page === 0" @click="page--">
              上一页
            </button>
            <span class="muted text-sm">
              {{ filteredRows.length }} 行 · 第 {{ page + 1 }} /
              {{ Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE)) }} 页
            </span>
            <button
              class="tab"
              :disabled="(page + 1) * PAGE_SIZE >= filteredRows.length"
              @click="page++"
            >
              下一页
            </button>
          </div>
          <div v-if="editable" class="mt-6 flex flex-wrap gap-4">
            <button
              class="primary-button"
              :disabled="busy || dirty || !selected.length"
              @click="confirmation = 'confirm'"
            >
              确认导入 {{ selected.length }} 行
            </button>
            <button
              class="muted"
              :disabled="busy"
              @click="confirmation = 'cancel'"
            >
              取消批次并清理暂存行
            </button>
          </div>
        </section>
      </template>
    </template>
    <div v-if="confirmation" class="modal-backdrop">
      <section
        class="panel w-full max-w-lg rounded-2xl p-6"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-confirm-title"
      >
        <h2 id="import-confirm-title" class="text-lg font-bold">
          {{ confirmation === 'confirm' ? '确认批量入账' : '取消本批次' }}
        </h2>
        <p class="muted mt-4 leading-7">
          {{
            confirmation === 'confirm'
              ? `将把已勾选的 ${selected.length} 行写入“${accountName(mapping.account_id)}”。请确认类型、金额、日期和分类已核对。确认时发现的新重复记录会自动跳过。`
              : '本批次尚未入账，取消后清理暂存行，需要重新上传才能处理。'
          }}
        </p>
        <div class="mt-6 flex flex-wrap gap-4">
          <button
            class="primary-button"
            :disabled="busy"
            @click="confirmation === 'confirm' ? commit() : cancel()"
          >
            {{ confirmation === 'confirm' ? '已核对，确认入账' : '确认取消' }}
          </button>
          <button class="muted" :disabled="busy" @click="confirmation = null">
            返回检查
          </button>
        </div>
      </section>
    </div>
  </main>
</template>
