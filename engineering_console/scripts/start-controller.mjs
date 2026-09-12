import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { loadConfig } from '../src/config.js';
import { createServer } from '../src/server.js';
import { git } from '../src/git.js';
const exec = promisify(execFile);
const config = loadConfig();
for (const key of ['cluster', 'jobTaskDefinition', 'jobImage', 'jobSecurityGroup', 'brokerUrl', 'transportDir', 'model']) if (!config[key]) throw new Error(`isolated_controller_missing_${key}`);
if (!config.browserAuthEnabled) throw new Error('browser_authentication_required');
for (const directory of [config.dataDir, config.workspaceRoot, config.transportDir, path.dirname(config.repository)]) fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
if (!fs.existsSync(path.join(config.repository, '.git'))) {
  if (fs.existsSync(config.repository)) throw new Error('repository_path_not_checkout');
  const temporary = `${config.repository}.init-${crypto.randomUUID()}`;
  await exec('git', ['clone', '--branch', 'main', '--single-branch', 'https://github.com/KirtKurt/parlay-platform.git', temporary], { timeout: 120000 });
  fs.renameSync(temporary, config.repository);
}
if ((await git(config.repository, ['remote', 'get-url', 'origin'])) !== 'https://github.com/KirtKurt/parlay-platform.git') throw new Error('repository_origin_mismatch');
createServer({ config }).listen(config.port, config.bindAddress);
