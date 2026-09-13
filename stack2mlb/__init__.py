"""2stackMLB — uncorrelated third engines on top of frozen KS1.

KS1 LightGBM p_home and KS1 dual-Poisson lambdas stay read-only.
This package never writes official probabilities, model hashes, or
S3 prediction rows. It only votes, shrinks, and gates.
"""

VERSION = "2stackMLB-v1"
SYSTEM = "2stackMLB"
DISAGREE_PP = 0.08
MAX_BAYES_MOVE = 0.08
HOME_FIELD_ELO = 30.0
ELO_SCALE = 400.0
ELO_K_TEAM = 6.0
ELO_K_PITCHER = 8.0
STARTER_WEIGHT = 0.35
