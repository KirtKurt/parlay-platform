import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const exec = promisify(execFile);

export async function aws(service, operation, input) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eng-console-aws-'));
  const file = path.join(root, 'request.json');
  try {
    fs.writeFileSync(file, JSON.stringify(input), { mode: 0o600 });
    const { stdout } = await exec('aws', [service, operation, '--cli-input-json', `file://${file}`, '--output', 'json'], {
      timeout: 120000, maxBuffer: 32 * 1024 * 1024, env: { ...process.env, AWS_PAGER: '' }
    });
    return stdout.trim() ? JSON.parse(stdout) : {};
  } catch { throw new Error(`aws_${service}_${operation}_failed`); }
  finally { fs.rmSync(root, { recursive: true, force: true }); }
}
