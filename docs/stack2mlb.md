# 2stackMLB

Uncorrelated third engines on top of frozen KS1. This package has **no
authority to rewrite** official `p_home`, model hashes, S3 prediction
rows, or the KS1 calibration contract.

## Live attach (shadow only)

```
python -m stack2mlb.shadow \
  --predictions path/to/date=YYYY-MM-DD/predictions.parquet \
  --ledger path/to/graded_ledger.json \
  --output path/to/date=YYYY-MM-DD
```

`stack2mlb.live.attach` reads KS1 `p_home`, `p_home_poisson`, `lambda_*`,
and `market_home_prob`. Elo is rebuilt from already-graded locks only.
`pick_status` lands in `stack2mlb_shadow.json` under
`mlb/ks1/stack2mlb-shadow-v1/`.

Official `mlb/ks1/predictions-v1/` is not written. SCHEMA is not
extended. `promoted` is hard-coded false. `chart()` draws the
reliability diagram and will not flip `promotion_allowed`.

Optional later hook in `ks1.daily` (env-gated, default off):

```
from stack2mlb.shadow import maybe_from_env
maybe_from_env(frame, output)
```

## Isolation

- Package import path: `stack2mlb`
- Does not change `ks1/model_refs.json` or official SCHEMA
- Draft PR only. No production promotion.
