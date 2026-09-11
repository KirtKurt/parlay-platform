import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { runWithRuntimeLock } from '../src/publisher-lock.js';

test('replacement worker cannot recover jobs until the old worker releases its lock', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-console-worker-lock-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const ready = path.join(root, 'ready');
  const release = path.join(root, 'release');
  const marker = path.join(root, 'replacement-started');
  const old = path.join(root, 'old.mjs');
  const next = path.join(root, 'next.mjs');
  fs.writeFileSync(old, `import fs from 'node:fs'; fs.writeFileSync(${JSON.stringify(ready)}, 'ready'); const timer = setInterval(() => { if (fs.existsSync(${JSON.stringify(release)})) { clearInterval(timer); } }, 10); setTimeout(() => process.exit(1), 10000).unref();`);
  fs.writeFileSync(next, `import fs from 'node:fs'; fs.writeFileSync(${JSON.stringify(marker)}, 'started');`);
  const directory = path.join(root, 'locks');
  const running = runWithRuntimeLock(directory, old, { lockName: 'worker.lock', stdio: 'ignore' });
  const deadline = Date.now() + 5000;
  while (!fs.existsSync(ready)) { assert.ok(Date.now() < deadline, 'first worker must acquire lock'); await new Promise(resolve => setTimeout(resolve, 10)); }
  assert.equal(await runWithRuntimeLock(directory, next, { lockName: 'worker.lock', stdio: 'ignore' }), 75);
  assert.equal(fs.existsSync(marker), false);
  fs.writeFileSync(release, 'stop');
  assert.equal(await running, 0);
  assert.equal(await runWithRuntimeLock(directory, next, { lockName: 'worker.lock', stdio: 'ignore' }), 0);
  assert.equal(fs.existsSync(marker), true);
});
