import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_setup(tmp_path, script, mode):
    executable = tmp_path / "aws"
    executable.write_text('''#!/usr/bin/env python3
import json, os, sys
args=sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as stream: stream.write(json.dumps(args)+'\\n')
mode=os.environ['TEST_MODE']
if args[:2]==['sts','get-caller-identity']: print('123456789012')
elif args[:2]==['cloudformation','describe-stack-resource']:
    print('api123' if 'ServerlessRestApi' in args else 'api-lambda')
elif args[:2]==['apigateway','get-rest-api']: print('Production API')
elif args[:2]==['budgets','create-budget'] and mode=='budget-denied':
    print('AccessDeniedException budgets:ModifyBudget',file=sys.stderr);sys.exit(1)
elif args[:2]==['cloudwatch','put-metric-alarm'] and mode=='alarm-denied':
    print('AccessDeniedException',file=sys.stderr);sys.exit(1)
elif args[:2]==['wafv2','list-web-acls']: print('arn:expected')
elif args[:2]==['wafv2','get-web-acl-for-resource']:
    if mode=='waf-denied': print('AccessDeniedException',file=sys.stderr);sys.exit(1)
    elif mode=='waf-absent': print('WAFNonexistentItemException',file=sys.stderr);sys.exit(1)
    else: print(json.dumps({'WebACL':{'ARN':'arn:other' if mode=='waf-other' else 'arn:expected'}}))
''')
    executable.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    args = ["parlay-platform-dev", "us-east-1", "100"] if script == "configure_security_alarms.sh" else ["https://api123.execute-api.us-east-1.amazonaws.com/Prod", "us-east-1"]
    result = subprocess.run(["bash", str(ROOT / "scripts" / script), *args], cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ['PATH'],
             "CALL_LOG": str(log), "TEST_MODE": mode}, capture_output=True, text=True)
    return result, [json.loads(line) for line in log.read_text().splitlines()]


def test_rest_api_alarms_use_real_metric_dimension(tmp_path):
    result, calls = run_setup(tmp_path, "configure_security_alarms.sh", "ok")
    assert result.returncode == 0
    alarms = [c for c in calls if c[:2] == ['cloudwatch','put-metric-alarm'] and 'AWS/ApiGateway' in c]
    assert len(alarms) == 3
    assert all('Name=ApiName,Value=Production API' in call for call in alarms)
    assert not any('Name=ApiId,Value=api123' in call for call in alarms)


def test_failed_budget_does_not_report_success(tmp_path):
    result, _ = run_setup(tmp_path, "configure_security_alarms.sh", "budget-denied")
    assert result.returncode == 1
    assert 'AccessDeniedException budgets:ModifyBudget' in result.stdout


def test_failed_alarm_does_not_report_success(tmp_path):
    result, _ = run_setup(tmp_path, "configure_security_alarms.sh", "alarm-denied")
    assert result.returncode == 1


def test_waf_read_denial_never_attempts_association(tmp_path):
    result, calls = run_setup(tmp_path, "configure_api_protection.sh", "waf-denied")
    assert result.returncode != 0
    assert not any(c[:2] == ['wafv2','associate-web-acl'] for c in calls)
    assert any(c[:2] == ['apigateway','update-stage'] for c in calls)


def test_existing_different_waf_is_not_replaced(tmp_path):
    result, calls = run_setup(tmp_path, "configure_api_protection.sh", "waf-other")
    assert result.returncode != 0
    assert not any(c[:2] == ['wafv2','associate-web-acl'] for c in calls)


def test_only_explicit_absence_allows_waf_association(tmp_path):
    result, calls = run_setup(tmp_path, "configure_api_protection.sh", "waf-absent")
    assert result.returncode == 0
    assert any(c[:2] == ['wafv2','associate-web-acl'] for c in calls)
