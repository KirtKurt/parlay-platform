"""Reversible MLB AUTO runtime stop. No source deployment, data writes or deletions."""
from __future__ import annotations
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TARGETS = (
    ('parlay-platform-auto-repair-mlb-auto', 'AutoRepairFunction'),
    ('parlay-platform-mlb-auto-llm', 'MLBAutoLLMFunction'),
)
PRESERVED_HELPER = 'MLBAutoLLMBedrockSmokeFunction'
OUT = Path('sunset-evidence')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                     separators=(',', ':')).encode()).hexdigest()


def validate_rule(rule, targets, expected_arn, owned_rules):
    require(rule.get('Name') in owned_rules, 'RULE_NOT_OWNED_BY_TARGET_STACK')
    require(rule.get('EventBusName', 'default') == 'default', 'NONDEFAULT_RULE')
    require(rule.get('State') in ('ENABLED', 'DISABLED'), 'UNKNOWN_RULE_STATE')
    require(bool(rule.get('ScheduleExpression')), 'NOT_A_SCHEDULED_RULE')
    require(not rule.get('ManagedBy'), 'MANAGED_RULE_NOT_ALLOWED')
    require(len(targets) == 1 and targets[0].get('Arn') == expected_arn,
            'SHARED_OR_UNEXPECTED_RULE_TARGET')


def verify_functions(before, after, target_names):
    require(set(before) == set(after), 'LAMBDA_INVENTORY_CHANGED')
    for name, old in before.items():
        new = after[name]
        require(old['configurationDigest'] == new['configurationDigest'],
                'LAMBDA_CODE_OR_CONFIGURATION_CHANGED')
        if name in target_names:
            require(new['reservedConcurrency'] == 0, 'TARGET_NOT_STOPPED')
        else:
            require(old['reservedConcurrency'] == new['reservedConcurrency'],
                    'OTHER_FUNCTION_CONCURRENCY_CHANGED')


def self_test():
    import unittest
    class SafetyTests(unittest.TestCase):
        def rule(self, targets=None, **changes):
            r = dict(Name='owned', State='ENABLED', ScheduleExpression='rate(5 minutes)')
            r.update(changes)
            validate_rule(r, [{'Arn': 'arn:target'}] if targets is None else targets,
                          'arn:target', {'owned'})
        def test_exact_single_target(self): self.rule()
        def test_shared_target_rejected(self):
            with self.assertRaisesRegex(RuntimeError, 'SHARED'): self.rule([{'Arn':'arn:target'}, {'Arn':'arn:other'}])
        def test_wrong_target_rejected(self):
            with self.assertRaisesRegex(RuntimeError, 'UNEXPECTED'): self.rule([{'Arn':'arn:other'}])
        def test_unowned_rule_rejected(self):
            with self.assertRaisesRegex(RuntimeError, 'NOT_OWNED'): self.rule(Name='other')
        def test_managed_rule_rejected(self):
            with self.assertRaisesRegex(RuntimeError, 'MANAGED'): self.rule(ManagedBy='service')
        def test_not_schedule_rejected(self):
            with self.assertRaisesRegex(RuntimeError, 'SCHEDULED'): self.rule(ScheduleExpression='')
        def baseline(self):
            return {'auto': {'configurationDigest':'a', 'reservedConcurrency':None},
                    'other': {'configurationDigest':'b', 'reservedConcurrency':3}}
        def test_only_auto_concurrency_may_change(self):
            before=self.baseline(); after=self.baseline(); after['auto']['reservedConcurrency']=0
            verify_functions(before, after, {'auto'})
        def test_other_concurrency_rejected(self):
            before=self.baseline(); after=self.baseline(); after['auto']['reservedConcurrency']=0; after['other']['reservedConcurrency']=0
            with self.assertRaisesRegex(RuntimeError, 'OTHER_FUNCTION'): verify_functions(before, after, {'auto'})
        def test_other_code_rejected(self):
            before=self.baseline(); after=self.baseline(); after['auto']['reservedConcurrency']=0; after['other']['configurationDigest']='changed'
            with self.assertRaisesRegex(RuntimeError, 'CODE_OR_CONFIGURATION'): verify_functions(before, after, {'auto'})
        def test_missing_inventory_rejected(self):
            with self.assertRaisesRegex(RuntimeError, 'INVENTORY'): verify_functions(self.baseline(), {}, {'auto'})
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SafetyTests)
    require(unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful(), 'SELF_TEST_FAILED')


