import { readCredential, readCredentialFile } from './token.mjs';
import { connectContext } from './app.js';
import { connectMemory } from './memory.js';

const $ = id => document.getElementById(id);
function status(text, error = false) {
  $('connection-status').textContent = text;
  $('connection-status').className = error ? 'notice error' : 'muted';
}
function bindPicker(buttonId, fileId, service) {
  const button = $(buttonId), picker = $(fileId);
  button.addEventListener('click', () => picker.click());
  picker.addEventListener('change', async () => {
    const file = picker.files[0]; picker.value = '';
    if (!file) return;
    button.disabled = true;
    try {
      const input = await readCredentialFile(file);
      if (service) {
        const token = readCredential(input, service);
        const success = await (service === 'memory' ? connectMemory : connectContext)(token);
        status(success ? '凭证已导入并连接。' : '凭证已读取，但服务连接失败，请查看对应服务的提示。', !success);
      } else {
        let document;
        try { document = JSON.parse(input.replace(/^\uFEFF/, '')); } catch { throw Error('连接文件不是有效的 JSON。'); }
        if (document?.workcontext_credentials !== 1) throw Error('这是单项凭证，请使用对应服务下的“导入凭证 JSON”；统一连接请导出 workcontext-connection.json。');
        const config = await fetch('/config').then(r => { if (!r.ok) throw Error('无法读取服务配置，请重试。'); return r.json(); });
        // Parse all included credentials before changing either connection.
        const credentials = Object.fromEntries(['memory', 'context'].filter(name => document[name] && (name === 'memory' ? config.memory || config.local : config.context)).map(name => [name, readCredential(input, name)]));
        if (!Object.keys(credentials).length) throw Error('连接文件没有包含当前页面所需的服务凭证。');
        status('正在导入并连接…');
        const results = await Promise.all(Object.entries(credentials).map(async ([name, token]) => [name, await (name === 'memory' ? connectMemory : connectContext)(token)]));
        const labels = { memory: '真实工作记忆', context: 'P2 合成上下文' };
        status(results.map(([name, ok]) => `${labels[name]}：${ok ? '已连接' : '连接失败，请查看下方提示'}`).join(' · '), results.some(([, ok]) => !ok));
      }
    } catch (error) { status(error.message, true); }
    finally { button.disabled = false; }
  });
}
bindPicker('connection-import', 'connection-file');
bindPicker('memory-import', 'memory-file', 'memory');
bindPicker('context-import', 'context-file', 'context');
