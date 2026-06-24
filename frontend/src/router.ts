import { createRouter, createWebHistory } from 'vue-router'
import HeroView from '@/views/HeroView.vue'

// vue-router history mode pairs with the server's SPA history fallback (non-/api → index.html).
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'field', component: HeroView },
    { path: '/inspector', name: 'inspector', component: () => import('@/views/InspectorView.vue') },
    { path: '/inspector/:id', name: 'fact', component: () => import('@/views/InspectorView.vue') },
    { path: '/triage', name: 'triage', component: () => import('@/views/TriageView.vue') },
    { path: '/search', name: 'search', component: () => import('@/views/SearchView.vue') },
  ],
})