def execute():
    import boto3
    from botocore.config import Config
    config = Config(connect_timeout=5, read_timeout=15, retries={'mode':'standard','total_max_attempts':2})
    session = boto3.Session(region_name='us-east-1')
    cf = session.client('cloudformation', config=config)
    lm = session.client('lambda', config=config)
    ev = session.client('events', config=config)
    require(os.environ.get('GITHUB_REPOSITORY') == 'KirtKurt/parlay-platform', 'WRONG_REPOSITORY')
    require(os.environ.get('GITHUB_REF') == 'refs/heads/main', 'NOT_MAIN')
    result = {'startedAtUtc':datetime.now(timezone.utc).isoformat(), 'sourceSha':os.environ['GITHUB_SHA'],
              'scope':'MLB_AUTO_ONLY', 'status':'PREFLIGHT', 'mutations':[], 'dataWrites':False,
              'deletions':False, 'sourceDeployment':False, 'modelPromotion':False}
    OUT.mkdir(exist_ok=True)
    def save():
        (OUT/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    def resources(stack):
        rows=[]
        for page in cf.get_paginator('list_stack_resources').paginate(StackName=stack):
            rows.extend(page['StackResourceSummaries'])
            require(len(rows)<=500, 'STACK_RESOURCE_BOUND')
        return rows
    def function_snapshot():
        rows={}; started=time.monotonic()
        for page in lm.get_paginator('list_functions').paginate():
            for cfg in page.get('Functions', []):
                name=cfg['FunctionName']
                if not name.startswith('parlay-platform-'): continue
                require(name not in rows and len(rows)<1000, 'FUNCTION_INVENTORY_BOUND_OR_DUPLICATE')
                # Do not retain environment values, code download URLs or credentials.
                stable={k:v for k,v in cfg.items() if k not in ('LastModified','RevisionId')}
                rows[name]={'arn':cfg['FunctionArn'], 'codeSha256':cfg['CodeSha256'],
                            'configurationDigest':digest(stable),
                            'reservedConcurrency':lm.get_function_concurrency(FunctionName=name).get('ReservedConcurrentExecutions')}
                require(time.monotonic()-started<360, 'FUNCTION_READ_TIME_BOUND')
        require(bool(rows), 'EMPTY_FUNCTION_INVENTORY')
        return rows
    plans=[]
    try:
        for stack, logical in TARGETS:
            status=cf.describe_stacks(StackName=stack)['Stacks'][0]['StackStatus']
            require(status in ('CREATE_COMPLETE','UPDATE_COMPLETE','UPDATE_ROLLBACK_COMPLETE'), 'STACK_NOT_STABLE')
            res=resources(stack)
            entries=[r for r in res if r['LogicalResourceId']==logical and r['ResourceType']=='AWS::Lambda::Function']
            require(len(entries)==1, 'EXACT_TARGET_NOT_FOUND')
            name=entries[0]['PhysicalResourceId']
            require(name.startswith('parlay-platform-'), 'PHYSICAL_TARGET_PREFIX_MISMATCH')
            cfg=lm.get_function_configuration(FunctionName=name)
            require(cfg['State']=='Active' and cfg['LastUpdateStatus']=='Successful', 'FUNCTION_NOT_STABLE')
            variables=cfg.get('Environment',{}).get('Variables',{})
            if logical=='AutoRepairFunction':
                require(variables.get('SPORT_NAME')=='mlb-auto' and variables.get('TARGET_STACK_NAME')=='parlay-platform-mlb-auto-llm', 'REPAIR_IS_NOT_MLB_AUTO_ONLY')
            else:
                table=[r['PhysicalResourceId'] for r in res if r['LogicalResourceId']=='MLBAutoLLMTable' and r['ResourceType']=='AWS::DynamoDB::Table']
                require(len(table)==1 and variables.get('MLB_AUTO_TABLE')==table[0], 'AUTO_TABLE_IDENTITY_MISMATCH')
                helper=[r['PhysicalResourceId'] for r in res if r['LogicalResourceId']==PRESERVED_HELPER and r['ResourceType']=='AWS::Lambda::Function']
                require(len(helper)==1 and helper[0]!=name, 'PRESERVED_PLANNER_HELPER_NOT_DISTINCT')
                result['preservedPlannerHelper']=helper[0]
                result['retainedAutoTable']=table[0]
            owned={r['PhysicalResourceId'] for r in res if r['ResourceType']=='AWS::Events::Rule'}
            names=[]; token=None; seen=set()
            while True:
                args={'TargetArn':cfg['FunctionArn']}
                if token: args['NextToken']=token
                page=ev.list_rule_names_by_target(**args); names.extend(page.get('RuleNames',[]))
                token=page.get('NextToken')
                require(len(names)<=100, 'RULE_COUNT_BOUND')
                if not token: break
                require(token not in seen, 'RULE_CURSOR_REPEAT'); seen.add(token)
            require(names and len(names)==len(set(names)), 'NO_RULE_OR_DUPLICATE_RULE')
            rules=[]
            for rn in names:
                rule=ev.describe_rule(Name=rn)
                targets=ev.list_targets_by_rule(Rule=rn)
                require(not targets.get('NextToken'), 'UNEXPECTED_TARGET_PAGINATION')
                validate_rule(rule, targets.get('Targets',[]), cfg['FunctionArn'], owned)
                rules.append({'name':rn, 'oldState':rule['State'],
                              'definitionDigest':digest({k:v for k,v in rule.items() if k not in ('ResponseMetadata','State')}),
                              'targetsDigest':digest(targets.get('Targets',[]))})
            plans.append({'stack':stack,'logicalId':logical,'name':name,'arn':cfg['FunctionArn'],
                          'timeout':cfg['Timeout'],'rules':rules})
        require(len({p['name'] for p in plans})==2, 'TARGET_PHYSICAL_ALIAS')
        require(all(0<p['timeout']<=900 for p in plans), 'UNEXPECTED_TIMEOUT')
        before=function_snapshot()
        require(all(p['name'] in before and before[p['name']]['arn']==p['arn'] for p in plans), 'TARGET_INVENTORY_MISMATCH')
        require(result['preservedPlannerHelper'] in before, 'HELPER_INVENTORY_MISSING')
        result.update(status='PREFLIGHT_PASSED', plan=plans, beforeFunctions=before)
        save()
        # First stop the dedicated repair controller, then AUTO itself. Never touch the planner helper.
        for plan in plans:
            lm.put_function_concurrency(FunctionName=plan['name'], ReservedConcurrentExecutions=0)
            require(lm.get_function_concurrency(FunctionName=plan['name']).get('ReservedConcurrentExecutions')==0, 'STOP_READBACK_FAILED')
            result['mutations'].append({'operation':'PutFunctionConcurrency','function':plan['name'],'value':0}); save()
        for plan in plans:
            for rule in plan['rules']:
                ev.disable_rule(Name=rule['name'])
                result['mutations'].append({'operation':'DisableRule','rule':rule['name']}); save()
        result['status']='STOP_APPLIED_DRAINING_EXISTING_INVOCATIONS'; save()
        # Concurrency zero does not justify claiming an already-running invocation has ended.
        time.sleep(max(p['timeout'] for p in plans)+30)
        for plan in plans:
            for old in plan['rules']:
                rule=ev.describe_rule(Name=old['name'])
                if rule['State']!='DISABLED':
                    ev.disable_rule(Name=old['name'])
                    result['mutations'].append({'operation':'DisableRuleAfterDrain','rule':old['name']}); save()
                    rule=ev.describe_rule(Name=old['name'])
                require(rule['State']=='DISABLED', 'RULE_STILL_ENABLED')
                require(digest({k:v for k,v in rule.items() if k not in ('ResponseMetadata','State')})==old['definitionDigest'], 'RULE_DEFINITION_CHANGED')
                target_response=ev.list_targets_by_rule(Rule=old['name'])
                require(not target_response.get('NextToken') and digest(target_response.get('Targets',[]))==old['targetsDigest'], 'RULE_TARGETS_CHANGED')
        after=function_snapshot()
        verify_functions(before,after,{p['name'] for p in plans})
        result.update(status='MLB_AUTO_RUNTIME_STOP_VERIFIED',afterFunctions=after,
                      nonTargetLambdaConfigurationReadbackUnchanged=True,
                      sourceRetirementComplete=False,
                      warning='Runtime suspension is persistent but not protection against a later separately authorized redeployment. Existing records retained; AUTO HTTP handler is also stopped.')
    except Exception as exc:
        result.update(status='FAILED_OR_PARTIAL_STOP', errorType=type(exc).__name__)
        # Avoid copying SDK exception bodies or environment values into public logs/artifacts.
        if isinstance(exc,RuntimeError): result['guardFailure']=str(exc)
        raise SystemExit(1) from None
    finally:
        result['finishedAtUtc']=datetime.now(timezone.utc).isoformat(); save()


if __name__=='__main__':
    if sys.argv[1:]==['--self-test']:
        self_test()
    elif sys.argv[1:]==['--stop-authorized-mlb-auto-only']:
        execute()
    else:
        raise SystemExit('Explicit self-test or authorized stop mode required')
