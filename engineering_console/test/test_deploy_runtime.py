import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy_runtime', Path(__file__).parents[1] / 'scripts' / 'deploy_runtime.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

class DeploymentBoundaryTest(unittest.TestCase):
    def test_deploy_success_prose_is_not_parsed_as_json(self):
        with patch.object(deploy.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='Successfully created/updated stack\n', stderr='')):
            self.assertIn('Successfully', deploy.aws('cloudformation', 'deploy', text_output=True))

    def test_job_network_rejects_general_egress_and_ingress(self):
        safe = {'IpPermissions': [], 'IpPermissionsEgress': [{'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443, 'UserIdGroupPairs': [{'GroupId': 'sg-broker'}]}]}
        deploy.validate_job_egress(safe)
        for bad in [dict(safe, IpPermissions=[{}]), {'IpPermissionsEgress': [{'IpProtocol': '-1'}]}, {'IpPermissionsEgress': [{'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}]}]:
            with self.assertRaises(ValueError): deploy.validate_job_egress(bad)

if __name__ == '__main__': unittest.main()
