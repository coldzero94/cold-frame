import { createApp } from 'vue'
import 'uno.css'
import '@/style.css'
import App from '@/App.vue'
import { router } from '@/router'
import { reportError } from '@/appError'

const app = createApp(App)

// Catch-all safety net: a crashed component (Vue errorHandler), an unhandled promise rejection, or
// an uncaught script error surfaces a calm banner (App.vue) instead of dying silently in the console.
app.config.errorHandler = (err) => {
  console.error(err)
  reportError(err)
}
window.addEventListener('unhandledrejection', (e) => reportError(e.reason))
window.addEventListener('error', (e) => reportError(e.error ?? e.message))

app.use(router).mount('#app')
