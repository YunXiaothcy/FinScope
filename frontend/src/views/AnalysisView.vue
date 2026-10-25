<script setup lang="ts">
import { requestKey } from '../utils/requestKey'
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import {
  askAnalysis,
  clearAnalysis,
  getAnalysis,
  getAnalysisCapabilities,
  listAnalyses,
  type Analysis,
  type AnalysisCapabilities,
  type Question,
} from '../api/analysis'
import { getWorkspace, type Workspace } from '../api/workspaces'
import { useAuthStore } from '../stores/auth'
import { displayDateTime, money } from '../utils/format'
import AppIcon from '../components/AppIcon.vue'

const workspaceId = String(useRoute().params.workspaceId)
const router = useRouter()
const auth = useAuthStore()
const workspace = ref<Workspace | null>(null)
const capabilities = ref<AnalysisCapabilities | null>(null)
const history = ref<Analysis[]>([])
const record = ref<Analysis | null>(null)
const question = ref('我这个月在哪些类别花钱最多？')
const custom = ref(false)
const start = ref('')
const end = ref('')
const busy = ref(false)
const loading = ref(true)
const error = ref('')
const notice = ref('')
const dialog = ref<'clear' | null>(null)
const pending = ref<{ payload: Question; key: string } | null>(null)
let disposed = false
let controller: AbortController | null = null
const labels: Record<string, string> = {
  local: '本地规则分析',
  running: '云端处理中',
  complete: '云端已选择重点',
  fallback: '已降级为本地分析',
  expired: '已清理',
}
const examples = [
  { title: '支出去向', question: '我这个月在哪些类别花钱最多？' },
  { title: '收支趋势', question: '最近三个月我的支出有什么变化？' },
  { title: '月度回顾', question: '分析上月收入、支出和现金流' },
  { title: '费用变化', question: '我的商户最近哪个费用类别增长最快？' },
  { title: '利润分析', question: '为什么这个月营业收入增长但利润下降？' },
  { title: '预算支持范围', question: '本月预算是否可能超支？' },
]

function chooseExample(value: string) {
  question.value = value
  custom.value = false
}

function showError(cause: unknown) {
  if (disposed) return
  error.value = cause instanceof Error ? cause.message : '操作失败，请重试'
  if (cause instanceof ApiError && cause.status === 401) {
    auth.user = null
    auth.csrfToken = ''
    void router.replace('/')
  }
}
function update(value: Analysis) {
  if (disposed) return
  record.value = value
  history.value = [
    value,
    ...history.value.filter((item) => item.id !== value.id),
  ]
    .filter((item) => item.status !== 'expired')
    .slice(0, 20)
}
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  controller = new AbortController()
  try {
    await action()
  } catch (cause) {
    showError(cause)
  } finally {
    if (!disposed) busy.value = false
  }
}
function signal() {
  return AbortSignal.any([controller!.signal, AbortSignal.timeout(60000)])
}
async function ask() {
  if (pending.value) return
  const payload: Question = { question: question.value.trim() }
  if (custom.value) {
    if (!start.value || !end.value || start.value > end.value) {
      error.value = '请填写有序的起止日期'
      return
    }
    payload.start_date = start.value
    payload.end_date = end.value
  }
  pending.value = { payload, key: requestKey() }
  await retryAsk()
}
async function retryAsk() {
  await run(async () => {
    if (!pending.value) return
    try {
      const value = await askAnalysis(
        workspaceId,
        pending.value.payload,
        pending.value.key,
        auth.csrfToken,
        signal(),
      )
      if (disposed) return
      pending.value = null
      update(value)
    } catch (cause) {
      if (cause instanceof ApiError && cause.status > 0 && cause.status < 500)
        pending.value = null
      throw cause
    }
  })
}
async function loadRecord(id: string) {
  await run(async () => update(await getAnalysis(workspaceId, id)))
}
async function refresh() {
  await run(async () => {
    const values = await Promise.all([
      listAnalyses(workspaceId),
      getAnalysisCapabilities(workspaceId),
    ])
    if (disposed) return
    history.value = values[0]
    capabilities.value = values[1]
    if (record.value) {
      const active = values[0].find((item) => item.id === record.value?.id)
      if (active) update(active)
      else record.value = null
    }
  })
}
async function clear() {
  if (!record.value) return
  const id = record.value.id
  dialog.value = null
  await run(async () => {
    await clearAnalysis(workspaceId, id, auth.csrfToken)
    if (disposed) return
    history.value = history.value.filter((item) => item.id !== id)
    record.value = null
    notice.value = '分析快照与结果已清理，账务保持原样。'
  })
}
async function initialize() {
  loading.value = true
  error.value = ''
  try {
    await auth.initialize()
    if (!auth.user) {
      await router.replace('/')
      return
    }
    const values = await Promise.all([
      getWorkspace(workspaceId),
      getAnalysisCapabilities(workspaceId),
      listAnalyses(workspaceId),
    ])
    if (disposed) return
    workspace.value = values[0]
    capabilities.value = values[1]
    history.value = values[2]
  } catch (cause) {
    showError(cause)
  } finally {
    if (!disposed) loading.value = false
  }
}
onMounted(initialize)
onUnmounted(() => {
  disposed = true
  controller?.abort()
})
</script>

