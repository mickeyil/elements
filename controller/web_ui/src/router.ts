import { createRouter, createWebHistory } from 'vue-router';

import LayoutEditorPage from './pages/LayoutEditorPage.vue';
import LogsPage from './pages/LogsPage.vue';
import PanelPage from './pages/PanelPage.vue';
import StatusPage from './pages/StatusPage.vue';
import ViewerPage from './pages/ViewerPage.vue';

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: StatusPage,
    },
    {
      path: '/panel',
      component: PanelPage,
    },
    {
      path: '/viewer',
      component: ViewerPage,
    },
    {
      path: '/logs',
      component: LogsPage,
    },
    {
      path: '/layouts/:deviceUid',
      component: LayoutEditorPage,
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
});
