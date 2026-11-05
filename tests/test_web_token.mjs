import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeToken } from '../workcontext/web/token.mjs';

test('normalizes raw, bearer and copied JSON credentials for browser Headers', () => {
  const expected = 'SYNTHETIC_token-0123';
  for (const input of [expected, `\ufeff ${expected}\n`, `Bearer ${expected}`, `bearer ${expected}`, JSON.stringify({token:expected}), JSON.stringify({说明:'只读凭证',token:expected})]) {
    const token=normalizeToken(input);
    assert.equal(token,expected);
    assert.doesNotThrow(()=>new Headers({Authorization:'Bearer '+token}));
  }
});
test('rejects non-ASCII, controls, paths and invalid JSON with useful secret-free errors', () => {
  const inputs=['你的token','abc\u200bsecret','abc\r\nX-Injected: yes','/Users/用户/consumer-token.json','{"token":', '{"token":42}', '{"token":"中文"}','{"url":"https://example.invalid"}'];
  for (const input of inputs) {
    assert.throws(()=>normalizeToken(input),error=>error instanceof Error && /凭证|JSON|token/.test(error.message) && !error.message.includes('secret'));
  }
});
test('rejects empty and oversized tokens before fetch',()=>{
  for(const input of ['', ' \n\t', 'a'.repeat(4001)])assert.throws(()=>normalizeToken(input));
});