<template>
  <main class="analysis-page mx-auto max-w-7xl px-4 py-8 sm:px-8">
    <header class="mb-6 flex flex-wrap items-center justify-between gap-4">
      <div>
        <p class="eyebrow">FINSCOPE / INSIGHTS</p>
        <h1 class="mt-2 text-2xl font-bold">AI 财务分析师</h1>
        <p class="muted mt-2">{{ workspace?.name }} · 只读分析 · 香港时间</p>
      </div>
    </header>
    <p v-if="loading" role="status" class="status-panel mb-5 rounded-xl p-5">
      正在加载分析工作台…
    </p>
    <p v-if="error" role="alert" class="message-error mb-5">{{ error }}</p>
    <button
      v-if="!workspace && !loading"
      class="primary-button mb-5"
      @click="initialize"
    >
      重新加载分析工作台
    </button>
    <p v-if="notice" role="status" class="message-success mb-5">{{ notice }}</p>
    <template v-if="workspace">
      <section class="panel analysis-composer mb-5 rounded-2xl p-5 sm:p-7">
        <div class="analysis-intro">
          <div class="analysis-heading">
            <span class="analysis-symbol">
              <AppIcon name="analysis" :size="25" />
            </span>
            <div>
              <p class="eyebrow">从一个问题开始</p>
              <h2 class="mt-2 text-xl font-semibold">让每一笔，都更清晰。</h2>
            </div>
          </div>
          <span class="analysis-mode">
            <span class="connection-dot" aria-hidden="true" />
            本地分析
          </span>
        </div>
        <p class="muted mt-3 text-sm leading-7">
          支持本月、上月、最近三个月，或自定义起止日期（最多 366
          天）。当前按整个空间汇总，暂不支持指定账户、商户、单个类别与自由对话。
        </p>
        <div class="question-grid my-5">
          <button
            v-for="example in examples"
            :key="example.question"
            type="button"
            class="question-card"
            :aria-pressed="question === example.question"
            :disabled="busy || !!pending"
            @click="chooseExample(example.question)"
          >
            <span class="question-card-heading">
              <span>{{ example.title }}</span>
              <AppIcon
                :name="question === example.question ? 'check' : 'arrow'"
                :size="16"
              />
            </span>
            <span class="question-card-description">
              {{ example.question }}
            </span>
          </button>
        </div>
        <form @submit.prevent="ask">
          <label class="field-label">
            你的财务问题
            <textarea
              v-model="question"
              class="field mt-2 min-h-24 w-full"
              maxlength="500"
              required
              :disabled="busy || !!pending"
            />
          </label>
          <label class="my-4 flex items-center gap-2">
            <input
              v-model="custom"
              type="checkbox"
              :disabled="busy || !!pending"
            />
            使用自定义日期（优先于问题中的日期）
          </label>
          <div v-if="custom" class="form-grid mb-4">
            <label class="field-label">
              开始日期
              <input
                v-model="start"
                type="date"
                class="field"
                :disabled="busy || !!pending"
                required
              />
            </label>
            <label class="field-label">
              结束日期
              <input
                v-model="end"
                type="date"
                class="field"
                :disabled="busy || !!pending"
                required
              />
            </label>
          </div>
          <button
            class="primary-button analysis-submit"
            :aria-busy="busy"
            :disabled="busy || !!pending || question.trim().length < 2"
          >
            <span v-if="busy" class="button-spinner" aria-hidden="true" />
            <AppIcon v-else name="analysis" :size="18" />
            {{ busy ? '正在分析…' : '生成本地分析' }}
          </button>
        </form>
        <p class="analysis-footnote muted mt-4 text-sm leading-7">
          基于当前空间的账务生成，只读、不修改交易。外部 AI 服务尚未接入。
        </p>
        <p class="muted mt-3 text-xs leading-6">
          问题原文不保存。分析快照仅本人在仍有成员权限时可查看，保留 30
          天，可主动清理；分析不会修改交易，只读成员也能使用。
        </p>
        <div v-if="pending" class="pending-panel mt-4 rounded-xl p-4">
          <p>上次提问结果待确认，原问题与请求编号仅保留在当前页面内存。</p>
          <button
            type="button"
            class="primary-button mt-3"
            :disabled="busy"
            @click="retryAsk"
          >
            使用原编号安全重试
          </button>
        </div>
      </section>
      <section
        v-if="record"
        class="panel analysis-result mb-5 rounded-2xl p-5 sm:p-7"
        aria-live="polite"
      >
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 class="text-xl font-bold">{{ record.title }}</h2>
            <p class="muted mt-2 text-sm">
              {{ labels[record.status] }} ·
              {{ displayDateTime(record.created_at) }} · {{ record.id }}
            </p>
          </div>
          <div class="flex flex-wrap gap-3">
            <button
              type="button"
              class="secondary-button"
              :disabled="busy"
              @click="loadRecord(record.id)"
            >
              刷新此记录
            </button>

            <button
              v-if="record.status !== 'expired'"
              type="button"
              class="secondary-button"
              :disabled="busy || record.status === 'running'"
              @click="dialog = 'clear'"
            >
              清理记录
            </button>
          </div>
        </div>
        <ul class="muted mt-5 list-disc space-y-2 pl-5 text-sm leading-7">
          <li v-for="line in record.notices" :key="line">{{ line }}</li>
        </ul>
        <h3 v-if="record.observations.length" class="mt-6 font-bold">
          分析重点 · 金额引用后端证据
        </h3>
        <ul class="mt-3 space-y-3 leading-7">
          <li v-for="line in record.observations" :key="line">{{ line }}</li>
        </ul>
        <h3 v-if="record.advice.length" class="mt-6 font-bold">
          下一步可做什么
        </h3>
        <ul class="mt-3 list-disc space-y-3 pl-5 leading-7">
          <li v-for="line in record.advice" :key="line">{{ line }}</li>
        </ul>
        <p v-if="record.status === 'complete'" class="muted mt-4 text-xs">
          云端选择证据和建议，文字由服务端模板生成；模型自由文字与自算金额不直接显示。
        </p>
        <details v-if="record.facts.length" class="mt-6" open>
          <summary class="cursor-pointer font-bold">
            全部指标与计算依据（{{ record.facts.length }}）
          </summary>
          <div class="mt-4 grid gap-3 md:grid-cols-2">
            <article
              v-for="fact in record.facts"
              :key="fact.id"
              class="status-panel fact-card min-w-0 rounded-xl p-4"
            >
              <p class="muted text-xs">
                {{ fact.id }} · {{ fact.start_date }} 至 {{ fact.end_date }}
              </p>
              <h4 class="mt-2 break-words font-bold">{{ fact.label }}</h4>
              <p class="accent mt-2 break-all text-xl tabular-nums">
                {{ fact.unit === '%' ? fact.value : money(fact.value) }}
                {{ fact.unit }}
              </p>
              <p class="muted mt-2 text-xs leading-6">{{ fact.formula }}</p>
            </article>
          </div>
        </details>
        <details v-if="record.tools.length" class="mt-5">
          <summary class="cursor-pointer text-sm">
            查看后端工具调用与口径
          </summary>
          <pre class="analysis-json mt-3">{{
            JSON.stringify(record.tools, null, 2)
          }}</pre>
        </details>
      </section>
      <section class="panel rounded-2xl p-5 sm:p-7">
        <div class="flex justify-between gap-3">
          <h2 class="section-heading font-bold">
            <AppIcon name="clock" :size="18" />
            我的最近分析
          </h2>
          <button
            type="button"
            class="accent"
            :disabled="busy"
            @click="refresh"
          >
            刷新分析列表
          </button>
        </div>
        <div v-if="!history.length" class="analysis-empty">
          <AppIcon name="analysis" :size="28" />
          <p class="mt-3 font-medium">你的下一份洞察，从这里开始</p>
          <p class="muted mt-2 text-sm">
            选择一个示例，或写下你想了解的财务问题。
          </p>
        </div>
        <div v-for="item in history" :key="item.id" class="ledger-row">
          <div>
            <p>{{ item.title }}</p>
            <p class="muted mt-1 text-xs">
              {{ displayDateTime(item.created_at) }} · {{ labels[item.status] }}
            </p>
          </div>
          <button
            class="secondary-button"
            type="button"
            :disabled="busy"
            @click="loadRecord(item.id)"
          >
            查看
          </button>
        </div>
      </section>
    </template>
    <div v-if="dialog" class="modal-backdrop" @click.self="dialog = null">
      <section
        class="panel analysis-dialog rounded-2xl p-6"
        role="dialog"
        aria-modal="true"
        aria-label="清理分析记录"
      >
        <div>
          <h2 class="text-xl font-bold">清理这条分析记录？</h2>
          <p class="muted my-4 leading-7">
            删除统计快照与分析结果，保留审计摘要和请求去重信息，不影响账本；已发送至外部服务的数据无法由此撤回。
          </p>
          <div class="flex gap-3">
            <button class="primary-button" @click="clear">确认清理</button>
            <button class="secondary-button" @click="dialog = null">
              取消
            </button>
          </div>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.analysis-json {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 0.75rem;
  line-height: 1.7;
  max-height: 22rem;
  overflow: auto;
  padding: 1rem;
  border: 1px solid #355162;
  border-radius: 0.7rem;
}
.analysis-dialog {
  width: min(95vw, 48rem);
  max-height: 90vh;
  overflow-y: auto;
}
</style>
