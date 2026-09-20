"""Run ks1.retrain_recent while freezing exact Statcast replay attempts.

The wrapped training behavior is unchanged. This module only records read-only S3
attempt identities during the historical Statcast restore and binds that inventory
into input_proof.json before any downstream development evaluation consumes it.
"""
from __future__ import annotations

import ks1.retrain_recent as target
from ks1.statcast_replay_attempt_proof import RecordingS3


def main():
    original_load = target.load_training_statcast
    original_evaluate = target.evaluate
    latest = {"attempts": None}

    def load_training_statcast(bundle, s3, bucket, *args, **kwargs):
        recorder = RecordingS3(s3)
        report = original_load(bundle, recorder, bucket, *args, **kwargs)
        latest["attempts"] = recorder.frozen_attempts()
        return report

    def evaluate(frame, body, output, proof, *args, **kwargs):
        attempts = latest.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ValueError("statcast replay attempt proof missing")
        proof["statcast_replay_attempts"] = attempts
        (output / "input_proof.json").write_bytes(target.encode(proof))
        return original_evaluate(frame, body, output, proof, *args, **kwargs)

    target.load_training_statcast = load_training_statcast
    target.evaluate = evaluate
    try:
        target.main()
    finally:
        target.load_training_statcast = original_load
        target.evaluate = original_evaluate


if __name__ == "__main__":
    main()
