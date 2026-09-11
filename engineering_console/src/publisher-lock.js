import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

function trustedOwner(stat) {
  if (typeof process.getuid !== 'function') throw new Error('publisher_lock_unix_runtime_required');
  return stat.uid === process.getuid() && (stat.mode & 0o022) === 0;
}

/** Run the trusted publisher in a process that itself holds the kernel lock.
 * The lock file is NEVER unlinked. Kernel release on exit/crash avoids stale-PID
 * leases. The directory must be shared only by publisher identities; the worker
 * must not mount it. Existing directories/files are rejected unless owned by the
 * publisher uid and not group/other writable.
 */
export async function runWithPublisherLock(directory, script, { args = [], env = process.env, stdio = 'inherit' } = {}) {
  return runWithRuntimeLock(directory, script, { args, env, stdio, lockName: 'publisher.lock' });
}

export async function runWithRuntimeLock(directory, script, { args = [], env = process.env, stdio = 'inherit', lockName } = {}) {
  if (!['publisher.lock', 'worker.lock'].includes(lockName)) throw new Error('invalid_runtime_lock_name');
  if (!path.isAbsolute(directory) || !path.isAbsolute(script)) throw new Error('absolute_publisher_paths_required');
  const normalized = path.resolve(directory);
  fs.mkdirSync(normalized, { recursive: true, mode: 0o700 });
  const real = fs.realpathSync(normalized);
  if (real !== normalized) throw new Error('publisher_lock_symlink_rejected');
  const directoryStat = fs.statSync(real);
  if (!directoryStat.isDirectory() || !trustedOwner(directoryStat)) throw new Error('publisher_lock_directory_not_trusted');

  const lockPath = path.join(real, lockName);
  const fd = fs.openSync(lockPath, fs.constants.O_CREAT | fs.constants.O_RDWR | fs.constants.O_NOFOLLOW, 0o600);
  const lockStat = fs.fstatSync(fd);
  if (!lockStat.isFile() || lockStat.nlink !== 1 || !trustedOwner(lockStat)) { fs.closeSync(fd); throw new Error('publisher_lock_file_not_trusted'); }

  const io = stdio === 'ignore' ? ['ignore', 'ignore', 'ignore', fd] : ['inherit', 'inherit', 'inherit', fd];
  let child;
  try {
    child = spawn('flock', ['--nonblock', '--conflict-exit-code', '75', '--no-fork', '/proc/self/fd/3', process.execPath, script, ...args], { env, stdio: io });
  } finally { fs.closeSync(fd); }
  const forward = (signal) => { if (child.exitCode === null) child.kill(signal); };
  const onTerm = () => forward('SIGTERM'); const onInt = () => forward('SIGINT');
  process.on('SIGTERM', onTerm); process.on('SIGINT', onInt);
  try {
    return await new Promise((resolve, reject) => {
      child.once('error', () => reject(new Error('publisher_lock_runtime_unavailable')));
      child.once('exit', (code, signal) => signal ? reject(new Error('publisher_process_interrupted')) : resolve(code));
    });
  } finally { process.off('SIGTERM', onTerm); process.off('SIGINT', onInt); }
}
