"""Drop-in handler: arb routes first, then the existing parlay API.

Point SAM Handler at parlay_api.lambda_handler when you want /v1/arb/scan
on the same function as /v1/games. Safe no-op for every other path.
"""
from arb_api import handle_arb_request
from api import lambda_handler as core_handler


def lambda_handler(event, context):
    arb = handle_arb_request(event or {})
    if arb is not None:
        return arb
    return core_handler(event, context)
