// Validate before constructing a browser Headers object. Never echo secrets in errors.
export function normalizeToken(input) {
  let token = input.trim();
  if (token.startsWith('{')) {
    let document;
    try { document = JSON.parse(token); }
    catch { throw new Error('Token JSON 格式不完整，请导入原始 JSON 文件或重新复制完整内容。'); }
    if (typeof document.token !== 'string') {
      throw new Error('该 JSON 中没有字符串类型的 token 字段，请使用对应服务的凭证文件。');
    }
    token = document.token.trim();
  }
  token = token.replace(/^Bearer[ \t]+/i, '').trim();
  if (!token) throw new Error('请导入凭证文件或填写服务 Token。');
  if (token.length > 4000 || !/^[A-Za-z0-9._~+\/-]+=*$/.test(token)) {
    throw new Error('凭证格式无效：请导入 JSON 文件或粘贴 token 值。凭证不能包含中文、路径或说明文字。');
  }
  return token;
}

export function readCredential(input, service) {
  let document;
  try { document = JSON.parse(input.replace(/^\uFEFF/, '')); }
  catch { throw Error('连接文件不是有效的 JSON，请重新选择凭证文件。'); }
  const value = document?.workcontext_credentials === 1 ? document[service] : document;
  if (!value || typeof value.token !== 'string') throw Error('文件中没有该服务的凭证，请核对真实记忆与 P2 的用途。');
  if (value.expires_at && (!Number.isFinite(Date.parse(value.expires_at)) || Date.parse(value.expires_at) <= Date.now())) throw Error('凭证已过期，请从管理端重新获取。');
  return normalizeToken(JSON.stringify(value));
}

export async function readCredentialFile(file) {
  if (!file || file.size > 32768) throw Error('请选择不超过 32 KB 的凭证 JSON 文件。');
  return file.text();
}
