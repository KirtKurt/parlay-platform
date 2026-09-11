import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { JobStore } from '../src/store.js';
import { DurableQueue } from '../src/queue.js';

const LIMIT = 16 * 1024 * 1024;
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-stream-safety-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const store = new JobStore(dir);
  const job = store.create({ instruction: 'Harmless proof.', authorizedScope: ['engineering_console_publication_proof'] }, 'owner', 'a'.repeat(40));
  return { dir, store, job };
}

test('two valid disjoint updates cannot commit an oversized merged record', async t => {
  const { dir, store, job } = fixture(t);
  const other = new JobStore(dir), copy = other.get(job.id);
  job.diff = 'x'.repeat(9 * 1024 * 1024);
  await store.saveAsync(job);
  const before = fs.readFileSync(store.file(job.id), 'utf8');
  copy.logs = ['y'.repeat(9 * 1024 * 1024)];
  await assert.rejects(other.saveAsync(copy), { code: 'EFBIG' });
  assert.equal(fs.readFileSync(store.file(job.id), 'utf8'), before);
  assert.ok(Buffer.byteLength(before) <= LIMIT);
});

test('final pretty-printed size is checked before commit', async t => {
  const { store, job } = fixture(t);
  job.logs = Array(2_300_000).fill('x');
  assert.ok(Buffer.byteLength(JSON.stringify(job)) < LIMIT);
  const before = fs.readFileSync(store.file(job.id), 'utf8');
  await assert.rejects(store.saveAsync(job), { code: 'EFBIG' });
  assert.equal(fs.readFileSync(store.file(job.id), 'utf8'), before);
});

test('ordinary concurrent disjoint writes remain successful', async t => {
  const { dir, store, job } = fixture(t);
  const other = new JobStore(dir), copy = other.get(job.id);
  job.logs.push('event'); copy.cancelRequested = true;
  await Promise.all([store.saveAsync(job), other.saveAsync(copy)]);
  const saved = store.get(job.id);
  assert.deepEqual(saved.logs, ['event']); assert.equal(saved.cancelRequested, true);
});

test('conflicting writes retain the one-winner snapshot rule', async t => {
  const { dir, store, job } = fixture(t);
  const other = new JobStore(dir), copy = other.get(job.id);
  job.status = 'running'; copy.status = 'cancelled';
  const results = await Promise.allSettled([store.saveAsync(job), other.saveAsync(copy)]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  assert.equal(results.find(result => result.status === 'rejected').reason.code, 'ESTALE');
});

for (const phase of ['before_rename', 'after_rename']) {
  test(`${phase} fsync failure reports unknown outcome without inventing a failed job`, async t => {
    const { dir, store, job } = fixture(t);
    const before = fs.readFileSync(store.file(job.id), 'utf8');
    const preload = path.join(dir, 'fault.mjs');
    fs.writeFileSync(preload, `import fs from 'node:fs';
      if(process.argv[1]?.endsWith('/scripts/store-write.mjs')) {
        const flush=fs.fsyncSync; fs.fsyncSync=fd=>{
          if(${JSON.stringify(phase)}==='before_rename'||fs.fstatSync(fd).isDirectory()) throw new Error('fixture flush failure');
          return flush(fd);
        };
      }`);
    const previous = process.env.NODE_OPTIONS;
    process.env.NODE_OPTIONS = `--import ${pathToFileURL(preload).href}`;
    try { job.instruction = 'New proof'; await assert.rejects(store.saveAsync(job), { code: 'EWRITEUNKNOWN' }); }
    finally { if(previous===undefined) delete process.env.NODE_OPTIONS; else process.env.NODE_OPTIONS=previous; }
    const current = fs.readFileSync(store.file(job.id), 'utf8');
    if (phase === 'before_rename') assert.equal(current, before);
    else assert.equal(JSON.parse(current).instruction, 'New proof');
    assert.equal(JSON.parse(current).status, 'queued');
  });
}

test('large async write that loses the kernel lock does not crash on EPIPE', {timeout:15_000}, async t => {
  const { dir, store, job } = fixture(t);
  const ready = path.join(dir, 'ready');
  const holderScript = path.join(dir, 'holder.mjs');
  fs.writeFileSync(holderScript, `import fs from 'node:fs';fs.writeFileSync(${JSON.stringify(ready)},'ready');setTimeout(()=>{},7000);`);
  const holder = spawn('flock', ['--exclusive', '--no-fork', `${store.file(job.id)}.lock`, process.execPath, holderScript], {stdio:'ignore'});
  t.after(()=>{if(holder.exitCode===null)holder.kill();});
  const deadline=Date.now()+3000;
  while(!fs.existsSync(ready)){assert.ok(Date.now()<deadline);await delay(10);}
  const script = `import {JobStore} from ${JSON.stringify(new URL('../src/store.js',import.meta.url).href)};
    const store=new JobStore(process.argv[1]);const job=store.get(process.argv[2]);job.diff='x'.repeat(2*1024*1024);
    try { await store.saveAsync(job); console.log('unexpected_saved');process.exitCode=2; }
    catch(e){ console.log(e.code);if(e.code!=='EBUSY')process.exitCode=3; }`;
  const child=spawn(process.execPath,['--input-type=module','-e',script,dir,job.id],{stdio:['ignore','pipe','pipe']});
  let stdout='',stderr='';child.stdout.on('data',c=>stdout+=c);child.stderr.on('data',c=>stderr+=c);
  const status=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',resolve);});
  holder.kill();
  assert.equal(status,0,stderr);assert.equal(stdout.trim(),'EBUSY');assert.doesNotMatch(stderr,/Unhandled 'error'/);
  assert.equal(store.get(job.id).diff,undefined);
});

test('unknown persistence outcome cannot be converted to failed by queue fallback', async t => {
  const { store, job } = fixture(t);
  const queue=new DurableQueue(store,async()=>{throw Object.assign(new Error('fixture unknown write'),{code:'EWRITEUNKNOWN'});});
  const done=new Promise(resolve=>queue.once(job.id,resolve));
  queue.enqueue(job.id);await done;
  assert.equal(store.get(job.id).status,'queued');
});
