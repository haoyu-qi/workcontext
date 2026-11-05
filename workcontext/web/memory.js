import { normalizeToken } from './token.mjs';

const $ = id => document.getElementById(id);
const element = (tag, text, cls) => {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = text;
  if (cls) value.className = cls;
  return value;
};
let token = '', sessionMode = false, epoch = 0, searchRequest = 0, detailRequest = 0;
const errors = {
  unauthorized: '凭证无效或已到期，请核对记忆服务 Token。',
  memory_not_configured: '尚未配置真实记忆服务，请设置 memory_url。',
  service_unavailable: '记忆服务暂不可达，请检查 Mini 或连接隧道。',
  invalid_request: '查询参数无效或记录不存在。',
  route_denied: '当前凭证没有读取记忆的权限。',
};
function status(text, error = false) {
  $('memory-stats').replaceChildren(element('p', text, error ? 'error' : 'state'));
}
async function api(path, credential = token) {
  if (!credential && !sessionMode) throw Error('请先连接真实记忆服务。');
  let response;
  try {
    response = await fetch('/api/' + $('memory-source').value + '-' + path, {
      headers: credential ? { Authorization: 'Bearer ' + credential } : {},
      signal: AbortSignal.timeout(35000),
    });
  } catch { throw Error('连接超时或网络不可用，请重试。'); }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && sessionMode) window.dispatchEvent(new Event('workcontext-session-expired'));
  if (!response.ok) throw Error(errors[data.error] || '请求失败：' + (data.error || response.status));
  return data;
}
function clear() {
  epoch++; token = ''; sessionMode = false; $('memory-token').value = '';
  $('memory-results').replaceChildren(); $('memory-detail').replaceChildren();
  status('已断开，凭证和查询结果已清除。');
}
function renderStats(data) {
  status(data.source === 'local' ? `${data.records} 条本机采集记录 · 已确认同步 ${data.queue.delivered||0} 条 · 待同步 ${data.queue.pending||0} 条` : `${data.records} 条真实来源记录 · ${data.notes} 条显式记忆`);
  const groups = element('div', undefined, 'devices');
  for (const group of data.agents || []) {
    const card = element('article', undefined, 'device');
    card.append(element('h3', group.device + ' / ' + group.agent), element('p', group.count + ' 条来源记录'));
    groups.append(card);
  }
  $('memory-stats').append(groups);
}
export async function connectMemory(input, session = false) {
  clear(); sessionMode = session;
  const stamp = epoch, button = $('memory-login').querySelector('button');
  button.disabled = true; status('正在连接真实记忆服务…');
  try {
    const candidate = session ? '' : normalizeToken(input), data = await api('stats', candidate);
    if (stamp !== epoch) return false;
    token = candidate; renderStats(data); return true;
  } catch (error) { if (stamp === epoch) status(error.message, true); return false; }
  finally { button.disabled = false; }
}
$('memory-login').addEventListener('submit', event => {event.preventDefault();connectMemory($('memory-token').value);});
$('memory-logout').addEventListener('click', clear);
$('memory-source').addEventListener('change',clear);
async function refresh() {
  const stamp = epoch;
  try { const data = await api('stats'); if (stamp === epoch) renderStats(data); }
  catch (error) { if (stamp === epoch) status(error.message, true); }
}
$('memory-refresh').addEventListener('click', refresh);
$('refresh').addEventListener('click', () => { if (token || sessionMode) refresh(); });
async function showDetail(id) {
  const stamp = epoch, request = ++detailRequest;
  const target = $('memory-detail');
  target.replaceChildren(element('p', '正在读取来源…', 'muted'));
  try {
    const data = await api('get?id=' + encodeURIComponent(id));
    if (stamp !== epoch || request !== detailRequest) return;
    target.replaceChildren(element('h3', data.title || '来源记录'), element('p', data.project || '', 'muted'), element('pre', data.content || data.text || ''));
    let refs = data.evidence || [];
    if (typeof refs === 'string') { try { refs = JSON.parse(refs); } catch { refs = []; } }
    for (const source of refs) {
      const button = element('button', '查看来源 ' + source.slice(0, 12), 'secondary');
      button.type = 'button'; button.addEventListener('click', () => showDetail(source)); target.append(button);
    }
    const details = element('details');
    details.append(element('summary', '完整记录与来源位置'), element('pre', JSON.stringify(data, null, 2)));
    target.append(details); target.scrollIntoView({ block: 'nearest' });
  } catch (error) { if (stamp === epoch && request === detailRequest) target.replaceChildren(element('p', error.message, 'error')); }
}
$('memory-search').addEventListener('submit', async event => {
  event.preventDefault();
  const query = $('memory-query').value.trim(), stamp = epoch, request = ++searchRequest;
  const target = $('memory-results'), button = event.currentTarget.querySelector('button');
  detailRequest++; $('memory-detail').replaceChildren();
  button.disabled = true; target.replaceChildren(element('p', '正在检索…', 'muted'));
  try {
    if (!query) throw Error('请输入检索词。');
    const data = await api('search?q=' + encodeURIComponent(query));
    if (stamp !== epoch || request !== searchRequest) return;
    target.replaceChildren(element('p', `找到 ${data.matches.length} 条相关记录（最多显示 8 条）`, 'muted'));
    if (!data.matches.length) target.append(element('p', '没有匹配记录，请换一个关键词。', 'empty'));
    for (const hit of data.matches) {
      const card = element('article', undefined, 'record');
      card.append(element('h3', hit.title || hit.project || '工作记录'), element('p', hit.type === 'notes' ? '显式记忆' : '来源记录', 'tag'), element('p', hit.excerpt || hit.content || hit.text));
      const button = element('button', '查看详情与来源', 'secondary');
      button.type = 'button'; button.addEventListener('click', () => showDetail(hit.id)); card.append(button); target.append(card);
    }
  } catch (error) { if (stamp === epoch && request === searchRequest) target.replaceChildren(element('p', error.message, 'error')); }
  finally { button.disabled = false; }
});
