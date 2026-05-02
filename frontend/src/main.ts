import { createApp } from 'vue'
import Vant from 'vant'
import 'vant/lib/index.css'
import './style.css'
import App from './App.vue'
import router from './router'

createApp(App).use(Vant).use(router).mount('#app')
