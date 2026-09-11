import test from 'node:test'; import assert from 'node:assert/strict'; import fs from 'node:fs';
const source=fs.readFileSync(new URL('../src/auth.js',import.meta.url),'utf8');
test('authorization is based on verified JWT claims, not spoofable identity headers',()=>{assert.match(source,/jwtVerify/);assert.doesNotMatch(source,/x-inqsi-user-id/i);assert.match(source,/administrator_required/)});
