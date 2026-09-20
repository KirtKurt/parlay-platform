"""Run explicit-derived KS1 development with proof-bound Statcast read attempts."""
from __future__ import annotations

import ks1.forensic_derived_development as target
import ks1.historical_individual_bullpen_enrichment as individual_bullpen
from ks1.statcast_replay_attempt_proof import ProofBoundS3


def main():
    original_context = target.proof_bound_statcast_context

    def proof_bound_statcast_context(cf, s3, bucket, proof):
        attempts = proof.get("statcast_replay_attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ValueError("individual_bullpen_statcast_replay_attempt_proof_missing")
        original_adapter = individual_bullpen._ProofBoundReads

        class AttemptBoundReads(ProofBoundS3):
            def __init__(self, wrapped_s3, _source_receipts):
                super().__init__(wrapped_s3, attempts)

        individual_bullpen._ProofBoundReads = AttemptBoundReads
        try:
            return original_context(cf, s3, bucket, proof)
        finally:
            individual_bullpen._ProofBoundReads = original_adapter

    target.proof_bound_statcast_context = proof_bound_statcast_context
    try:
        target.main()
    finally:
        target.proof_bound_statcast_context = original_context


if __name__ == "__main__":
    main()
