import { createRouter, createWebHistory } from 'vue-router'
import WorkspaceView from '../views/WorkspaceView.vue'
import FinanceView from '../views/FinanceView.vue'
import BusinessView from '../views/BusinessView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: WorkspaceView },
    {
      path: '/workspaces/:workspaceId/analysis',
      component: () => import('../views/AnalysisView.vue'),
    },
    {
      path: '/workspaces/:workspaceId/dashboard',
      component: () => import('../views/DashboardView.vue'),
    },
    { path: '/workspaces/:workspaceId/finance', component: FinanceView },
    { path: '/workspaces/:workspaceId/business', component: BusinessView },
    {
      path: '/workspaces/:workspaceId/imports',
      component: () => import('../views/ImportView.vue'),
    },
    {
      path: '/workspaces/:workspaceId/receipts',
      component: () => import('../views/ReceiptView.vue'),
    },
  ],
})
