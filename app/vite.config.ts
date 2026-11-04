import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // The app reads ../data (codebooks and transcripts), which sits outside the Vite root.
  server: { port: 5174, strictPort: true, fs: { allow: ['..'] } },
})
