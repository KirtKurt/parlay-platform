import test from 'node:test';
import assert from 'node:assert/strict';
import { recoveryPollDecision } from '../scripts/verify-live.mjs';

test('live proof fails immediately before recovery readiness if the controller is unavailable', () => {
  assert.deepEqual(recoveryPollDecision(false, null, 1000, 180000), { retry: false, since: null });
});

test('live proof tolerates only a bounded controller outage after recovery readiness', () => {
  const first = recoveryPollDecision(true, null, 1000, 180000);
  assert.deepEqual(first, { retry: true, since: 1000 });
  assert.deepEqual(recoveryPollDecision(true, first.since, 180999, 180000), { retry: true, since: 1000 });
  assert.deepEqual(recoveryPollDecision(true, first.since, 181001, 180000), { retry: false, since: 1000 });
});
