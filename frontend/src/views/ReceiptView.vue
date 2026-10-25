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
  clearReceipt,
  confirmReceipt,
  getReceipt,
  getReceiptCapabilities,
  listReceipts,
  receiptImageURL,
  receiptSampleURL,
  recognizeReceipt,
  reviewReceipt,
  structureReceipt,
  uploadReceipt,
  type Receipt,
  type ReceiptCapabilities,
  type ReceiptSummary,
} from '../api/receipts'
import { getWorkspace, type Workspace } from '../api/workspaces'
import { useAuthStore } from '../stores/auth'
import { displayDateTime, money, toUTC } from '../utils/format'
import { receiptDraft } from '../utils/receipts'

interface Pending {
  receiptId: string
  version: number
  key: string
}
const workspaceId = String(useRoute().params.workspaceId)
const base = `/workspaces/${workspaceId}`
const auth = useAuthStore()
const router = useRouter()
const workspace = ref<Workspace | null>(null)
const accounts = ref<Account[]>([])
const categories = ref<Category[]>([])
const capabilities = ref<ReceiptCapabilities | null>(null)
const history = ref<ReceiptSummary[]>([])
const receipt = ref<Receipt | null>(null)
const draft = ref(
  receiptDraft({ candidates: null, reviewed_payload: null } as Receipt),
)
const file = ref<File | null>(null)
const text = ref('')
const reviewed = ref(false)
const cloudConsent = ref(false)
const busy = ref(false)
const initializing = ref(true)
const error = ref('')
const notice = ref('')
const imageError = ref(false)
const pending = ref<Pending | null>(null)
const dialog = ref<'confirm' | 'cloud' | 'clear' | null>(null)
let disposed = false
const readonly = computed(() => workspace.value?.role === 'viewer')
const editable = computed(
  () => receipt.value?.status === 'draft' && !readonly.value && !pending.value,
)
const activeAccounts = computed(() =>
  accounts.value.filter((item) => item.is_active),
)
const availableCategories = computed(() =>
  categories.value.filter((item) => item.type === draft.value.type),
)
const currencyMismatch = computed(
  () =>
    !!receipt.value?.candidates?.currency &&
    receipt.value.candidates.currency !== workspace.value?.base_currency,
)
const textDirty = computed(() => text.value !== (receipt.value?.ocr_text || ''))
const storageKey = () =>
  `finscope.pending.receipt.${auth.user?.id}.${workspaceId}`
const labels: Record<string, string> = {
  draft: '待核对',
  confirmed: '已确认入账',
  cancelled: '已取消',
  expired: '已过期',
  pending: '未识别',
  ready: '已提取文字',
  failed: '识别失败，可手工填写',
  amount: '金额',
  date: '日期',
  time: '时间',
  merchant_name: '商户',
  category_hint: '分类建议',
  transaction_type: '收支方向',
  currency: '币种',
  low: '低，仅供参考',
  medium: '中，仅供参考',
  unknown: '未知',
  income: '收入',
  expense: '支出',
}
const candidateRows = computed(() => {
  const value = receipt.value?.candidates
  return value
    ? (
        [
          'amount',
          'date',
          'time',
          'merchant_name',
          'category_hint',
          'transaction_type',
          'currency',
        ] as const
      ).map((key) => ({
        key,
        value:
          key === 'amount' && value[key]
            ? money(value[key])
            : labels[value[key] || ''] || value[key] || '未识别，请手工核对',
        confidence: labels[value.confidence[key] || 'unknown'],
      }))
    : []
})

