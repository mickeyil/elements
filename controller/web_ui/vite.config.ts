import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  build: {
    // Built by `./elemctl setup` into the repo-local, gitignored artifact dir
    // (alongside local/venv); served from there by controller/elemctl/web.py.
    outDir: '../../local/web_dist',
    emptyOutDir: true,
  },
});
