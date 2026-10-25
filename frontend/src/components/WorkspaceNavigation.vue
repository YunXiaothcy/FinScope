<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getWorkspace, type Workspace } from '../api/workspaces'
import { useAuthStore } from '../stores/auth'
import { isNavigationActive, workspaceNavigation } from '../utils/navigation'
import AppIcon from './AppIcon.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const workspace = ref<Workspace | null>(null)
const more = ref(false)
const moreButton = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const base = computed(() => `/workspaces/${String(route.params.workspaceId)}`)
const links = computed(() =>
  workspaceNavigation(base.value, workspace.value?.type === 'business'),
)
const primary = computed(() =>
  links.value.filter((item) =>
    ['dashboard', 'finance', 'analysis'].includes(item.id),
  ),
)
const extra = computed(() =>
  links.value.filter(
    (item) => !['dashboard', 'finance', 'analysis'].includes(item.id),
  ),
)
const moreActive = computed(() =>
  extra.value.some((item) => isNavigationActive(route.path, item.to)),
)
const roles: Record<string, string> = {
  owner: '所有者',
  admin: '管理员',
  member: '成员',
  viewer: '只读成员',
}
let generation = 0
let mobileMedia: MediaQueryList | undefined

watch(
  () => [route.params.workspaceId, auth.user?.id],
  async () => {
    const current = ++generation
    workspace.value = null
    more.value = false
    if (!route.params.workspaceId || !auth.user) return
    try {
      const value = await getWorkspace(String(route.params.workspaceId))
      if (current === generation) workspace.value = value
    } catch {
      /* 页面展示会话/权限错误，导航不保留旧空间信息。 */
    }
  },
  { immediate: true },
)
watch(
  () => route.fullPath,
  () => {
    more.value = false
  },
)
async function toggleMore() {
  more.value = !more.value
  if (more.value) {
    await nextTick()
    menu.value?.focus()
  }
}
function closeMore(restoreFocus = true) {
  more.value = false
  if (restoreFocus) moreButton.value?.focus()
}
function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape' && more.value) closeMore()
}
function resized() {
  if (!mobileMedia?.matches) closeMore(false)
}
function visit(id: string) {
  more.value = false
  if (id === 'finance') window.dispatchEvent(new Event('finscope:ledger'))
}
function quick() {
  window.dispatchEvent(new Event('finscope:quick-entry'))
  void router.push(`${base.value}/finance?quick=1#quick-entry`)
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  mobileMedia = window.matchMedia('(max-width: 900px)')
  mobileMedia.addEventListener('change', resized)
})
onUnmounted(() => {
  generation++
  window.removeEventListener('keydown', onKey)
  mobileMedia?.removeEventListener('change', resized)
})
</script>

<template>
  <template v-if="route.params.workspaceId && auth.user">
    <aside class="workspace-sidebar" aria-label="工作空间导航">
      <RouterLink to="/" class="workspace-brand">
        <span class="brand-mark">F</span>
        <span>FinScope</span>
      </RouterLink>
      <div class="workspace-context">
        <p class="eyebrow">当前工作空间</p>
        <p class="mt-2 font-semibold">{{ workspace?.name || '正在加载…' }}</p>
        <p v-if="workspace" class="muted mt-1 text-xs">
          {{ workspace.type === 'business' ? '商户' : '个人' }} ·
          {{ workspace.base_currency }} · {{ roles[workspace.role] }}
        </p>
      </div>
      <nav v-if="workspace" class="workspace-links" aria-label="主要功能">
        <template v-for="group in ['main', 'tools']" :key="group">
          <p v-if="group === 'tools'" class="workspace-nav-label">数据录入</p>
          <RouterLink
            v-for="item in links.filter((entry) => entry.group === group)"
            :key="item.id"
            :to="item.to"
            :class="{ selected: isNavigationActive(route.path, item.to) }"
            :aria-current="
              isNavigationActive(route.path, item.to) ? 'page' : undefined
            "
            @click="visit(item.id)"
          >
            <AppIcon :name="item.icon" />
            <span>{{ item.label }}</span>
            <span
              v-if="isNavigationActive(route.path, item.to)"
              class="nav-current-dot"
              aria-hidden="true"
            />
          </RouterLink>
        </template>
      </nav>
      <div class="workspace-nav-footer">
        <button
          v-if="workspace && workspace.role !== 'viewer'"
          class="primary-button w-full"
          @click="quick"
        >
          <AppIcon name="plus" :size="18" />
          记一笔
        </button>
        <RouterLink to="/" class="workspace-switch">
          <AppIcon name="switch" :size="18" />
          切换空间与设置
        </RouterLink>
      </div>
    </aside>
    <template v-if="workspace">
      <div
        v-if="more"
        class="mobile-menu-dismiss"
        @click="closeMore()"
        aria-hidden="true"
      />
      <Transition name="popover">
        <section
          v-if="more"
          id="workspace-more"
          ref="menu"
          tabindex="-1"
          class="mobile-more panel"
          aria-label="更多工作空间功能"
        >
          <div class="flex items-center justify-between gap-3">
            <h2 class="font-semibold">更多功能</h2>
            <button class="muted" @click="closeMore()">关闭</button>
          </div>
          <p class="muted mt-1 text-xs">{{ workspace.name }}</p>
          <nav class="mobile-extra-links mt-3" aria-label="其他功能">
            <RouterLink
              v-for="item in extra"
              :key="item.id"
              :to="item.to"
              :class="{ selected: isNavigationActive(route.path, item.to) }"
              :aria-current="
                isNavigationActive(route.path, item.to) ? 'page' : undefined
              "
              @click="visit(item.id)"
            >
              <AppIcon :name="item.icon" :size="18" />
              {{ item.label }}
            </RouterLink>
            <RouterLink to="/" @click="more = false">
              <AppIcon name="switch" :size="18" />
              空间与设置
            </RouterLink>
          </nav>
        </section>
      </Transition>
      <nav class="workspace-mobile-nav" aria-label="手机主导航">
        <template v-for="item in primary" :key="item.id">
          <button
            v-if="item.id === 'analysis' && workspace.role !== 'viewer'"
            class="quick-nav"
            @click="quick"
          >
            <AppIcon name="plus" />
            记一笔
          </button>
          <RouterLink
            :to="item.to"
            :class="{ selected: isNavigationActive(route.path, item.to) }"
            :aria-current="
              isNavigationActive(route.path, item.to) ? 'page' : undefined
            "
            @click="visit(item.id)"
          >
            <AppIcon :name="item.icon" />
            {{ item.shortLabel }}
          </RouterLink>
        </template>
        <button
          ref="moreButton"
          :aria-expanded="more"
          aria-controls="workspace-more"
          :class="{ selected: more || moreActive }"
          @click="toggleMore"
        >
          <AppIcon name="more" />
          更多
        </button>
      </nav>
    </template>
  </template>
</template>
