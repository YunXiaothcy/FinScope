import { defineStore } from 'pinia'
import {
  changePassword,
  getSession,
  loginUser,
  logoutUser,
  registerUser,
  updateProfile,
  type AuthResponse,
  type User,
} from '../api/auth'
import { ApiError } from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as User | null,
    csrfToken: '',
    initialized: false,
  }),
  actions: {
    acceptSession(result: AuthResponse) {
      this.user = result.user
      this.csrfToken = result.csrf_token
    },
    async initialize() {
      try {
        this.acceptSession(await getSession())
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 401)) throw error
        this.user = null
        this.csrfToken = ''
      } finally {
        this.initialized = true
      }
    },
    async register(email: string, password: string, displayName: string) {
      this.acceptSession(await registerUser(email, password, displayName))
    },
    async login(email: string, password: string) {
      this.acceptSession(await loginUser(email, password))
    },
    async logout() {
      try {
        await logoutUser(this.csrfToken)
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 401)) throw error
      }
      this.user = null
      this.csrfToken = ''
      for (const key of Object.keys(sessionStorage)) {
        if (key.startsWith('finscope.pending.')) sessionStorage.removeItem(key)
      }
    },
    async updateProfile(displayName: string) {
      this.user = await updateProfile(displayName, this.csrfToken)
    },
    async changePassword(currentPassword: string, newPassword: string) {
      await changePassword(currentPassword, newPassword, this.csrfToken)
    },
  },
})
