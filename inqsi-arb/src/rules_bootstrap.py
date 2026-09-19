"""Load every reviewed supplemental house-rule registration.

Import this module before reading the canonical registry. Registration modules
are intentionally side-effect only and must not depend on caller import order.
"""

import rules_betmgm  # noqa: F401
import rules_caesars  # noqa: F401
import rules_fanatics  # noqa: F401
import rules_state_packs  # noqa: F401
