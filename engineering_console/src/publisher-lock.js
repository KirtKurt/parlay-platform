import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

/** Run the trusted publisher in a process that itself holds the kernel lock.
 * The lock file is NEVER unlinked. Kernel release on exit/crash avoids stale-PID
 * leases. The directory must be shared by all publishers, and inaccessible to
 * Codex. EFS/NFS lock behavior still requires deployment smoke verification.
 */
export async function runWithPublisherLock(directory, script, { args = [], env = process.env, stdio = 'inherit' } = {}) {
  if (!path.isAbsolute(directory) || !path.isAbsolute(script)) throw new Error('absolute_publisher_paths_required');
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const real = fs.realpathSync(directory);
  if (real !== directory) throw new Error('publisher_lock_symlink_rejected');
  const fd = fs.openSync(path.join(real, 'publisher.lock'), fs.constants.O_CREAT | fs.constants.O_RDWR | fs.constants.O_NOFOLLOW, 0o600);
  if (!fs.fstatSync(fd).isFile()) { fs.closeSync(fd); throw new Error('publisher_lock_not_regular'); }
  const io = stdio === 'ignore' ? ['ignore', 'ignore', 'ignore', fd] : ['inherit', 'inherit', 'inherit', fd];
  let child;
  try {
    // --no-fork execs Node with fd 3 and the lock retained for the entire run.
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
