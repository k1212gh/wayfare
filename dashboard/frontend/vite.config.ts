import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';

function readEnvVar(key: string, fallback: string): string {
  if (process.env[key]) return process.env[key] as string;
  const envPath = resolve(__dirname, '../../.env');
  if (existsSync(envPath)) {
    const content = readFileSync(envPath, 'utf-8');
    const re = new RegExp(`^${key}\\s*=\\s*(.+)$`, 'm');
    const m = content.match(re);
    if (m) return m[1].trim();
  }
  return fallback;
}

const backendPort = readEnvVar('BACKEND_PORT', '8008');
const frontendPort = parseInt(readEnvVar('FRONTEND_PORT', '5173'), 10);
const apiTarget = process.env.VITE_API_TARGET || `http://127.0.0.1:${backendPort}`;

export default defineConfig({
  plugins: [react()],
  server: {
    port: frontendPort,
    proxy: {
      '/api': apiTarget,
    },
  },
});
