import { createRouter, createWebHistory } from 'vue-router'
import HeroView from '@/views/HeroView.vue'

// vue-router history mode pairs with the server's SPA history fallback (non-/api → index.html).
// /fact/:id is the CANONICAL fact route — it must match branding.fact_deeplink (the path Claude
// Code's MCP receipts + the CLI footer emit). /inspector/:id redirects to it for back-compat.
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'field', component: HeroView },
    { path: '/inspector', name: 'inspector', component: () => import('@/views/InspectorView.vue') },
    { path: '/fact/:id', name: 'fact', component: () => import('@/views/InspectorView.vue') },
    { path: '/inspector/:id', redirect: (to) => ({ path: `/fact/${to.params.id}` }) },
    { path: '/triage', name: 'triage', component: () => import('@/views/TriageView.vue') },
    { path: '/search', name: 'search', component: () => import('@/views/SearchView.vue') },
    // catch-all: a stale/mistyped path (or a renamed deep-link) gets a real 404, never a blank pane.
    { path: '/:pathMatch(.*)*', name: 'notfound', component: () => import('@/views/NotFoundView.vue') },
  ],
})
