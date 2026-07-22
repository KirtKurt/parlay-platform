# Isolated Tennis ML deployment bundle

This branch deploys the reviewed `tennis_predictive_platform_v1` source from the
checksum-pinned ZIP in this directory. The GitHub Actions workflow verifies the
SHA-256 before extraction, runs the full test and isolation suite, validates and
builds the SAM application, inspects the Tennis-only CloudFormation change set,
and deploys `parlay-platform-tennis-ml-prod` without modifying the MLB stack.

The extracted source is the standalone Tennis application; it is not imported by
the MLB runtime.
