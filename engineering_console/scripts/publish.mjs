import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runWithPublisherLock } from '../src/publisher-lock.js';
import { loadPublicationPolicy, requirePublisherLockDirectory } from '../src/publication-policy.js';

// Both fresh claims and recovered .processing claims execute under ONE shared
// kernel lock. No lease based on PIDs, timestamps, or untrusted outbox content.
loadPublicationPolicy();
const directory = requirePublisherLockDirectory();
const runner = path.join(path.dirname(fileURLToPath(import.meta.url)), 'publisher-runner.mjs');
const result = await runWithPublisherLock(directory, runner);
process.exitCode = result === 75 ? 0 : result;
