import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runWithRuntimeLock } from '../src/publisher-lock.js';

const directory = process.env.INQSI_ENGINEERING_WORKER_LOCK_DIR;
if (!directory || !path.isAbsolute(directory)) {
  throw new Error('absolute_shared_worker_lock_directory_required');
}
// Hold the shared lock throughout the server's startup recovery and lifetime.
// A replacement exits 75 before opening jobs or serving health. Deployment must
// also stop the entire old task (including coding subprocesses) on server exit.
process.exitCode = await runWithRuntimeLock(directory, fileURLToPath(new URL('../src/server.js', import.meta.url)), { lockName: 'worker.lock' });
