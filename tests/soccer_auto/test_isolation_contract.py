from __future__ import annotations

import ast
import json
import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]


class CloudFormationLoader(yaml.SafeLoader):
    pass


def _construct_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CloudFormationLoader.add_multi_constructor("!", _construct_tag)


class IsolationTests(unittest.TestCase):
    def test_source_never_imports_existing_sport_algorithms(self) -> None:
        forbidden = {"hello_world", "tennis_learning"}
        for path in (ROOT / "soccer_auto").glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = {node.module.split(".")[0]}
                else:
                    continue
                self.assertFalse(roots & forbidden, f"{path.name} crosses a sport boundary")

    def test_template_has_only_soccer_owned_resources(self) -> None:
        text = (ROOT / "soccer-auto-template.yaml").read_text()
        template = yaml.load(text, Loader=CloudFormationLoader)
        self.assertNotIn("ImportValue", text)
        self.assertNotIn("parlay_platform_", text)
        self.assertNotIn("inqis_tennis", text)
        self.assertTrue(all(name.startswith("Soccer") for name in template["Resources"]))
        function_handlers = {
            value["Properties"]["Handler"]
            for value in template["Resources"].values()
            if value.get("Type") == "AWS::Serverless::Function"
        }
        self.assertIn("soccer_auto.collector.inventory_handler", function_handlers)
        self.assertIn("soccer_auto.llm_analyst.llm_analyst_handler", function_handlers)
        self.assertNotIn("ReservedConcurrentExecutions", text)
        for name, resource in template["Resources"].items():
            if resource.get("Type") != "AWS::DynamoDB::Table":
                continue
            properties = resource["Properties"]
            defined = {row["AttributeName"] for row in properties.get("AttributeDefinitions", [])}
            used = {row["AttributeName"] for row in properties.get("KeySchema", [])}
            for index in properties.get("GlobalSecondaryIndexes", []):
                used.update(row["AttributeName"] for row in index.get("KeySchema", []))
            self.assertEqual(defined, used, f"{name} has invalid DynamoDB attribute definitions")

    def test_durable_resources_survive_established_stack_deletion_not_create_rollback(self) -> None:
        template = yaml.load(
            (ROOT / "soccer-auto-template.yaml").read_text(),
            Loader=CloudFormationLoader,
        )
        durable_types = {
            "AWS::DynamoDB::Table",
            "AWS::S3::Bucket",
            "AWS::SecretsManager::Secret",
        }
        durable = {
            name: resource
            for name, resource in template["Resources"].items()
            if resource.get("Type") in durable_types
        }
        self.assertEqual(len(durable), 11)
        for name, resource in durable.items():
            self.assertEqual(
                resource.get("DeletionPolicy"),
                "RetainExceptOnCreate",
                f"{name} can orphan empty state after an initial create rollback",
            )
            self.assertEqual(resource.get("UpdateReplacePolicy"), "Retain")

    def test_deploy_workflow_recovers_only_the_exact_failed_soccer_stack(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy-soccer-auto.yml").read_text()
        self.assertIn('stack_name=parlay-platform-soccer-auto', workflow)
        self.assertIn('stack_status" == "ROLLBACK_COMPLETE', workflow)
        self.assertIn('stack_status" == "ROLLBACK_FAILED', workflow)
        self.assertIn("list-stack-resources", workflow)
        self.assertIn("ResourceStatus==`DELETE_SKIPPED`", workflow)
        self.assertIn("wait stack-delete-complete", workflow)
        self.assertNotIn("delete-table", workflow)
        self.assertNotIn("delete-bucket", workflow)
        self.assertNotIn("delete-secret", workflow)

    def test_deploy_policy_covers_only_soccer_queue_topic_and_dashboard_lifecycle(self) -> None:
        policy = json.loads((ROOT / "soccer_auto/deploy_iam_policy.json").read_text())
        statements = {row["Sid"]: row for row in policy["Statement"]}
        dashboard = statements["SoccerAutoDashboardLifecycleOnly"]
        self.assertEqual(
            set(dashboard["Action"]),
            {
                "cloudwatch:PutDashboard",
                "cloudwatch:GetDashboard",
                "cloudwatch:DeleteDashboards",
            },
        )
        self.assertEqual(
            dashboard["Resource"],
            "arn:aws:cloudwatch::735707987003:dashboard/SoccerAutoDashboard-*",
        )
        serialized = json.dumps(policy)
        self.assertNotIn('"Resource": "*"', serialized)
        self.assertNotIn("parlay_platform_", serialized)
        self.assertNotIn("tennis", serialized.lower())
        self.assertNotIn("mlb", serialized.lower())

    def test_runtime_smoke_prints_lambda_error_payload_before_asserting(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy-soccer-auto.yml").read_text()
        helper = workflow.split("invoke_and_assert_ok() {", 1)[1].split(
            "          # Catalog and events", 1
        )[0]
        self.assertIn("--cli-connect-timeout 10", helper)
        self.assertIn("--cli-read-timeout 360", helper)
        print_at = workflow.index("print(name, response)")
        function_error_at = workflow.index('assert not metadata.get("FunctionError")')
        self.assertLess(print_at, function_error_at)
        self.assertIn('policy == "allow_daily_quota"', helper)
        self.assertIn('response.get("status") == "DEFERRED_QUOTA"', helper)
        self.assertIn('row.get("category") == "DAILY_TOKEN_QUOTA"', helper)
        self.assertIn('row.get("error_code") == "ThrottlingException"', helper)
        self.assertIn('attempted == expected_models', helper)
        self.assertIn('[row.get("model_id") for row in errors] == attempted', helper)
        self.assertNotIn("continue-on-error", helper)

    def test_coverage_first_defaults_keep_shared_key_safety_controls(self) -> None:
        template = yaml.load(
            (ROOT / "soccer-auto-template.yaml").read_text(),
            Loader=CloudFormationLoader,
        )
        self.assertEqual(template["Parameters"]["SharedQuotaReservePercent"]["Default"], 0)
        self.assertEqual(template["Parameters"]["QuotaRaceBufferCredits"]["Default"], 2000)
        self.assertEqual(template["Parameters"]["SoccerOddsRequestsPerSecond"]["Default"], 3)
        self.assertEqual(template["Parameters"]["SoccerOddsRequestsPerSecond"]["MaxValue"], 3)
        self.assertEqual(template["Parameters"]["EnableHistoricalBackfill"]["Default"], "true")
        queue_event = template["Resources"]["SoccerCollectionWorkerFunction"]["Properties"][
            "Events"
        ]["CollectionQueue"]["Properties"]
        self.assertEqual(queue_event["BatchSize"], 1)
        self.assertEqual(queue_event["ScalingConfig"]["MaximumConcurrency"], 6)

        workflow = (ROOT / ".github/workflows/deploy-soccer-auto.yml").read_text()
        self.assertIn("default: '0'", workflow)
        self.assertIn("options: ['0', '20', '40', '60', '80']", workflow)
        self.assertIn("inputs.shared_quota_reserve_percent || '0'", workflow)
        self.assertIn('QuotaRaceBufferCredits="2000"', workflow)
        self.assertIn('SoccerOddsRequestsPerSecond="3"', workflow)
        self.assertIn("safety['soccer_request_limit_per_second'] == 3", workflow)
        self.assertIn("safety['burst_capacity'] == 1", workflow)
        self.assertIn("safety['minimum_spacing_ms'] == 334", workflow)
        self.assertIn("safety['distributed_lease'] is True", workflow)
        self.assertIn("safety['fail_closed'] is True", workflow)
        storage = (ROOT / "soccer_auto/storage.py").read_text()
        self.assertIn('SOCCER_AUTO_SHARED_QUOTA_RESERVE_PERCENT", "0"', storage)
        self.assertIn('SOCCER_AUTO_QUOTA_RACE_BUFFER_CREDITS", "2000"', storage)
        client = (ROOT / "soccer_auto/odds_api.py").read_text()
        self.assertIn('SOCCER_AUTO_ODDS_RPS_CAP", "3"', client)
        self.assertIn('"burst_capacity": 1', client)
        self.assertIn("ConditionExpression=condition", client)
        self.assertIn("self._limiter.acquire(operation=path, attempt=attempt)", client)
        self.assertIn("exc.code == 429", client)
        self.assertIn("_bounded_retry_after(exc.headers)", client)
        self.assertIn("time.sleep(retry_after)", client)
        api = (ROOT / "soccer_auto/api.py").read_text()
        controller = (ROOT / "soccer_auto/autonomous_controller.py").read_text()
        self.assertIn('"provider_429_telemetry"', api)
        self.assertIn('"provider_429_telemetry"', controller)
        self.assertIn("provider_429_status", api)
        self.assertIn("provider_429_status", controller)
        self.assertIn("provider_429_baseline", workflow)
        self.assertIn("distributed_rate_limit_state", workflow)

    def test_verified_soccer_deployment_is_manual_only(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy-soccer-auto.yml").read_text()
        trigger = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertIn("push:", trigger)
        self.assertIn("branches: [main]", trigger)
        self.assertIn("soccer_auto/.deploy-repair-once", trigger)
        self.assertNotIn("soccer_auto/**", trigger)
        self.assertTrue((ROOT / "soccer_auto/.deploy-repair-once").exists())

    def test_historical_backfill_defaults_on_and_remains_observable_with_kill_switch(self) -> None:
        template = yaml.load(
            (ROOT / "soccer-auto-template.yaml").read_text(),
            Loader=CloudFormationLoader,
        )
        resource = template["Resources"]["SoccerHistoricalFunction"]
        self.assertNotIn("Condition", resource)
        events = resource["Properties"]["Events"]
        self.assertEqual(
            set(events),
            {
                "FeaturedHistoricalHourly",
                "AdditionalHistoricalHourly",
                "MaterializeHistoricalT45Hourly",
            },
        )
        for event in events.values():
            self.assertEqual(event["Properties"]["Enabled"], ["HistoricalBackfillEnabled", True, False])
        self.assertEqual(
            events["MaterializeHistoricalT45Hourly"]["Properties"]["Schedule"],
            "cron(27 * * * ? *)",
        )
        self.assertEqual(
            events["MaterializeHistoricalT45Hourly"]["Properties"]["Input"],
            '{"mode":"materialize","max_events":5}',
        )
        self.assertEqual(
            template["Globals"]["Function"]["Environment"]["Variables"][
                "SOCCER_AUTO_HISTORICAL_BACKFILL_ENABLED"
            ],
            "EnableHistoricalBackfill",
        )
        workflow = (ROOT / ".github/workflows/deploy-soccer-auto.yml").read_text()
        self.assertNotIn("enable_historical_backfill:", workflow)
        self.assertIn('EnableHistoricalBackfill="true"', workflow)
        self.assertIn("historical_rules_enabled", workflow)
        self.assertIn("assert len(names) == 3", workflow)
        self.assertIn("historical_featured_smoke", workflow)
        self.assertIn("historical_t45_materialization_smoke", workflow)
        self.assertIn("historical_training_rows_after_materialization", workflow)

    def test_lambda_memory_fits_the_production_account_ceiling(self) -> None:
        template = yaml.load(
            (ROOT / "soccer-auto-template.yaml").read_text(),
            Loader=CloudFormationLoader,
        )
        functions = {
            name: resource
            for name, resource in template["Resources"].items()
            if resource.get("Type") == "AWS::Serverless::Function"
        }
        self.assertTrue(functions)
        for name, resource in functions.items():
            memory = int(resource["Properties"].get("MemorySize", 128))
            self.assertLessEqual(memory, 3008, f"{name} exceeds the production Lambda ceiling")
        self.assertEqual(
            functions["SoccerTrainerFunction"]["Properties"]["MemorySize"],
            3008,
        )

    def test_future_soccer_only_paths_cannot_trigger_main_deploy(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
        for path in (
            '"soccer_auto/**"',
            '"soccer-auto-template.yaml"',
            '"tests/soccer_auto/**"',
            '"docs/SOCCER_AUTO.md"',
            '".github/workflows/deploy-soccer-auto.yml"',
        ):
            self.assertIn(path, workflow)
        for path in (
            '".github/workflows/deploy.yml"',
            '".github/workflows/mlb-remove-bbd-active-runtime-once.yml"',
            '".github/workflows/v7-v10-stall-fix-migration.yml"',
        ):
            self.assertNotIn(path, workflow.split("workflow_dispatch:", 1)[0])
        self.assertIn("initial [skip ci] merge", workflow)

    def test_initial_release_documents_trigger_level_suppression(self) -> None:
        documentation = (ROOT / "docs/SOCCER_AUTO.md").read_text()
        self.assertIn("`[skip ci]`", documentation)
        self.assertIn("`workflow_run`", documentation)
        self.assertIn("single merge commit", documentation)

    def test_write_capable_mlb_pr_jobs_ignore_soccer_branch(self) -> None:
        guarded = {
            "mlb-remove-bbd-active-runtime-once.yml":
                "agent/mlb-remove-bbd-and-fix-blockers-20260802",
            "v7-v10-stall-fix-migration.yml": "agent/fix-v7-v10-stalls-20260804",
        }
        for name, authorized_branch in guarded.items():
            source = (ROOT / ".github/workflows" / name).read_text()
            self.assertIn("github.event_name != 'pull_request'", source)
            self.assertIn(f"github.head_ref == '{authorized_branch}'", source)

    def test_llm_has_no_prediction_or_promotion_write_authority(self) -> None:
        source = (ROOT / "soccer_auto/llm_analyst.py").read_text()
        template = yaml.load(
            (ROOT / "soccer-auto-template.yaml").read_text(),
            Loader=CloudFormationLoader,
        )
        self.assertNotIn("put_prediction(", source)
        self.assertNotIn("promote_candidate(", source)
        self.assertNotIn("put_settlement(", source)
        llm_function = template["Resources"]["SoccerLlmAnalystFunction"]
        self.assertEqual(llm_function["Properties"]["Role"], "SoccerLlmAnalystRole.Arn")
        runtime_role = template["Resources"]["SoccerAutoRuntimeRole"]
        self.assertNotIn("bedrock:InvokeModel", str(runtime_role))
        analyst_role = template["Resources"]["SoccerLlmAnalystRole"]
        analyst_policy = str(analyst_role)
        for table in ("SoccerPredictionsTable", "SoccerSettlementsTable", "SoccerLocksTable"):
            self.assertNotIn(table, analyst_policy)
        self.assertIn("dynamodb:LeadingKeys", analyst_policy)
        self.assertIn("LLM_ANALYSIS", analyst_policy)
        api_function = template["Resources"]["SoccerApiFunction"]
        self.assertEqual(api_function["Properties"]["Role"], "SoccerReadApiRole.Arn")
        api_policy = str(template["Resources"]["SoccerReadApiRole"])
        for table in ("SoccerLocksTable", "SoccerSettlementsTable"):
            self.assertIn(table, api_policy)
        for action in ("dynamodb:PutItem", "sqs:SendMessage", "secretsmanager:GetSecretValue"):
            self.assertNotIn(action, api_policy)

    def test_nova_2_uses_cris_profile_and_narrow_underlying_model_authority(self) -> None:
        template = yaml.load(
            (ROOT / "soccer-auto-template.yaml").read_text(),
            Loader=CloudFormationLoader,
        )
        parameter = template["Parameters"]["SoccerLlmModelId"]
        self.assertEqual(parameter["Default"], "us.amazon.nova-2-lite-v1:0")
        self.assertEqual(parameter["AllowedValues"], ["us.amazon.nova-2-lite-v1:0"])
        policy = str(template["Resources"]["SoccerLlmAnalystRole"])
        self.assertIn("inference-profile/${SoccerLlmModelId}", policy)
        self.assertIn(
            "foundation-model/amazon.nova-2-lite-v1:0",
            policy,
        )
        self.assertIn("bedrock:InferenceProfileArn", policy)
        self.assertNotIn("bedrock:*::foundation-model", policy)
        self.assertNotIn("foundation-model/${SoccerLlmModelId}", policy)
        workflow = (ROOT / ".github/workflows/deploy-soccer-auto.yml").read_text()
        self.assertIn("SoccerLlmModelId=\"$soccer_llm_model_id\"", workflow)
        self.assertIn("bedrock_cris_smoke", workflow)
        self.assertIn("us-east-1|us-east-2|us-west-2", workflow)

    def test_llm_fallback_chain_has_exact_profile_and_foundation_model_authority(self) -> None:
        template = yaml.load(
            (ROOT / "soccer-auto-template.yaml").read_text(),
            Loader=CloudFormationLoader,
        )
        globals_environment = template["Globals"]["Function"]["Environment"]["Variables"]
        self.assertEqual(
            globals_environment["SOCCER_AUTO_LLM_FALLBACK_MODEL_IDS"],
            "mistral.ministral-3-14b-instruct,"
            "us.meta.llama4-scout-17b-instruct-v1:0,"
            "us.meta.llama4-maverick-17b-instruct-v1:0,"
            "global.amazon.nova-2-lite-v1:0,us.amazon.nova-pro-v1:0,"
            "us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0",
        )

        statements = {
            statement.get("Sid"): statement
            for statement in template["Resources"]["SoccerLlmAnalystRole"]["Properties"]
            ["Policies"][0]["PolicyDocument"]["Statement"]
            if statement.get("Sid")
        }
        profile_arns = statements["InvokeOnlySoccerAnalystProfiles"]["Resource"]
        self.assertEqual(
            profile_arns,
            [
                "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/${SoccerLlmModelId}",
                "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/us.meta.llama4-scout-17b-instruct-v1:0",
                "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/us.meta.llama4-maverick-17b-instruct-v1:0",
                "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/us.amazon.nova-pro-v1:0",
                "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/us.amazon.nova-lite-v1:0",
                "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/us.amazon.nova-micro-v1:0",
            ],
        )

        expected = {
            "InvokeOnlyNovaTwoLiteThroughSoccerProfile": (
                "${SoccerLlmModelId}",
                "amazon.nova-2-lite-v1:0",
                ("us-east-1", "us-east-2", "us-west-2"),
            ),
            "InvokeOnlyNovaLiteThroughSoccerFallbackProfile": (
                "us.amazon.nova-lite-v1:0",
                "amazon.nova-lite-v1:0",
                ("us-east-1", "us-east-2", "us-west-2"),
            ),
            "InvokeOnlyNovaProThroughSoccerFallbackProfile": (
                "us.amazon.nova-pro-v1:0",
                "amazon.nova-pro-v1:0",
                ("us-east-1", "us-east-2", "us-west-2"),
            ),
            "InvokeOnlyNovaMicroThroughSoccerFallbackProfile": (
                "us.amazon.nova-micro-v1:0",
                "amazon.nova-micro-v1:0",
                ("us-east-1", "us-east-2", "us-west-2"),
            ),
            "InvokeOnlyLlamaScoutThroughSoccerFallbackProfile": (
                "us.meta.llama4-scout-17b-instruct-v1:0",
                "meta.llama4-scout-17b-instruct-v1:0",
                ("us-east-1", "us-east-2", "us-west-2"),
            ),
            "InvokeOnlyLlamaMaverickThroughSoccerFallbackProfile": (
                "us.meta.llama4-maverick-17b-instruct-v1:0",
                "meta.llama4-maverick-17b-instruct-v1:0",
                ("us-east-1", "us-east-2", "us-west-2"),
            ),
        }
        for sid, (profile_id, foundation_model_id, regions) in expected.items():
            statement = statements[sid]
            self.assertEqual(statement["Action"], "bedrock:InvokeModel")
            self.assertEqual(
                statement["Resource"],
                [
                    f"arn:${{AWS::Partition}}:bedrock:{region}::foundation-model/{foundation_model_id}"
                    for region in regions
                ],
            )
            self.assertEqual(
                statement["Condition"]["StringEquals"]["bedrock:InferenceProfileArn"],
                "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:"
                f"inference-profile/{profile_id}",
            )

        self.assertEqual(
            statements["InvokeOnlyMinistralFoundationModel"],
            {
                "Sid": "InvokeOnlyMinistralFoundationModel",
                "Effect": "Allow",
                "Action": "bedrock:InvokeModel",
                "Resource": "arn:${AWS::Partition}:bedrock:${AWS::Region}::"
                "foundation-model/mistral.ministral-3-14b-instruct",
            },
        )

        global_profile = (
            "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:"
            "inference-profile/global.amazon.nova-2-lite-v1:0"
        )
        global_statements = {
            "InvokeOnlyGlobalNovaTwoLiteProfileInSourceRegion": {
                "Action": "bedrock:InvokeModel",
                "Resource": global_profile,
                "Condition": {
                    "StringEquals": {"aws:RequestedRegion": "AWS::Region"}
                },
            },
            "InvokeOnlyGlobalNovaTwoLiteSourceModel": {
                "Action": "bedrock:InvokeModel",
                "Resource": "arn:${AWS::Partition}:bedrock:${AWS::Region}::"
                "foundation-model/amazon.nova-2-lite-v1:0",
                "Condition": {
                    "StringEquals": {
                        "aws:RequestedRegion": "AWS::Region",
                        "bedrock:InferenceProfileArn": global_profile,
                    }
                },
            },
            "InvokeOnlyGlobalNovaTwoLiteGlobalModel": {
                "Action": "bedrock:InvokeModel",
                "Resource": "arn:${AWS::Partition}:bedrock:::foundation-model/"
                "amazon.nova-2-lite-v1:0",
                "Condition": {
                    "StringEquals": {
                        "aws:RequestedRegion": "unspecified",
                        "bedrock:InferenceProfileArn": global_profile,
                    }
                },
            },
        }
        for sid, expected_statement in global_statements.items():
            statement = statements[sid]
            self.assertEqual(statement["Action"], expected_statement["Action"])
            self.assertEqual(statement["Resource"], expected_statement["Resource"])
            self.assertEqual(statement["Condition"], expected_statement["Condition"])

        policy = str(template["Resources"]["SoccerLlmAnalystRole"])
        self.assertNotIn("foundation-model/*", policy)
        self.assertNotIn("inference-profile/*", policy)

    def test_llm_fallback_recovery_runs_hourly(self) -> None:
        template = yaml.load(
            (ROOT / "soccer-auto-template.yaml").read_text(),
            Loader=CloudFormationLoader,
        )
        resources = template["Resources"]
        events = resources["SoccerLlmAnalystFunction"]["Properties"]["Events"]
        self.assertEqual(set(events), {"AnalyzeSoccerLearningHourly"})
        self.assertEqual(
            events["AnalyzeSoccerLearningHourly"]["Properties"]["Schedule"],
            "cron(22 * * * ? *)",
        )
        alarm = resources["SoccerLlmAnalystLivenessAlarm"]["Properties"]
        self.assertEqual(alarm["Period"], 3600)
        self.assertEqual(alarm["EvaluationPeriods"], 2)
        self.assertEqual(alarm["DatapointsToAlarm"], 2)
        self.assertIn("two hourly schedule periods", alarm["AlarmDescription"])

        workflow = (ROOT / ".github/workflows/deploy-soccer-auto.yml").read_text()
        self.assertIn("response['status'] == 'ANALYZED'", workflow)
        self.assertIn('"force_refresh":true', workflow)
        self.assertIn("allow_daily_quota", workflow)
        self.assertIn("BEDROCK_ALL_FALLBACK_MODELS_UNAVAILABLE", workflow)
        self.assertIn("controller_validated_bedrock_quota_deferral", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertIn("response['analysis_origin'] == 'BEDROCK_CONVERSE'", workflow)
        self.assertIn("bedrock_cris_smoke", workflow)

    def test_controller_observes_every_scheduled_component_fail_closed(self) -> None:
        template = yaml.load(
            (ROOT / "soccer-auto-template.yaml").read_text(),
            Loader=CloudFormationLoader,
        )
        resources = template["Resources"]
        controller = resources["SoccerControllerFunction"]
        variables = controller["Properties"]["Environment"]["Variables"]
        expected_functions = {
            "SOCCER_AUTO_INVENTORY_FUNCTION": "SoccerInventoryFunction",
            "SOCCER_AUTO_DISPATCH_FUNCTION": "SoccerDispatchFunction",
            "SOCCER_AUTO_FREEZE_FUNCTION": "SoccerFreezeFunction",
            "SOCCER_AUTO_SETTLEMENT_FUNCTION": "SoccerSettlementFunction",
            "SOCCER_AUTO_TRAINER_FUNCTION": "SoccerTrainerFunction",
            "SOCCER_AUTO_LLM_ANALYST_FUNCTION": "SoccerLlmAnalystFunction",
            "SOCCER_AUTO_HISTORICAL_FUNCTION": "SoccerHistoricalFunction",
            "SOCCER_AUTO_HISTORICAL_BACKFILL_ENABLED": "EnableHistoricalBackfill",
        }
        self.assertEqual(variables, expected_functions)
        self.assertIn(
            "cloudwatch:GetMetricStatistics",
            str(resources["SoccerAutoRuntimeRole"]),
        )
        self.assertIn("dynamodb:Scan", str(resources["SoccerLlmAnalystRole"]))

        for component, function_id in (
            ("Inventory", "SoccerInventoryFunction"),
            ("Dispatch", "SoccerDispatchFunction"),
            ("Freeze", "SoccerFreezeFunction"),
            ("Settlement", "SoccerSettlementFunction"),
            ("Trainer", "SoccerTrainerFunction"),
            ("LlmAnalyst", "SoccerLlmAnalystFunction"),
            ("Historical", "SoccerHistoricalFunction"),
        ):
            alarm = resources[f"Soccer{component}LivenessAlarm"]
            self.assertEqual(alarm["Type"], "AWS::CloudWatch::Alarm")
            properties = alarm["Properties"]
            self.assertEqual(properties["MetricName"], "Invocations")
            self.assertEqual(properties["TreatMissingData"], "breaching")
            self.assertEqual(properties["ComparisonOperator"], "LessThanThreshold")
            self.assertEqual(properties["Dimensions"][0]["Value"], function_id)
            self.assertEqual(properties["AlarmActions"], ["SoccerAutoAlarmTopic"])
            error_alarm = resources[f"Soccer{component}ErrorAlarm"]
            error_properties = error_alarm["Properties"]
            self.assertEqual(error_properties["MetricName"], "Errors")
            self.assertEqual(error_properties["TreatMissingData"], "notBreaching")
            self.assertEqual(error_properties["ComparisonOperator"], "GreaterThanThreshold")
            self.assertEqual(error_properties["Dimensions"][0]["Value"], function_id)
            self.assertEqual(error_properties["AlarmActions"], ["SoccerAutoAlarmTopic"])


if __name__ == "__main__":
    unittest.main()
