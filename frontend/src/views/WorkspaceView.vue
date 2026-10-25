<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  addMember,
  createWorkspace,
  getWorkspace,
  listMembers,
  listWorkspaces,
  type Member,
  type Workspace,
  type WorkspaceType,
} from '../api/workspaces'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const mode = ref<'register' | 'login'>('register')
const email = ref('')
const password = ref('')
const displayName = ref('')
const authError = ref('')
const workspaceError = ref('')
const memberError = ref('')
const busy = ref(false)
const workspaces = ref<Workspace[]>([])
const selectedId = ref('')
const members = ref<Member[]>([])
const workspaceName = ref('')
const workspaceType = ref<WorkspaceType>('personal')
const baseCurrency = ref('CNY')
const memberEmail = ref('')
const memberRole = ref<'admin' | 'member' | 'viewer'>('member')
const profileName = ref('')
const currentPassword = ref('')
const newPassword = ref('')
const settingsMessage = ref('')
const selectedWorkspace = computed(() =>
  workspaces.value.find((item) => item.id === selectedId.value),
)

function errorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    auth.user = null
    auth.csrfToken = ''
    return '登录已过期，请重新登录'
  }
  return error instanceof Error ? error.message : '操作失败，请重试'
}

async function refreshWorkspaces() {
  workspaces.value = await listWorkspaces()
  if (
    selectedId.value &&
    !workspaces.value.some((item) => item.id === selectedId.value)
  ) {
    selectedId.value = ''
    members.value = []
  }
}

async function reloadWorkspaces() {
  workspaceError.value = ''
  try {
    await refreshWorkspaces()
  } catch (error) {
    workspaceError.value = errorMessage(error)
  }
}

function setMode(value: 'register' | 'login') {
  mode.value = value
  authError.value = ''
}

async function submitAuth() {
  authError.value = ''
  busy.value = true
  try {
    if (mode.value === 'register')
      await auth.register(
        email.value.trim(),
        password.value,
        displayName.value.trim(),
      )
    else await auth.login(email.value.trim(), password.value)
    password.value = ''
    profileName.value = auth.user?.display_name || ''
    await refreshWorkspaces()
  } catch (error) {
    authError.value = errorMessage(error)
  } finally {
    busy.value = false
  }
}

async function selectWorkspace(id: string) {
  workspaceError.value = ''
  try {
    const workspace = await getWorkspace(id)
    const index = workspaces.value.findIndex((item) => item.id === id)
    if (index >= 0) workspaces.value[index] = workspace
    members.value = await listMembers(id)
    selectedId.value = id
  } catch (error) {
    workspaceError.value = errorMessage(error)
  }
}

async function submitWorkspace() {
  workspaceError.value = ''
  busy.value = true
  try {
    const created = await createWorkspace(
      workspaceName.value.trim(),
      workspaceType.value,
      baseCurrency.value.trim().toUpperCase(),
      auth.csrfToken,
    )
    workspaceName.value = ''
    await refreshWorkspaces()
    await selectWorkspace(created.id)
  } catch (error) {
    workspaceError.value = errorMessage(error)
  } finally {
    busy.value = false
  }
}

async function submitMember() {
  if (!selectedId.value) return
  memberError.value = ''
  busy.value = true
  try {
    await addMember(
      selectedId.value,
      memberEmail.value.trim(),
      memberRole.value,
      auth.csrfToken,
    )
    memberEmail.value = ''
    members.value = await listMembers(selectedId.value)
  } catch (error) {
    memberError.value = errorMessage(error)
  } finally {
    busy.value = false
  }
}

async function logout() {
  authError.value = ''
  try {
    await auth.logout()
    workspaces.value = []
    selectedId.value = ''
    members.value = []
  } catch (error) {
    authError.value = errorMessage(error)
  }
}

async function saveProfile() {
  settingsMessage.value = ''
  busy.value = true
  try {
    await auth.updateProfile(profileName.value.trim())
    settingsMessage.value = '资料已更新'
  } catch (error) {
    settingsMessage.value = errorMessage(error)
  } finally {
    busy.value = false
  }
}

async function savePassword() {
  settingsMessage.value = ''
  busy.value = true
  try {
    await auth.changePassword(currentPassword.value, newPassword.value)
    currentPassword.value = ''
    newPassword.value = ''
    settingsMessage.value = '密码已更新，其他设备需要重新登录'
  } catch (error) {
    settingsMessage.value = errorMessage(error)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    await auth.initialize()
    profileName.value = auth.user?.display_name || ''
    if (auth.user) await refreshWorkspaces()
  } catch (error) {
    authError.value = errorMessage(error)
  }
})
</script>

