from pathlib import Path

API = Path('hello_world/api.py')
text = API.read_text()

if 'service": "inqsi-deploy-smoke"' not in text:
    marker = '    if method == "GET" and path in {"/", "/health", "/v1/health"}:\n'
    block = '''    if method == "GET" and path == "/v1/moderation/policy":
        return _resp(200, {
            "ok": True,
            "service": "inqsi-deploy-smoke",
            "route": "/v1/moderation/policy",
            "deploymentSmoke": "read_only",
            "secretExposed": False,
        })
'''
    if marker not in text:
        raise RuntimeError('api.py route marker not found for deploy smoke route')
    API.write_text(text.replace(marker, block + marker, 1))

print('Patched main API deploy smoke route for /v1/moderation/policy.')
