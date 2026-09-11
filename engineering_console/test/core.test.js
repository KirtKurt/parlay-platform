import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { JobStore } from '../src/store.js';
import { DurableQueue } from '../src/queue.js';
import { sanitize } from '../src/sanitize.js';
import { loadConfig } from '../src/config.js';

test('fails closed without identity configuration', () => assert.throws(() => loadConfig({}), /console disabled/));
test('durably stores jobs and scopes history to its owner', () => { const dir=fs.mkdtempSync(path.join(os.tmpdir(),'inqsi-store-')); const store=new JobStore(dir); const job=store.create({instruction:'change docs',authorizedScope:['docs']},'admin-a','abc'); assert.equal(new JobStore(dir).get(job.id).status,'queued'); assert.equal(store.list('admin-a').length,1); assert.equal(store.list('admin-b').length,0); });
test('cancellation reaches a running worker abort signal', async () => { const dir=fs.mkdtempSync(path.join(os.tmpdir(),'inqsi-queue-')); const store=new JobStore(dir); const job=store.create({instruction:'wait',authorizedScope:['docs']},'admin','abc'); let aborted=false; const queue=new DurableQueue(store,async(j,signal)=>{j.status='running';store.save(j);await new Promise(resolve=>signal.addEventListener('abort',()=>{aborted=true;resolve()}));j.status='cancelled';store.save(j)}); queue.enqueue(job.id); await new Promise(r=>setTimeout(r,10)); queue.cancel(job.id); await new Promise(r=>setTimeout(r,10)); assert.equal(aborted,true); assert.equal(store.get(job.id).status,'cancelled'); });
test('redacts credentials from logs and artifacts', () => { const value=sanitize('Authorization: Bearer-secret sk-example123456789 token=abc123'); assert.equal(value.includes('sk-example'),false); assert.equal(value.includes('abc123'),false); });
