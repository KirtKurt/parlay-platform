import json
from pathlib import Path
import subprocess
import pytest
import publish_mlb_audit_evidence as publisher


def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,text=True).stdout.strip()


def bundle(root,day):
    stamp=f'2026-09-{day:02}T14:00:00+00:00'
    for name in publisher.FILES:
        p=root/name;p.parent.mkdir(exist_ok=True,parents=True)
        p.write_text(json.dumps({'createdAtUtc':stamp,'auditCreatedAtUtc':stamp,'ok':False}))


def setup(tmp_path):
    remote=tmp_path/'remote.git';git(tmp_path,'init','--bare',str(remote))
    root=tmp_path/'repo';git(tmp_path,'clone',str(remote),str(root))
    git(root,'config','user.name','Test');git(root,'config','user.email','test@example.com')
    git(root,'checkout','-b','main');bundle(root,1);git(root,'add','.');git(root,'commit','-m','baseline');git(root,'push','origin','main')
    return root,remote


def test_publishes_failed_but_fresh_evidence_without_changing_source_tree(tmp_path):
    root,remote=setup(tmp_path);bundle(root,9)
    head=git(root,'rev-parse','HEAD');result=publisher.publish(root)
    assert result['published'] is True
    assert git(root,'rev-parse','HEAD')==head
    assert json.loads(git(remote,'show','main:'+publisher.EXECUTION))['ok'] is False
    assert not (root/'runtime_reports').is_symlink()


def test_newer_published_evidence_wins(tmp_path):
    root,remote=setup(tmp_path);bundle(root,9);git(root,'add','.');git(root,'commit','-m','new audit');git(root,'push','origin','main')
    bundle(root,8);head=git(remote,'rev-parse','main')
    assert publisher.publish(root)['published'] is False
    assert git(remote,'rev-parse','main')==head


def test_concurrent_unrelated_main_update_is_preserved(tmp_path,monkeypatch):
    root,remote=setup(tmp_path);other=tmp_path/'other';git(tmp_path,'clone','--branch','main',str(remote),str(other))
    git(other,'config','user.name','Test');git(other,'config','user.email','test@example.com')
    bundle(root,9);original=publisher.git; raced=[]
    def run(path,*args,**kwargs):
        if args[:3]==('push','origin','HEAD:main') and not raced:
            raced.append(True);(other/'unrelated.txt').write_text('keep me')
            git(other,'add','.');git(other,'commit','-m','concurrent work');git(other,'push','origin','main')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(publisher,'git',run)
    result=publisher.publish(root)
    assert result['attempts']==2
    assert git(remote,'show','main:unrelated.txt')=='keep me'
    assert json.loads(git(remote,'show','main:'+publisher.EXECUTION))['createdAtUtc'].startswith('2026-09-09')


def test_mismatched_bundle_is_rejected_before_publication(tmp_path):
    root,remote=setup(tmp_path);bundle(root,9)
    (root/publisher.FILES[2]).write_text(json.dumps({'auditCreatedAtUtc':'2026-09-08T14:00:00Z'}))
    with pytest.raises(ValueError,match='another audit'):publisher.publish(root)


def test_trainer_diagnostic_uses_same_safe_publication_without_audit_mutation(tmp_path):
    root,remote=setup(tmp_path)
    path='runtime_reports/mlb_trainer_function_error_latest.json'
    (root/path).write_text(json.dumps({'createdAtUtc':'2026-09-09T15:00:00Z','ok':False}))
    audit=git(remote,'show','main:'+publisher.EXECUTION)
    assert publisher.publish_trainer_diagnostic(root)['published'] is True
    assert git(remote,'show','main:'+publisher.EXECUTION)==audit
    assert json.loads(git(remote,'show','main:'+path))['ok'] is False


def test_manual_diagnostic_uses_bounded_invoker_and_safe_publisher():
    root=Path(__file__).resolve().parents[2]
    text=(root/'.github/workflows/mlb-trainer-function-error-diagnostic.yml').read_text()
    assert 'python scripts/invoke_mlb_trainer_with_retry.py' in text
    assert '--retry-execution-lease' in text
    assert 'aws lambda invoke' not in text
    assert 'publish_mlb_audit_evidence.py --trainer-diagnostic' in text
    assert 'group: unified-mlb-learning' in text
