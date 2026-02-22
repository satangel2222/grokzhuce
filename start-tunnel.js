#!/usr/bin/env node
/**
 * Cloudflare Quick Tunnel 启动脚本（PM2 管理）
 * 暴露本地 Turnstile Solver (port 5072) 到公网
 *
 * 用法: pm2 start start-tunnel.js --name solver-tunnel
 */

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const LOCAL_PORT = process.env.SOLVER_PORT || 5072;
const CONFIG_PATH = path.join(process.env.HOME || process.env.USERPROFILE, '.claude', 'auto-register-config.json');

function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  } catch {
    return {};
  }
}

function writeConfig(updates) {
  const config = readConfig();
  Object.assign(config, updates);
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
  console.log(`[tunnel] Config updated: ${CONFIG_PATH}`);
}

function startTunnel() {
  console.log(`[tunnel] Starting cloudflared tunnel for localhost:${LOCAL_PORT}...`);

  const child = spawn('cloudflared', ['tunnel', '--url', `http://localhost:${LOCAL_PORT}`], {
    stdio: ['ignore', 'pipe', 'pipe']
  });

  let tunnelUrl = '';

  function parseLine(line) {
    const str = line.toString();
    process.stderr.write(str);

    // cloudflared outputs the URL in stderr
    const match = str.match(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/);
    if (match && !tunnelUrl) {
      tunnelUrl = match[0];
      console.log(`\n[tunnel] ✅ Solver URL: ${tunnelUrl}`);
      writeConfig({
        solver_url: `http://127.0.0.1:${LOCAL_PORT}`,
        solver_tunnel_url: tunnelUrl
      });
    }
  }

  child.stdout.on('data', parseLine);
  child.stderr.on('data', parseLine);

  child.on('error', (err) => {
    console.error(`[tunnel] Failed to start cloudflared: ${err.message}`);
    process.exit(1);
  });

  child.on('exit', (code) => {
    console.log(`[tunnel] cloudflared exited with code ${code}`);
    // Clear tunnel URL from config
    writeConfig({ solver_tunnel_url: '' });
    // PM2 will auto-restart
    process.exit(code || 1);
  });

  // Graceful shutdown
  process.on('SIGINT', () => {
    console.log('[tunnel] Shutting down...');
    child.kill('SIGTERM');
  });
  process.on('SIGTERM', () => {
    console.log('[tunnel] Shutting down...');
    child.kill('SIGTERM');
  });
}

startTunnel();
