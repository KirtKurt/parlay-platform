import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import { isDeepStrictEqual } from 'node:util';

// Invoked by JobStore under flock. Payload travels over stdin, never argv/logs.
const target = process.argv[2];
let temporary;
try {
  const { base, job } = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (!path.isAbsolute(target) || path.basename(target) !== `${job.id}.json`) throw new Error('invalid_target');
  let current = null;
  try { current = JSON.parse(fs.readFileSync(target, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if ((!base && current) || (base && !current)) process.exit(73);
  const merged = current ? { ...current } : { ...job };
  if (base) {
    if (base.id !== job.id || current.id !== job.id) process.exit(73);
    for (const key of new Set([...Object.keys(base), ...Object.keys(job)])) {
      if (key === 'updatedAt' || isDeepStrictEqual(base[key], job[key])) continue;
      // Preserve changes from another writer when fields are disjoint; reject
      // conflicting state transitions instead of overwriting a newer receipt.
      if (!isDeepStrictEqual(current[key], base[key]) && !isDeepStrictEqual(current[key], job[key])) process.exit(73);
      if (Object.hasOwn(job, key)) merged[key] = job[key];
      else delete merged[key];
    }
  }
  merged.updatedAt = new Date().toISOString();
  const content = `${JSON.stringify(merged, null, 2)}\n`;
  temporary = `${target}.${crypto.randomUUID()}.tmp`;
  const fd = fs.openSync(temporary, 'wx', 0o600);
  try { fs.writeFileSync(fd, content); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  fs.renameSync(temporary, target);
  temporary = undefined;
  const directory = fs.openSync(path.dirname(target), 'r');
  try { fs.fsyncSync(directory); } finally { fs.closeSync(directory); }
  process.stdout.write(content);
} catch {
  // Never echo job contents or filesystem errors into service logs.
  process.exitCode = 74;
} finally {
  if (temporary) fs.rmSync(temporary, { force: true });
}
