import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { JOB_ID } from '../src/publication.js';
import { PROOF_ROOT, PROOF_CHECK, loadPublicationPolicy } from '../src/publication-policy.js';
import { validatePublicationHistory } from '../src/publication-git-guard.js';

const [candidate, base, head, branch] = process.argv.slice(2);
if (!candidate || !/^[0-9a-f]{40}$/.test(base || '') || !/^[0-9a-f]{40}$/.test(head || '') || !branch?.startsWith('inqsi/publish-') || !JOB_ID.test(branch.slice('inqsi/publish-'.length))) throw new Error('invalid_proof_check_arguments');
const cwd = path.resolve(candidate);
const git = (...args) => execFileSync('git', ['-c', 'core.hooksPath=/dev/null', ...args], { cwd, encoding: 'utf8', maxBuffer: 2 * 1024 * 1024, timeout: 30000 });
const policy = loadPublicationPolicy({ INQSI_ENGINEERING_PUBLICATION_POLICY: 'proof-v1', INQSI_ENGINEERING_ALLOWED_SCOPES: PROOF_ROOT, INQSI_ENGINEERING_REQUIRED_CHECKS: PROOF_CHECK });
const parent = git('show', '-s', '--format=%P', head).trim();
if (!/^[0-9a-f]{40}$/.test(parent)) throw new Error('proof_requires_single_parent');
const changedFiles = git('diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', `${base}...${head}`, '--').split('\0').filter(Boolean).sort();
const evidence = await validatePublicationHistory(cwd, { startingRevision: parent, authorizedScope: [PROOF_ROOT], changedFiles }, head, base, policy);
console.log(JSON.stringify({ check: PROOF_CHECK, state: 'passed', head: evidence.head, base: evidence.main, changedFileCount: evidence.files.length }));
