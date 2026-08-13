"""Minimal import stubs for local stdlib-only test environments."""
from __future__ import annotations

import importlib.util
import sys
import types


def install_if_needed() -> None:
    if "boto3" in sys.modules:
        return
    if importlib.util.find_spec("boto3") is not None:
        return

    class Condition:
        def eq(self, value):
            return self

        def between(self, start, end):
            return self

        def begins_with(self, value):
            return self

        def __and__(self, other):
            return self

    class Key(Condition):
        def __init__(self, name):
            self.name = name

    class Attr(Key):
        pass

    class ClientError(Exception):
        def __init__(self, error_response=None, operation_name=""):
            super().__init__(str(error_response or {}))
            self.response = error_response or {"Error": {}}

    class TypeSerializer:
        def serialize(self, value):
            return {"S": str(value)}

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    boto3 = types.ModuleType("boto3")
    boto3.client = lambda *args, **kwargs: None
    boto3.resource = lambda *args, **kwargs: None
    dynamodb = types.ModuleType("boto3.dynamodb")
    conditions = types.ModuleType("boto3.dynamodb.conditions")
    conditions.Key = Key
    conditions.Attr = Attr
    types_module = types.ModuleType("boto3.dynamodb.types")
    types_module.TypeSerializer = TypeSerializer
    botocore = types.ModuleType("botocore")
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = ClientError
    config = types.ModuleType("botocore.config")
    config.Config = Config
    sys.modules.update(
        {
            "boto3": boto3,
            "boto3.dynamodb": dynamodb,
            "boto3.dynamodb.conditions": conditions,
            "boto3.dynamodb.types": types_module,
            "botocore": botocore,
            "botocore.exceptions": exceptions,
            "botocore.config": config,
        }
    )
