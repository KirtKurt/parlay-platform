# MLB ML Platform

This directory is the MLB-only managed ML/MLOps layer. It is intentionally isolated from tennis, soccer, and NFL resources.

## Authority contract

1. The published MLB probability authority is a trained, calibrated statistical model/ensemble.
2. Amazon Bedrock may extract unstructured evidence, propose bounded experiments, summarize failure clusters, and explain an already-computed probability.
3. Bedrock may not override the statistical champion, alter audit labels, rewrite an immutable pregame feature vector, or promote a model.
4. Promotion is fail-closed and requires out-of-time evidence, calibration, and improvement over the same-time de-vigged market baseline.
5. Every feature row is point-in-time and reproducible from immutable provider evidence.

## AWS services

- Amazon S3: immutable raw/curated/model artifacts.
- SageMaker Feature Store: point-in-time offline feature history plus online lookup.
- SageMaker Training: managed model competition and calibration.
- SageMaker Pipelines: reproducible processing/training/evaluation/registration workflow.
- SageMaker Model Registry: versioned MLB champions/challengers.
- Managed MLflow: experiments, metrics, artifacts, and automatic model registration.
- CloudWatch: pipeline/training/quality telemetry.
- DynamoDB remains the authority for operational locks, predictions, settlements, and leases.
- Bedrock remains the language/reasoning analyst layer only.

## Candidate families

The managed trainer evaluates multiple independent candidates against an untouched chronological audit set. The built-in baseline families are regularized logistic regression, random forest, extra trees, histogram gradient boosting, and gradient boosting. Optional XGBoost/LightGBM/CatBoost candidates are admitted automatically when their packages are available in the training image. The best candidates may be combined in a calibrated probability ensemble only when the ensemble beats the strongest single model on validation log loss.

## Promotion metrics

Promotion requires all of the following:

- complete point-in-time provenance;
- no post-cutoff data in any feature row;
- minimum train/validation/audit row counts;
- lower audit log loss than the same-time market baseline;
- positive bootstrap lower bound for log-loss skill;
- acceptable expected calibration error;
- no degradation against the active champion on the same audit rows;
- immutable model, feature-schema, data-manifest, code, and deployment digests.

Accuracy is reported, but accuracy alone cannot promote a model.

## Deployment

`.github/workflows/deploy-mlb-sagemaker-platform.yml` provisions the MLB-only Feature Store, Model Registry, MLflow tracking server, S3 artifact lake, execution role, and upserts the SageMaker training pipeline. The workflow intentionally does not touch tennis, soccer, or NFL resources.
