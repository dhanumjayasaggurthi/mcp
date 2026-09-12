import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const target=process.env.EDP_API_UPSTREAM || 'http://127.0.0.1:8080';
const proxy=Object.fromEntries(['/v1','/readyz','/livez','/openapi.json'].map(path=>[path,{target,changeOrigin:true}]));
export default defineConfig({plugins:[react()],server:{port:5173,proxy},preview:{port:4173,proxy}});