function showError(cause: unknown) {
  if (disposed) return
  error.value = cause instanceof Error ? cause.message : '操作失败，请重试'
  if (cause instanceof ApiError && cause.status === 401) {
    auth.user = null
    auth.csrfToken = ''
    void router.replace('/')
  }
}
function openCloud() {
  cloudConsent.value = false
  dialog.value = 'cloud'
}
function changeType() {
  draft.value.category_id = ''
  reviewed.value = false
}
function accept(value: Receipt) {
  if (disposed) return
  receipt.value = value
  draft.value = receiptDraft(value)
  text.value = value.ocr_text || ''
  reviewed.value = false
  cloudConsent.value = false
  imageError.value = false
}
async function refreshHistory() {
  const result = await listReceipts(workspaceId)
  if (!disposed) history.value = result
}
async function open(id: string) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    accept(await getReceipt(workspaceId, id))
  } catch (cause) {
    showError(cause)
  } finally {
    busy.value = false
  }
}
async function upload() {
  if (!file.value || busy.value || pending.value || readonly.value) return
  if (
    !/\.(png|jpe?g|webp)$/i.test(file.value.name) ||
    !file.value.size ||
    file.value.size > 5 * 1024 * 1024
  ) {
    error.value = '请选择不超过 5 MiB 的静态 JPEG、PNG 或 WebP 图片'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    accept(await uploadReceipt(workspaceId, file.value, auth.csrfToken))
    await refreshHistory()
    notice.value =
      receipt.value?.status === 'confirmed'
        ? '同一图片已确认过，已打开原记录，没有重复入账。'
        : '图片已保存，尚未入账。可开始本地识别，也可直接手工填写。'
  } catch (cause) {
    showError(cause)
  } finally {
    busy.value = false
  }
}
async function recognize() {
  if (!receipt.value || !editable.value || busy.value) return
  busy.value = true
  error.value = ''
  notice.value = '正在本机识别，请等待…'
  try {
    accept(
      await recognizeReceipt(
        workspaceId,
        receipt.value.id,
        receipt.value.version,
        auth.csrfToken,
      ),
    )
    await refreshHistory()
    notice.value = '识别结果仅为候选，仍未入账。请对照图片填写下方表单。'
  } catch (cause) {
    showError(cause)
  } finally {
    busy.value = false
  }
}
async function structure(useAI: boolean) {
  if (!receipt.value || !editable.value || busy.value) return
  if (useAI && (!cloudConsent.value || textDirty.value)) return
  busy.value = true
  error.value = ''
  dialog.value = null
  try {
    accept(
      await structureReceipt(
        workspaceId,
        receipt.value.id,
        {
          version: receipt.value.version,
          ...(useAI
            ? {
                use_ai: true,
                consent: true,
                consent_target: capabilities.value?.ai_target,
              }
            : { text: text.value }),
        },
        auth.csrfToken,
      ),
    )
    notice.value = '已更新候选字段，请重新核对；尚未入账。'
    await refreshHistory()
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
    if (value) notice.value = '浏览器无法暂存重试状态，请在当前页面等待结果。'
  }
}
async function retry() {
  if (!pending.value || busy.value || readonly.value) return
  const value = pending.value
  busy.value = true
  error.value = ''
  dialog.value = null
  try {
    const result = await confirmReceipt(
      workspaceId,
      value.receiptId,
      value.version,
      value.key,
      auth.csrfToken,
    )
    remember(null)
    if (receipt.value?.id === result.id)
      receipt.value = { ...receipt.value, ...result }
    notice.value = '票据已确认入账，可以返回账本核对金额和余额。'
    accept(await getReceipt(workspaceId, value.receiptId))
    await refreshHistory()
    notice.value = '票据已确认入账，可以返回账本核对金额和余额。'
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
async function saveAndConfirm() {
  if (
    !receipt.value ||
    !workspace.value ||
    !reviewed.value ||
    !editable.value ||
    busy.value
  )
    return
  busy.value = true
  error.value = ''
  dialog.value = null
  try {
    if (!draft.value.type) throw new Error('请选择实际收支方向')
    const value = await reviewReceipt(
      workspaceId,
      receipt.value.id,
      {
        version: receipt.value.version,
        reviewed: true,
        currency: workspace.value.base_currency,
        currency_hint_corrected: draft.value.currency_hint_corrected,
        transaction: {
          account_id: draft.value.account_id,
          category_id: draft.value.category_id,
          type: draft.value.type,
          amount: draft.value.amount,
          occurred_at: toUTC(draft.value.localTime),
          merchant_name: draft.value.merchant_name,
          description: draft.value.description,
          related_transaction_id: null,
          adjustment_direction: null,
        },
      },
      auth.csrfToken,
    )
    accept(value)
    remember({
      receiptId: value.id,
      version: value.version,
      key: requestKey(),
    })
  } catch (cause) {
    showError(cause)
    return
  } finally {
    busy.value = false
  }
  await retry()
}
async function clear() {
  if (!receipt.value || readonly.value || pending.value || busy.value) return
  busy.value = true
  error.value = ''
  dialog.value = null
  try {
    accept(
      await clearReceipt(
        workspaceId,
        receipt.value.id,
        receipt.value.version,
        auth.csrfToken,
      ),
    )
    await refreshHistory()
    notice.value = '票据图片与识别文字已清理；已入账交易保持原状。'
  } catch (cause) {
    showError(cause)
  } finally {
    busy.value = false
  }
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
      getReceiptCapabilities(workspaceId),
      listReceipts(workspaceId),
    ])
    if (disposed) return
    workspace.value = result[0]
    accounts.value = result[1]
    categories.value = result[2]
    capabilities.value = result[3]
    history.value = result[4]
    try {
      const saved = sessionStorage.getItem(storageKey())
      if (saved) {
        const value = JSON.parse(saved) as Pending
        if (
          /^[a-f0-9-]{36}$/.test(value.receiptId) &&
          /^[a-f0-9-]{36}$/.test(value.key) &&
          Number.isInteger(value.version)
        ) {
          pending.value = value
          accept(await getReceipt(workspaceId, value.receiptId))
        } else sessionStorage.removeItem(storageKey())
      }
    } catch (cause) {
      if (cause instanceof ApiError) showError(cause)
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
        <p class="eyebrow">FINSCOPE / RECEIPT</p>
        <h1 class="mt-2 text-2xl font-bold">票据识别与核对</h1>
        <p class="muted mt-2 text-sm">
          {{ workspace?.name }} · 上传图片 → 提取文字 → 人工核对 → 确认入账
        </p>
      </div>
    </header>
    <p v-if="initializing" role="status" class="status-panel rounded-xl p-5">
      正在加载票据工作台…
    </p>
    <p v-if="error" role="alert" class="message-error mb-5">{{ error }}</p>
    <p v-if="notice" role="status" class="message-success mb-5">{{ notice }}</p>
    <section v-if="pending" class="pending-panel mb-5 rounded-xl p-5">
      <h2 class="font-bold">有一笔确认结果待核实</h2>
      <p class="mt-2 text-sm">
        保留原请求编号，安全重试不会重复入账。浏览器仅暂存编号，不保存图片和金额。
      </p>
      <button
        class="primary-button mt-4"
        :disabled="busy || readonly"
        @click="retry"
      >
        安全重试原确认
      </button>
    </section>
    <template v-if="workspace">
      <section class="panel mb-5 rounded-2xl p-5 sm:p-7">
        <div class="flex flex-wrap justify-between gap-4">
          <h2 class="font-bold">上传票据图片</h2>
          <a
            :href="receiptSampleURL(workspaceId)"
            download="FinScope-demo-receipt.jpg"
            class="accent text-sm"
          >
            下载虚构测试票据
          </a>
        </div>
        <p class="muted mt-3 text-sm leading-7">
          支持静态 JPEG、PNG、WebP，最大 5 MiB、1600
          万像素。图片去除元数据后保存在服务端私有数据库，72
          小时后访问时清理。上传和识别都不会自动记账。
        </p>
        <form
          v-if="!readonly"
          class="mt-4 flex flex-wrap gap-4"
          @submit.prevent="upload"
        >
          <input
            type="file"
            accept=".jpg,.jpeg,.png,.webp"
            aria-label="选择票据图片"
            class="field min-w-0 flex-1"
            :disabled="busy || !!pending"
            @change="
              file = ($event.target as HTMLInputElement).files?.[0] || null
            "
          />
          <button class="primary-button" :disabled="!file || busy || !!pending">
            上传图片
          </button>
        </form>
        <p v-else class="muted mt-4">
          当前为只读成员，可查看票据，不能识别、修改或入账。
        </p>
        <p v-if="!capabilities?.ai_enabled" class="muted mt-3 text-xs">
          云端 AI 未配置，当前可使用本地 OCR、规则整理和手工填写。
        </p>
      </section>
      <section class="panel mb-5 rounded-2xl p-5 sm:p-7">
        <h2 class="font-bold">最近票据</h2>
        <p v-if="!history.length" class="muted mt-4">还没有上传票据。</p>
        <div v-for="item in history" :key="item.id" class="ledger-row">
          <div class="break-words">
            <p>{{ item.filename }}</p>
            <p class="muted mt-1 text-xs">
              {{ labels[item.status] }} · {{ labels[item.ocr_status] }} ·
              {{ displayDateTime(item.created_at) }}
            </p>
          </div>
          <button
            class="accent shrink-0"
            :disabled="busy"
            @click="open(item.id)"
          >
            打开
          </button>
        </div>
      </section>
      <template v-if="receipt">
        <section class="status-panel mb-5 rounded-xl p-5">
          <h2 class="break-words font-bold">
            {{ receipt.filename }} · {{ labels[receipt.status] }}
          </h2>
          <p class="muted mt-2 text-xs">
            图片与识别文字保留至
            {{ displayDateTime(receipt.expires_at) }}，到期访问时清理。{{
              receipt.transaction_id
                ? '已确认交易的后续编辑和撤销以账本为准。'
                : '尚未入账。'
            }}
          </p>
          <p
            v-for="item in receipt.notices"
            :key="item"
            class="muted mt-2 text-sm"
          >
            {{ item }}
          </p>
          <div class="mt-4 flex flex-wrap gap-4">
            <button
              v-if="editable"
              class="primary-button"
              :disabled="busy"
              @click="recognize"
            >
              {{ busy ? '处理中…' : '本地识别 / 重新识别' }}
            </button>
            <button
              v-if="!readonly && !pending && receipt.image_available"
              class="muted"
              :disabled="busy"
              @click="dialog = 'clear'"
            >
              清理图片与文字
            </button>
            <RouterLink
              v-if="receipt.transaction_id"
              :to="`${base}/finance`"
              class="accent"
            >
              查看账本 →
            </RouterLink>
          </div>
        </section>
        <div class="receipt-grid">
          <section class="panel rounded-2xl p-5 sm:p-7">
            <h2 class="font-bold">对照原图</h2>
            <img
              v-if="receipt.image_available && !imageError"
              :src="receiptImageURL(workspaceId, receipt.id)"
              alt="当前空间已授权的票据预览"
              class="receipt-preview mt-4"
              @error="imageError = true"
            />
            <p v-else class="muted mt-4">图片已清理、过期或暂时无法加载。</p>
            <details class="mt-5" :open="receipt.ocr_status === 'failed'">
              <summary class="cursor-pointer font-bold">识别文字与修订</summary>
              <textarea
                v-model="text"
                class="field mt-3"
                rows="9"
                maxlength="12000"
                :disabled="!editable || busy"
                aria-label="识别文字或修订文字"
              />
              <div v-if="editable" class="mt-4 flex flex-wrap gap-3">
                <button
                  class="primary-button"
                  :disabled="busy || !text.trim()"
                  @click="structure(false)"
                >
                  保存文字并本地整理
                </button>
                <button
                  v-if="capabilities?.ai_enabled"
                  class="accent"
                  :disabled="busy || textDirty || !receipt.ocr_text"
                  @click="openCloud"
                >
                  核对发送内容并使用云端 AI
                </button>
              </div>
              <p v-if="textDirty" class="muted mt-3 text-xs">
                文字有修订，请先本地整理保存，再检查 AI 发送预览。
              </p>
            </details>
          </section>
          <section class="panel rounded-2xl p-5 sm:p-7">
            <h2 class="font-bold">候选字段（均需人工核对）</h2>
            <p class="muted mt-2 text-xs">
              整理方式：{{
                receipt.extraction_provider === 'compatible_ai'
                  ? '云端 AI'
                  : '本地规则'
              }}。未知字段不会自动猜测，置信等级不代表准确率。
            </p>
            <div
              v-for="row in candidateRows"
              :key="row.key"
              class="ledger-row text-sm"
            >
              <div>
                <p class="muted">{{ labels[row.key] }}</p>
                <p class="mt-1 break-words">{{ row.value }}</p>
              </div>
              <span class="muted shrink-0 text-xs">{{ row.confidence }}</span>
            </div>
            <p v-if="!candidateRows.length" class="muted mt-5">
              尚无候选，可识别图片或直接手工填写交易。
            </p>
            <p
              v-for="warning in receipt.candidates?.warnings || []"
              :key="warning"
              class="muted mt-4 text-xs leading-6"
            >
              {{ warning }}
            </p>
          </section>
        </div>
        <form
          v-if="editable"
          class="panel mt-5 rounded-2xl p-5 sm:p-7"
          @submit.prevent="dialog = 'confirm'"
        >
          <fieldset :disabled="busy">
            <legend class="mb-5 font-bold">人工审核交易</legend>
            <p class="muted mb-5 text-sm">
              识别失败也可手工填写。请确认图片代表实际收入或支出；转账、退款、借还款和应收应付结算请使用原功能。
            </p>
            <div class="form-grid">
              <label class="field-label">
                收支方向 *
                <select
                  v-model="draft.type"
                  class="field"
                  required
                  @change="changeType"
                >
                  <option value="">请核对实际方向</option>
                  <option value="income">收入</option>
                  <option value="expense">支出</option>
                </select>
              </label>
              <label class="field-label">
                入账账户 *
                <select v-model="draft.account_id" class="field" required>
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
                分类 *
                <select v-model="draft.category_id" class="field" required>
                  <option value="">请选择分类</option>
                  <option
                    v-for="item in availableCategories"
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
              <label class="field-label">
                金额（{{ workspace.base_currency }}）*
                <input
                  v-model="draft.amount"
                  class="field"
                  required
                  inputmode="decimal"
                  pattern="[0-9]{1,16}(\.[0-9]{1,2})?"
                />
              </label>
              <label class="field-label">
                实际发生时间（香港时间）*
                <input
                  v-model="draft.localTime"
                  type="datetime-local"
                  step="1"
                  min="1970-01-01T00:00"
                  max="2100-12-31T23:59:59"
                  class="field"
                  required
                />
              </label>
              <label class="field-label">
                商户 / 对方
                <input
                  v-model="draft.merchant_name"
                  class="field"
                  maxlength="120"
                />
              </label>
              <label class="field-label">
                备注
                <input
                  v-model="draft.description"
                  class="field"
                  maxlength="500"
                />
              </label>
            </div>
            <p
              v-if="receipt.candidates?.date && !receipt.candidates.time"
              class="muted mt-4 text-xs"
            >
              未识别到时间，草稿暂填 00:00，请根据实际情况修正。
            </p>
            <p v-if="!activeAccounts.length || !categories.length" class="mt-4">
              <RouterLink :to="`${base}/finance?tab=accounts`" class="accent">
                先创建账户和分类 →
              </RouterLink>
            </p>
            <div
              v-if="currencyMismatch"
              class="pending-panel mt-5 rounded-xl p-4 text-sm"
            >
              <p>
                候选币种 {{ receipt.candidates?.currency }} 与空间
                {{ workspace.base_currency }}
                不符。图片确为外币时请使用对应币种空间，不做汇率转换。
              </p>
              <label class="mt-3 flex gap-3">
                <input
                  v-model="draft.currency_hint_corrected"
                  type="checkbox"
                />
                <span>
                  我已查看图片，确认这是币种误识别，实际应使用
                  {{ workspace.base_currency }}。
                </span>
              </label>
            </div>
            <label class="mt-5 flex items-start gap-3 text-sm leading-7">
              <input v-model="reviewed" type="checkbox" required class="mt-2" />
              <span>
                我已对照图片核对金额、币种、日期、方向、账户和分类，并确认未在账本重复登记。
              </span>
            </label>
            <button
              class="primary-button mt-5"
              :disabled="
                busy ||
                !reviewed ||
                (currencyMismatch && !draft.currency_hint_corrected)
              "
            >
              核对完成，准备入账
            </button>
          </fieldset>
        </form>
      </template>
    </template>
    <div v-if="dialog" class="modal-backdrop">
      <section
        class="panel receipt-dialog w-full max-w-xl rounded-2xl p-6"
        role="dialog"
        aria-modal="true"
        aria-labelledby="receipt-dialog-title"
      >
        <h2 id="receipt-dialog-title" class="text-lg font-bold">
          {{
            dialog === 'cloud'
              ? '确认发送给云端 AI 的内容'
              : dialog === 'confirm'
                ? '确认这笔交易'
                : '清理票据图片和文字'
          }}
        </h2>
        <template v-if="dialog === 'cloud'">
          <p class="mt-4 text-sm">接收方：{{ capabilities?.ai_target }}</p>
          <p class="muted mt-3 text-sm leading-7">
            {{ capabilities?.disclosure }} 外部服务可能按其政策保留数据。
          </p>
          <pre
            class="status-panel mt-4 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-xl p-4 text-xs"
            >{{ receipt?.external_text_preview }}</pre>
          <label class="mt-4 flex items-start gap-3 text-sm leading-7">
            <input v-model="cloudConsent" type="checkbox" class="mt-2" />
            <span>
              我已核对上述文字，同意发送给显示的服务用于提取候选字段。
            </span>
          </label>
        </template>
        <p
          v-else-if="dialog === 'confirm'"
          class="mt-4 break-words text-sm leading-7"
        >
          {{ labels[draft.type] }} {{ money(draft.amount) }}
          {{ workspace?.base_currency }}
          <br />
          {{ draft.localTime.replace('T', ' ') }}（香港时间）
          <br />
          账户：{{
            accounts.find((item) => item.id === draft.account_id)?.name
          }}
          <br />
          分类：{{
            categories.find((item) => item.id === draft.category_id)?.name
          }}
          <br />
          确认后创建一笔交易，并保留票据关联。
        </p>
        <p v-else class="muted mt-4 text-sm leading-7">
          图片、识别文字及候选草稿将被清理。未入账票据会取消；已经入账的交易保留，需要撤销请到账本处理。
        </p>
        <div class="mt-6 flex flex-wrap gap-4">
          <button
            class="primary-button"
            :disabled="busy || (dialog === 'cloud' && !cloudConsent)"
            @click="
              dialog === 'cloud'
                ? structure(true)
                : dialog === 'confirm'
                  ? saveAndConfirm()
                  : clear()
            "
          >
            {{
              dialog === 'cloud'
                ? '同意并整理'
                : dialog === 'confirm'
                  ? '确认入账'
                  : '确认清理'
            }}
          </button>
          <button class="muted" :disabled="busy" @click="dialog = null">
            返回检查
          </button>
        </div>
      </section>
    </div>
  </main>
</template>
