import { connectContext } from './app.js';
import { connectMemory } from './memory.js';
const $ = id => document.getElementById(id);
let epoch = 0;
const errors = {
  invalid_pairing_code: '配对码无效、已使用或已过期，请重新获取。',
  pairing_rate_limited: '尝试次数过多，请稍后一分钟再试。',
  pairing_not_configured: '尚未启用设备配对，请联系管理员配置。',
  session_expired: '会话已失效，请重新配对。',
  credential_expired: '后台授权已到期，请联系管理员更新。',
};
function status(text, error = false) {
  $('connection-status').textContent = text;
  $('connection-status').className = error ? 'notice error' : 'muted';
}
async function auth(path, body) {
  const response = await fetch('/auth/' + path, {
    method: body ? 'POST' : 'GET', headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(15000),
  });
  const data = await response.json();
  if (!response.ok) throw Error(errors[data.error] || '认证服务不可用，请稍后重试。');
  return data;
}
function clearConnections() { $('memory-logout').click(); $('logout').click(); }
async function connectSession(data, stamp) {
  const config = await fetch('/config').then(r => r.json());
  if (stamp !== epoch) return;
  if (config.memory && !config.local) $('memory-source').value = 'memory';
  const results = await Promise.all(data.services.map(name => name === 'memory' ? connectMemory('', true) : connectContext('', true)));
  if (stamp !== epoch) return;
  status(results.every(Boolean) ? '已登录 · 会话到期：' + new Date(data.expires_at * 1000).toLocaleString('zh-CN') : '已登录，部分服务不可达，请查看下方提示。', !results.every(Boolean));
}
$('pair-form').addEventListener('submit', async event => {
  event.preventDefault();
  const stamp = ++epoch, code = $('pair-code').value;
  $('pair-code').value = ''; $('pair-submit').disabled = true; $('connection-disconnect').disabled = true; status('正在配对…');
  try { const data = await auth('pair', { code }); if (stamp === epoch) await connectSession(data, stamp); }
  catch (error) { if (stamp === epoch) status(error.message, true); }
  finally { $('pair-submit').disabled = false; $('connection-disconnect').disabled = false; }
});
$('connection-disconnect').addEventListener('click', async () => {
  ++epoch; clearConnections();
  try {
    const config = await fetch('/config').then(r => r.json());
    if (config.pairing) await auth('logout', {});
    status('已退出，当前设备会话已撤销。');
  } catch { status('本页已断开，但服务端退出失败。请恢复网络后再次点击退出登录。', true); }
});
window.addEventListener('workcontext-session-expired', () => {
  ++epoch; clearConnections(); status('会话或后台授权已失效，请重新配对或联系管理员。', true);
});
(async () => {
  const stamp = epoch;
  try {
    const config = await fetch('/config').then(r => r.json());
    $('pair-form').hidden = !config.pairing;
    if (!config.pairing) { status('此服务尚未启用配对，可导入原有连接文件。'); return; }
    const data = await auth('session');
    if (stamp === epoch) await connectSession(data, stamp);
  } catch (error) {
    if (stamp === epoch) status(error.message === errors.session_expired ? '输入一次性配对码以连接工作台。' : error.message);
  }
})();
