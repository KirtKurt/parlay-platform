import { fileURLToPath } from 'node:url';
import { runWithRuntimeLock } from '../src/publisher-lock.js';
import { createJobBroker } from '../src/job-broker.js';
if (process.argv.includes('--locked')) {
  createJobBroker({ root: process.env.INQSI_ENGINEERING_TRANSPORT_DIR, openAIKey: process.env.OPENAI_API_KEY, model: process.env.INQSI_ENGINEERING_MODEL }).listen(8790, '0.0.0.0');
} else {
  process.exitCode = await runWithRuntimeLock(process.env.INQSI_ENGINEERING_BROKER_LOCK_DIR, fileURLToPath(import.meta.url), { lockName: 'broker.lock', args: ['--locked'] });
}
