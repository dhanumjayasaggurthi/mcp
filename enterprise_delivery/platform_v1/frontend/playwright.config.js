import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir:'./e2e',fullyParallel:false,workers:1,retries:0,
  use:{baseURL:'http://127.0.0.1:4173',viewport:{width:1440,height:1000},trace:'retain-on-failure',screenshot:'only-on-failure'},
  reporter:[['list'],['html',{open:'never'}]],
  webServer:{command:'npm run preview -- --host 127.0.0.1',url:'http://127.0.0.1:4173',reuseExistingServer:false},
});
