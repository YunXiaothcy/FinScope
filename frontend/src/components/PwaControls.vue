<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useConnectivityStore } from '../stores/connectivity'
import { registerOnlineWorker, type InstallPrompt } from '../utils/pwa'

const network = useConnectivityStore()
const route = useRoute()
const canRefresh = computed(() =>
  /\/(finance|business|dashboard)$/.test(route.path),
)
const installPrompt = ref<InstallPrompt | null>(null)
const update = ref<ServiceWorkerRegistration | null>(null)
const installed = ref(false)
const help = ref(false)
const message = ref('')
let disposed = false
let reloadApproved = false
const updateOnline = () => network.update(navigator.onLine)
function beforeInstall(event: Event) {
  event.preventDefault()
  installPrompt.value = event as InstallPrompt
}
function afterInstall() {
  installed.value = true
  installPrompt.value = null
}
async function install() {
  const prompt = installPrompt.value
  if (!prompt) return
  installPrompt.value = null
  try {
    await prompt.prompt()
    const choice = await prompt.userChoice
    if (!disposed && choice.outcome === 'accepted') installed.value = true
  } catch {
    message.value = '未能打开安装窗口，可使用浏览器菜单添加到主屏幕。'
  }
}
function activateUpdate() {
  if (
    !window.confirm(
      '请先完成或确认正在提交的操作。现在刷新会丢失尚未保存的表单，是否继续？',
    )
  )
    return
  reloadApproved = true
  if (update.value?.waiting)
    update.value.waiting.postMessage({ type: 'ACTIVATE_UPDATE' })
  else window.location.reload()
}
function controllerChanged() {
  if (reloadApproved) window.location.reload()
}
function refreshData() {
  window.dispatchEvent(new Event('finscope:refresh'))
  network.reconnected = false
}
onMounted(async () => {
  installed.value =
    window.matchMedia('(display-mode: standalone)').matches ||
    !!(navigator as Navigator & { standalone?: boolean }).standalone
  window.addEventListener('online', updateOnline)
  window.addEventListener('offline', updateOnline)
  window.addEventListener('beforeinstallprompt', beforeInstall)
  window.addEventListener('appinstalled', afterInstall)
  navigator.serviceWorker?.addEventListener(
    'controllerchange',
    controllerChanged,
  )
  try {
    await registerOnlineWorker(
      import.meta.env.PROD,
      window.isSecureContext,
      navigator.serviceWorker,
      (value) => {
        if (!disposed) update.value = value
      },
    )
  } catch {
    if (!disposed) message.value = '应用安装支持暂不可用，可继续在线使用网页。'
  }
})
onUnmounted(() => {
  disposed = true
  window.removeEventListener('online', updateOnline)
  window.removeEventListener('offline', updateOnline)
  window.removeEventListener('beforeinstallprompt', beforeInstall)
  window.removeEventListener('appinstalled', afterInstall)
  navigator.serviceWorker?.removeEventListener(
    'controllerchange',
    controllerChanged,
  )
})
</script>

<template>
  <div class="pwa-controls">
    <div class="pwa-tools">
      <span class="connection-label muted text-xs">
        <span
          class="connection-dot"
          :class="{ offline: !network.online }"
          aria-hidden="true"
        />
        {{
          !network.online
            ? 'FinScope · 当前离线'
            : installed
              ? 'FinScope · 独立应用'
              : 'FinScope · 在线工作'
        }}
      </span>
      <button
        v-if="installPrompt && !installed"
        class="accent text-sm"
        @click="install"
      >
        安装应用
      </button>
      <button v-if="update" class="accent text-sm" @click="activateUpdate">
        有新版本，更新
      </button>
      <button class="muted text-sm" :aria-expanded="help" @click="help = !help">
        手机使用与安装
      </button>
    </div>
    <p
      v-if="!network.online"
      role="status"
      class="pending-panel p-3 text-sm leading-6"
    >
      当前已离线，页面内容可能过时，暂不可提交。FinScope
      不提供离线记账；恢复网络后请刷新核对。
    </p>
    <div
      v-else-if="network.reconnected"
      role="status"
      class="status-panel flex flex-wrap items-center gap-3 p-3 text-sm"
    >
      <span>网络已恢复，请核对最新数据；未确认的提交请使用原编号重试。</span>
      <button v-if="canRefresh" class="accent" @click="refreshData">
        刷新当前数据
      </button>
      <button class="muted" @click="network.reconnected = false">
        关闭提示
      </button>
    </div>
    <p v-if="message" role="status" class="muted px-4 py-2 text-sm">
      {{ message }}
    </p>
    <section
      v-if="help"
      class="status-panel mx-auto max-w-5xl rounded-xl p-5 text-sm leading-7"
    >
      <h2 class="font-bold">同一账号，使用同一服务端账本</h2>
      <p class="mt-2">
        手机和电脑打开同一个服务器地址并登录同一账号。切回账本、经营页或总览时会刷新；页面也保留手动刷新入口。
      </p>
      <p class="mt-2">
        Android /
        桌面支持安装提示的浏览器可点“安装应用”，或使用浏览器菜单。iPhone 请用
        Safari 的“分享 → 添加到主屏幕”。安装入口是否出现由浏览器决定。
      </p>
      <p class="muted mt-2">
        安装需要 HTTPS 或本机 localhost；开发页面不注册
        PWA，生产预览才启用。局域网 HTTP 可测试在线页面，通常不能安装
        PWA。应用必须联网使用，不缓存账务、票据或分析结果。
      </p>
    </section>
  </div>
</template>
