import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { JobStore } from '../src/store.js';

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-console-store-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const store = new JobStore(dir);
  const job = store.create({ instruction: 'write proof', authorizedScope: ['engineering_console_publication_proof'] }, 'admin', 'a'.repeat(40));
  return { dir, store, job };
}

test('independent worker and publisher stores preserve disjoint updates', (t) => {
  const { dir, store, job } = fixture(t);
  const publisher = new JobStore(dir);
  const copy = publisher.get(job.id);
  job.logs.push('worker output');
  store.save(job);
  copy.cancelRequested = true;
  publisher.save(copy);
  assert.deepEqual(store.get(job.id).logs, ['worker output']);
  assert.equal(store.get(job.id).cancelRequested, true);
});

test('a stale writer cannot replace a newer publication state', (t) => {
  const { dir, store, job } = fixture(t);
  const publisher = new JobStore(dir);
  const copy = publisher.get(job.id);
  copy.status = 'completed';
  copy.publicationState = 'merged';
  publisher.save(copy);
  job.status = 'failed';
  assert.throws(() => store.save(job), { code: 'ESTALE' });
  assert.equal(store.get(job.id).publicationState, 'merged');
  assert.equal(store.get(job.id).status, 'completed');
});

test('temporary writes do not use container-local PID names', (t) => {
  const { dir, store, job } = fixture(t);
  const previousTemp = `${store.file(job.id)}.${process.pid}.tmp`;
  fs.writeFileSync(previousTemp, 'another container owns this');
  job.status = 'running';
  store.save(job);
  assert.equal(fs.readFileSync(previousTemp, 'utf8'), 'another container owns this');
  assert.equal(fs.statSync(store.file(job.id)).mode & 0o777, 0o600);
  assert.deepEqual(fs.readdirSync(dir).filter(x => x.endsWith('.tmp')), [path.basename(previousTemp)]);
});

test('concurrent processes cannot both commit conflicting transitions', async (t) => {
  const { dir, store, job } = fixture(t);
  const source = `import {JobStore} from ${JSON.stringify(new URL('../src/store.js', import.meta.url).href)};
    const store = new JobStore(process.argv[1]); const job = store.get(process.argv[2]);
    process.send('ready'); process.once('message', () => {
      job.status = process.argv[3];
      try { store.save(job); process.send('saved'); }
      catch (e) { process.send(e.code); }
      process.disconnect();
    });`;
  const children = ['running', 'cancelled'].map(status => spawn(process.execPath, ['--input-type=module', '-e', source, dir, job.id, status], { stdio: ['ignore', 'ignore', 'inherit', 'ipc'] }));
  t.after(() => children.forEach(child => { if (child.exitCode === null) child.kill(); }));
  await Promise.all(children.map(child => new Promise(resolve => child.once('message', resolve))));
  const outcomes = children.map(child => new Promise((resolve, reject) => { child.once('message', resolve); child.once('error', reject); }));
  children.forEach(child => child.send('save'));
  assert.deepEqual((await Promise.all(outcomes)).sort(), ['ESTALE', 'saved']);
  await Promise.all(children.map(child => child.exitCode === null ? new Promise(resolve => child.once('exit', resolve)) : Promise.resolve()));
  assert.ok(['running', 'cancelled'].includes(store.get(job.id).status));
});
