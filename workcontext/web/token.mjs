// Validate before constructing a browser Headers object. Never echo secrets in errors.
export function normalizeToken(input) {
  let token = input.trim();
  if (token.startsWith('{')) {
    let document;
    try { document = JSON.parse(token); }
    catch { throw new Error('Token JSON 格式不完整，请重新复制 consumer-token.json 的完整内容。'); }
    if (typeof document.token !== 'string') {
      throw new Error('该 JSON 中没有字符串类型的 token 字段，请使用 consumer-token.json。');
    }
    token = document.token.trim();
  }
  token = token.replace(/^Bearer[ \t]+/i, '').trim();
  if (!token) throw new Error('请填写 P2 Reader Token。');
  if (token.length > 4000 || !/^[A-Za-z0-9._~+\/-]+=*$/.test(token)) {
    throw new Error('凭证格式无效：请粘贴 token 值或 consumer-token.json 的完整内容。凭证不能包含中文、路径或说明文字。');
  }
  return token;
}
