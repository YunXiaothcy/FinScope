import { defineStore } from 'pinia'

export const useConnectivityStore = defineStore('connectivity', {
  state: () => ({ online: navigator.onLine, reconnected: false }),
  actions: {
    update(online: boolean) {
      if (online && !this.online) this.reconnected = true
      this.online = online
    },
  },
})
