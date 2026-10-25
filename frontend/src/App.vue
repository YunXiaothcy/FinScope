<script setup lang="ts">
import { useRoute } from 'vue-router'
import WorkspaceNavigation from './components/WorkspaceNavigation.vue'
import PwaControls from './components/PwaControls.vue'
import { useConnectivityStore } from './stores/connectivity'
const route = useRoute()
const network = useConnectivityStore()
</script>

<template>
  <div :class="{ 'app-workspace': !!route.params.workspaceId }">
    <PwaControls />
    <WorkspaceNavigation />
    <fieldset class="app-page" :disabled="!network.online">
      <RouterView v-slot="{ Component, route: currentRoute }">
        <Transition name="page" mode="out-in">
          <component :is="Component" :key="currentRoute.path" />
        </Transition>
      </RouterView>
    </fieldset>
  </div>
</template>
