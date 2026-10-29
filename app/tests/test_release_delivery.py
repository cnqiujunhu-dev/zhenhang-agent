"""Portable configuration and default public-surface checks, on disposable files only."""
from pathlib import Path
import os,subprocess,sys
import pytest
from fastapi.testclient import TestClient
from zhenxing.app import create_app
from zhenxing.model_gateway import ModelGateway,RuleIntentModel
from zhenxing.deployment_credentials import initial_customer_password,initial_admin_password

pytestmark=pytest.mark.release_credentials
ROOT=Path(__file__).resolve().parents[1]

def test_configuration_creates_distinct_credentials_and_never_overwrites_existing(tmp_path):
    script=tmp_path/'configure_local.py';script.write_bytes((ROOT/'configure_local.py').read_bytes())
    source=tmp_path/'src/zhenxing';source.mkdir(parents=True);(source/'accounts.py').write_text('SEED_USERNAMES=("wangwei","qiujunhu")\n',encoding='utf-8')
    subprocess.run([sys.executable,str(script)],check=True,capture_output=True)
    before=(tmp_path/'.env').read_bytes();values=dict(line.split('=',1)for line in before.decode().splitlines())
    assert len(values['ZX_INITIAL_CUSTOMER_SUFFIX'])>=12 and len(values['ZX_INITIAL_ADMIN_PASSWORD'])>=12
    assert values['ZX_DATABASE_PASSWORD']!=values['ZX_KNOWLEDGE_PASSWORD']
    login=(tmp_path/'var/private/首次登录.txt').read_text(encoding='utf-8')
    assert 'wangwei'+values['ZX_INITIAL_CUSTOMER_SUFFIX']in login and values['ZX_INITIAL_ADMIN_PASSWORD']in login
    again=subprocess.run([sys.executable,str(script)],capture_output=True)
    assert again.returncode!=0 and (tmp_path/'.env').read_bytes()==before

def test_initial_seed_credentials_require_explicit_configuration(monkeypatch):
    monkeypatch.delenv('ZX_INITIAL_CUSTOMER_SUFFIX',raising=False);monkeypatch.delenv('ZX_INITIAL_ADMIN_PASSWORD',raising=False)
    with pytest.raises(RuntimeError):initial_customer_password('wangwei')
    with pytest.raises(RuntimeError):initial_admin_password()
    monkeypatch.setenv('ZX_INITIAL_CUSTOMER_SUFFIX','IsolatedFixtureSuffix')
    assert initial_customer_password('wangwei')=='wangweiIsolatedFixtureSuffix'

def test_unauthenticated_demo_creation_is_disabled_by_default(tmp_path,monkeypatch):
    monkeypatch.delenv('ZX_ALLOW_DEMO_SESSIONS',raising=False)
    app=create_app('sqlite:///'+(tmp_path/'entry.sqlite').as_posix(),ModelGateway(RuleIntentModel()),bootstrap_accounts=False)
    with TestClient(app)as client:
        response=client.post('/api/v1/demo/sessions',json={})
        assert response.status_code==403 and response.json()['error']['code']=='DEMO_SESSIONS_DISABLED'
        assert client.get('/api/v1/accounts').status_code==401
