import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: '../integrations/agh/extensions/main/client', emptyOutDir: true,
    lib: { entry: 'src/features/agh/plugin.tsx', formats: ['es'], fileName: () => 'index.js', cssFileName: 'style' },
    rolldownOptions: { external: ['react', 'react/jsx-runtime', 'react-dom', 'react-dom/client', '@agnes/cordis', '@agnes/web-client'] },
  },
});
