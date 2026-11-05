import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeToken, readCredential, readCredentialFile } from '../workcontext/web/token.mjs';

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
test('connection bundles select credentials by purpose and reject expired or missing services',()=>{
  const bundle={workcontext_credentials:1,memory:{token:'memory-secret'},context:{token:'context-secret'}};
  assert.equal(readCredential(JSON.stringify(bundle),'memory'),'memory-secret');
  assert.equal(readCredential(JSON.stringify(bundle),'context'),'context-secret');
  assert.equal(readCredential('\ufeff'+JSON.stringify({token:'original-token'}),'memory'),'original-token');
  delete bundle.context;
  assert.throws(()=>readCredential(JSON.stringify(bundle),'context'),/没有该服务/);
  bundle.memory.expires_at='2000-01-01T00:00:00Z';
  assert.throws(()=>readCredential(JSON.stringify(bundle),'memory'),/过期/);
  assert.throws(()=>readCredential('{broken','memory'),/JSON/);
});
test('large files are rejected before reading their contents',async()=>{
  let read=false;
  await assert.rejects(()=>readCredentialFile({size:32769,text(){read=true;}}),/32 KB/);
  assert.equal(read,false);
});
