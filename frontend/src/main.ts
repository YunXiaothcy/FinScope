import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { ElButton } from 'element-plus'
import 'element-plus/es/components/button/style/css'
import './style.css'
import './motion.css'
import App from './App.vue'
import { router } from './router'

createApp(App)
  .use(createPinia())
  .use(router)
  .component('ElButton', ElButton)
  .mount('#app')
