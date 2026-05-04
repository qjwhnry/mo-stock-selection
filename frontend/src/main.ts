import { createApp } from 'vue'
import Vant from 'vant'
import 'vant/lib/index.css'
import './style.css'
import App from './App.vue'
import router from './router'

// 创建 Vue 应用实例，依次注册 Vant UI 组件库和 Vue Router 路由管理器，然后挂载到 #app 容器
createApp(App).use(Vant).use(router).mount('#app')