<template>
  <main class="min-h-screen px-5 py-8 sm:py-12">
    <div class="mx-auto max-w-5xl">
      <header class="mb-10 flex flex-wrap items-center justify-between gap-4">
        <div class="flex items-center gap-3">
          <div class="brand-mark">F</div>
          <span class="text-lg font-semibold">FinScope</span>
          <span class="stage-label">财务工作台</span>
        </div>
        <button
          v-if="auth.user"
          class="text-sm muted hover:text-white"
          @click="logout"
        >
          退出登录
        </button>
      </header>

      <div v-if="!auth.initialized" class="panel rounded-3xl p-8">
        正在检查登录状态…
      </div>
      <div
        v-else-if="!auth.user"
        class="grid gap-10 lg:grid-cols-[1.1fr_0.9fr] lg:items-center"
      >
        <section>
          <p class="eyebrow">智能财务分析平台</p>
          <h1 class="mt-5 text-4xl font-semibold leading-tight sm:text-5xl">
            从一个清晰的
            <br />
            <span class="accent">财务空间开始。</span>
          </h1>
          <p class="mt-6 max-w-xl leading-8 muted">
            建立独立的工作空间，管理账户、收入、支出和转账，随时查看余额与收支汇总。
          </p>
          <div class="mt-8 flex flex-wrap gap-3">
            <span class="pill">独立工作空间</span>
            <span class="pill">成员权限</span>
            <span class="pill">安全会话</span>
          </div>
        </section>
        <section class="panel rounded-3xl p-6 sm:p-8">
          <div class="mb-7 flex gap-2">
            <button
              class="tab"
              :class="{ active: mode === 'register' }"
              :aria-pressed="mode === 'register'"
              @click="setMode('register')"
            >
              注册
            </button>
            <button
              class="tab"
              :class="{ active: mode === 'login' }"
              :aria-pressed="mode === 'login'"
              @click="setMode('login')"
            >
              登录
            </button>
          </div>
          <form class="space-y-5" @submit.prevent="submitAuth">
            <label v-if="mode === 'register'" class="field-label">
              显示名称
              <input
                v-model="displayName"
                class="field"
                required
                maxlength="80"
                autocomplete="name"
                placeholder="你的名字"
              />
            </label>
            <label class="field-label">
              邮箱
              <input
                v-model="email"
                class="field"
                required
                type="email"
                autocomplete="email"
                placeholder="name@example.com"
              />
            </label>
            <label class="field-label">
              密码
              <input
                v-model="password"
                class="field"
                required
                type="password"
                :minlength="mode === 'register' ? 12 : undefined"
                :autocomplete="
                  mode === 'register' ? 'new-password' : 'current-password'
                "
                placeholder="至少 12 个字符"
              />
            </label>
            <p v-if="authError" class="error text-sm" role="alert">
              {{ authError }}
            </p>
            <button
              class="primary-button w-full"
              :disabled="busy"
              type="submit"
            >
              {{ busy ? '请稍候…' : mode === 'register' ? '创建账号' : '登录' }}
            </button>
          </form>
        </section>
      </div>

      <div v-else class="space-y-7">
        <section class="panel rounded-3xl p-6 sm:p-8">
          <p class="eyebrow">欢迎回来</p>
          <h1 class="mt-3 text-3xl font-semibold">
            {{ auth.user.display_name }} 的工作空间
          </h1>
          <p class="mt-3 muted">
            当前账号：{{ auth.user.email }}。你的空间只会显示给已授权成员。
          </p>
          <p v-if="authError" class="error mt-3" role="alert">
            {{ authError }}
          </p>
        </section>
        <div class="grid gap-7 lg:grid-cols-[0.9fr_1.1fr]">
          <section class="panel rounded-3xl p-6 sm:p-8">
            <h2 class="text-xl font-semibold">创建工作空间</h2>
            <p class="mt-2 text-sm muted">
              一个账号可以拥有多个个人或商户空间。
            </p>
            <form class="mt-6 space-y-4" @submit.prevent="submitWorkspace">
              <label class="field-label">
                空间名称
                <input
                  v-model="workspaceName"
                  class="field"
                  required
                  maxlength="80"
                  placeholder="例如：我的日常账本"
                />
              </label>
              <label class="field-label">
                空间类型
                <select v-model="workspaceType" class="field">
                  <option value="personal">个人</option>
                  <option value="business">商户</option>
                </select>
              </label>
              <label class="field-label">
                基础货币
                <input
                  v-model="baseCurrency"
                  class="field"
                  required
                  maxlength="3"
                  pattern="[A-Za-z]{3}"
                  placeholder="CNY"
                />
              </label>
              <p v-if="workspaceError" class="error text-sm" role="alert">
                {{ workspaceError }}
              </p>
              <button
                class="primary-button w-full"
                :disabled="busy"
                type="submit"
              >
                {{ busy ? '创建中…' : '创建空间' }}
              </button>
            </form>
          </section>
          <section class="panel rounded-3xl p-6 sm:p-8">
            <div class="flex items-center justify-between gap-3">
              <h2 class="text-xl font-semibold">我的工作空间</h2>
              <button
                class="text-sm accent hover:underline"
                @click="reloadWorkspaces"
              >
                刷新
              </button>
            </div>
            <p v-if="workspaces.length === 0" class="mt-6 muted">
              还没有工作空间。创建一个开始使用。
            </p>
            <div v-else class="mt-5 space-y-3">
              <button
                v-for="workspace in workspaces"
                :key="workspace.id"
                class="workspace-card w-full text-left"
                :class="{ selected: selectedId === workspace.id }"
                @click="selectWorkspace(workspace.id)"
              >
                <span class="font-medium">{{ workspace.name }}</span>
                <span class="block pt-1 text-sm muted">
                  {{ workspace.type === 'personal' ? '个人' : '商户' }} ·
                  {{ workspace.base_currency }} · {{ workspace.role }}
                </span>
              </button>
            </div>
          </section>
        </div>
        <section v-if="selectedWorkspace" class="panel rounded-3xl p-6 sm:p-8">
          <RouterLink
            class="primary-button mb-6 mr-3 inline-block"
            :to="`/workspaces/${selectedWorkspace.id}/dashboard`"
          >
            分析总览 →
          </RouterLink>
          <RouterLink
            class="primary-button mb-6 inline-block"
            :to="`/workspaces/${selectedWorkspace.id}/finance`"
          >
            打开账本 →
          </RouterLink>
          <RouterLink
            v-if="selectedWorkspace.type === 'business'"
            class="primary-button mb-6 ml-3 inline-block"
            :to="`/workspaces/${selectedWorkspace.id}/business`"
          >
            商户经营 →
          </RouterLink>
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p class="eyebrow">当前空间</p>
              <h2 class="mt-2 text-2xl font-semibold">
                {{ selectedWorkspace.name }}
              </h2>
              <p class="mt-2 text-sm muted">
                空间 ID：{{ selectedWorkspace.id }}
              </p>
            </div>
            <span class="pill">你的角色：{{ selectedWorkspace.role }}</span>
          </div>
          <div class="mt-7 grid gap-7 lg:grid-cols-2">
            <div>
              <h3 class="font-semibold">成员</h3>
              <div
                v-for="member in members"
                :key="member.user_id"
                class="member-row"
              >
                <span>
                  {{ member.display_name }}
                  <span class="ml-2 text-sm muted">{{ member.email }}</span>
                </span>
                <span class="text-sm accent">{{ member.role }}</span>
              </div>
            </div>
            <form
              v-if="selectedWorkspace.role === 'owner'"
              class="space-y-4"
              @submit.prevent="submitMember"
            >
              <h3 class="font-semibold">添加已注册用户</h3>
              <label class="field-label">
                用户邮箱
                <input
                  v-model="memberEmail"
                  class="field"
                  type="email"
                  required
                  placeholder="member@example.com"
                />
              </label>
              <label class="field-label">
                角色
                <select v-model="memberRole" class="field">
                  <option value="admin">管理员</option>
                  <option value="member">成员</option>
                  <option value="viewer">只读查看者</option>
                </select>
              </label>
              <p v-if="memberError" class="error text-sm" role="alert">
                {{ memberError }}
              </p>
              <button class="primary-button" :disabled="busy" type="submit">
                添加成员
              </button>
            </form>
          </div>
        </section>
        <section class="panel rounded-3xl p-6 sm:p-8">
          <h2 class="text-xl font-semibold">账号设置</h2>
          <div class="mt-6 grid gap-8 lg:grid-cols-2">
            <form class="space-y-4" @submit.prevent="saveProfile">
              <h3 class="font-semibold">个人资料</h3>
              <label class="field-label">
                显示名称
                <input
                  v-model="profileName"
                  class="field"
                  required
                  maxlength="80"
                />
              </label>
              <button class="primary-button" :disabled="busy" type="submit">
                保存资料
              </button>
            </form>
            <form class="space-y-4" @submit.prevent="savePassword">
              <h3 class="font-semibold">修改密码</h3>
              <label class="field-label">
                当前密码
                <input
                  v-model="currentPassword"
                  class="field"
                  type="password"
                  required
                  autocomplete="current-password"
                />
              </label>
              <label class="field-label">
                新密码
                <input
                  v-model="newPassword"
                  class="field"
                  type="password"
                  required
                  minlength="12"
                  autocomplete="new-password"
                />
              </label>
              <button class="primary-button" :disabled="busy" type="submit">
                更新密码
              </button>
            </form>
          </div>
          <p v-if="settingsMessage" class="mt-4 text-sm accent" role="status">
            {{ settingsMessage }}
          </p>
        </section>
        <p class="text-sm muted">
          选择空间后进入分析总览查看趋势，或打开账本管理账户与收支；商户空间还可管理应收应付、利润和现金流。
        </p>
      </div>
    </div>
  </main>
</template>
