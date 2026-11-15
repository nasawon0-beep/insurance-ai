"""Exercise the extracted workflow publisher command with offline GH transport."""
import importlib.util
import json
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def workflow_command():
    source = (ROOT/'.github/workflows/release.yml').read_text(encoding='utf-8')
    step = source.split('- name: Create new draft GitHub Release only',1)[1]
    command = step.split('run: |',1)[1].strip()
    assert command == 'python scripts/create_release_only.py --version "$RELEASE_VERSION"'
    return shlex.split(command.replace('$RELEASE_VERSION','2.5.18'))


@pytest.mark.parametrize('case', ['public','wrong-target','draft','draft-page2','tag-only','401','403','500','network','timeout','malformed','ambiguous404','tag-401','tag-403','tag-500','tag-network','tag-malformed','inventory403','inventory-malformed','explicit404','tag-race','release-race','asset-race'])
def test_extracted_workflow_create_only(tmp_path, monkeypatch, case):
    command = workflow_command()
    package=tmp_path/'desktop/package.json';package.parent.mkdir();package.write_text('{"version":"2.5.18"}')
    asset=tmp_path/'desktop/src-tauri/target/release/bundle/nsis/synthetic.exe';asset.parent.mkdir(parents=True);asset.write_bytes(b'fixture-not-executable')
    asset.with_name(asset.name+'.sig').write_bytes(b'fixture-signature')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GH_REPO','fixture/repository')
    monkeypatch.setenv('GITHUB_SHA','a'*40)
    monkeypatch.setattr(sys,'argv',[str(ROOT/command[1]),*command[2:]])
    calls=[]
    uploaded_assets=[]
    def response(status, data, headers=''):
        return SimpleNamespace(returncode=0 if status<400 else 1,stdout='HTTP/2.0 '+str(status)+' Status\nContent-Type: application/json\n'+headers+'\n'+json.dumps(data),stderr='suppressed')
    def transport(args, **kwargs):
        assert args[:4]==['gh','api','--include','--method']
        method, endpoint=args[4:6]
        calls.append((method,endpoint,kwargs.get('input')))
        assert method in ('GET','POST')
        tag_lookup='/git/ref/tags/' in endpoint
        phase_case=case.removeprefix('tag-') if tag_lookup and case.startswith('tag-') else case if '/releases/tags/' in endpoint else None
        if phase_case in ('401','403','500'):
            return response(int(phase_case),{'message':'denied'})
        if phase_case=='network': raise OSError('offline synthetic transport error')
        if phase_case=='timeout': raise subprocess.TimeoutExpired(args,120)
        if phase_case=='malformed': return SimpleNamespace(returncode=1,stdout='not-json',stderr='')
        if phase_case=='ambiguous404': return response(404,{'message':'unexpected'})
        if '/releases/tags/' in endpoint:
            if case in ('public','wrong-target'):
                return response(200,{'id':9,'draft':False,'target_commitish':'b'*40})
            return response(404,{'message':'Not Found'})
        if '/releases?per_page=' in endpoint:
            if case=='inventory403': return response(403,{'message':'denied'})
            if case=='inventory-malformed': return response(200,{})
            if case=='draft-page2' and endpoint.endswith('page=1'):
                return response(200,[{'tag_name':'other-'+str(i)} for i in range(100)],'Link: <https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2>; rel="next"\n')
            return response(200,[{'tag_name':'v2.5.18-windows','draft':True}] if case in ('draft','draft-page2') else [])
        if tag_lookup:
            return response(200,{'object':{'sha':'b'*40}}) if case=='tag-only' else response(404,{'message':'Not Found'})
        if endpoint.endswith('/git/refs'):
            if case=='tag-race': return response(422,{'message':'Reference already exists'})
            payload=json.loads(kwargs['input']);assert payload=={'ref':'refs/tags/v2.5.18-windows','sha':'a'*40}
            return response(201,{'ref':payload['ref'],'object':{'sha':payload['sha']}})
        if endpoint.endswith('/releases'):
            if case=='release-race': return response(422,{'message':'already exists'})
            payload=json.loads(kwargs['input']);assert payload['draft'] is True and payload['prerelease'] is False and payload['target_commitish']=='a'*40
            return response(201,{'id':123,'draft':True,'tag_name':'v2.5.18-windows'})
        if endpoint.endswith('/releases/123'):
            return response(200,{'id':123,'draft':True,'tag_name':'v2.5.18-windows','assets':uploaded_assets})
        assert '/releases/123/assets?name=synthetic.exe' in endpoint
        if case=='asset-race': return response(422,{'message':'already exists'})
        path=Path(args[-1]);identity=100+len(uploaded_assets)
        item={'id':identity,'url':f'https://api.github.com/repos/fixture/repository/releases/assets/{identity}','name':path.name,'state':'uploaded','size':path.stat().st_size}
        uploaded_assets.append(item)
        return response(201,item)
    monkeypatch.setattr(subprocess,'run',transport)
    if case=='explicit404':
        runpy.run_path(str(ROOT/command[1]),run_name='__main__')
        assert len([call for call in calls if call[0]=='POST'])==4
    else:
        with pytest.raises(RuntimeError):
            runpy.run_path(str(ROOT/command[1]),run_name='__main__')
        expected={'tag-race':1,'release-race':2,'asset-race':3}.get(case,0)
        assert len([call for call in calls if call[0]=='POST'])==expected
    assert not any('edit' in call[1] or 'clobber' in call[1] for call in calls)


def selection_module():
    spec=importlib.util.spec_from_file_location('selection',ROOT/'scripts/run_release_regressions.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_release_selection_contract():
    module=selection_module();module.validate_selection()
    assert len(module.ENGINE)==27 and len(module.CONTROL)==1
    assert module.ENGINE['tests/test_engine_auth.py']=='legacy-fixtures'
    assert module.ENGINE['tests/test_account_isolation_boundary.py']=='independent'
    assert module.NODE==['tests/windows-mobile-key-owner.test.mjs','tests/repository-public-maps.test.mjs']
    source=(ROOT/'.github/workflows/release.yml').read_text(encoding='utf-8')
    for command in ['python scripts/run_release_regressions.py --group control --output "$RUNNER_TEMP/release-control"', 'local-engine/.venv/Scripts/python.exe scripts/run_release_regressions.py --group engine --output "$RUNNER_TEMP/release-engine"']:
        assert command in [line.strip() for line in source.splitlines()]
    assert "cancel-in-progress: false" in source
    assert "group: windows-release-${{ github.event_name == 'workflow_dispatch' && format('v{0}', inputs.version) || github.ref_name }}" in source
    assert 'pip install -r requirements.txt' in source.split('- name: Validate isolated control regressions')[0]
    assert '--clobber' not in source and 'gh release edit' not in source


@pytest.mark.parametrize('name', list(selection_module().ENGINE)+list(selection_module().CONTROL)+selection_module().NODE)
def test_every_required_file_has_fail_closed_existence_guard(tmp_path,name):
    module=selection_module()
    paths=[*module.ENGINE,*module.CONTROL,*module.NODE,'tests/release-regression-required.json','tests/release_evidence.py','tests/release_sandbox/sitecustomize.py','scripts/create_release_only.py']
    for item in paths:
        if item!=name:
            path=tmp_path/item;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
    with pytest.raises(RuntimeError,match='CI_REQUIRED_INPUT_MISSING') as error:
        module.validate_selection(tmp_path)
    assert str(error.value)=='CI_REQUIRED_INPUT_MISSING:'+name


@pytest.mark.parametrize('name', selection_module().REQUIRED)
def test_required_selection_cannot_be_omitted(name):
    module=selection_module()
    if name in module.ENGINE:
        del module.ENGINE[name]
    elif name in module.CONTROL:
        del module.CONTROL[name]
    else:
        module.NODE.remove(name)
    with pytest.raises(RuntimeError,match='CI_REQUIRED_SELECTION_MISSING'):
        module.validate_selection()


def edge_run(tmp_path, monkeypatch, names, pages=None, fault=None):
    import hashlib
    from urllib.parse import parse_qs, urlsplit
    command = workflow_command()
    package = tmp_path/'desktop/package.json'
    package.parent.mkdir()
    package.write_text('{"version":"2.5.18"}')
    originals = {}
    for name in names:
        folder = 'msi' if '.msi' in name.lower() else 'nsis'
        path = tmp_path/'desktop/src-tauri/target/release/bundle'/folder/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(('synthetic bytes '+name).encode('utf-8'))
        originals[path] = path.read_bytes()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GH_REPO','fixture/repository')
    monkeypatch.setenv('GITHUB_SHA','a'*40)
    monkeypatch.setattr(sys,'argv',[str(ROOT/command[1]),*command[2:]])
    calls, uploads = [], []
    def reply(code, body, link=None):
        headers = 'Content-Type: application/json\r\n'
        if link is not None:
            headers += 'Link: '+link+'\r\n'
        return SimpleNamespace(returncode=0 if code<400 else 1,stdout=f'HTTP/2.0 {code} Status\r\n'+headers+'\r\n'+json.dumps(body),stderr='')
    def transport(args, **kwargs):
        method, endpoint = args[4:6]
        calls.append((method,endpoint))
        assert method in ('GET','POST')
        assert kwargs['encoding']=='utf-8'
        if '/releases/tags/' in endpoint or '/git/ref/tags/' in endpoint:
            return reply(404,{'message':'Not Found'})
        if '/releases?' in endpoint:
            page = int(parse_qs(urlsplit(endpoint).query)['page'][0])
            body, link = pages(page) if pages else ([],None)
            return reply(200,body,link)
        if endpoint.endswith('/git/refs'):
            return reply(201,{'ref':'refs/tags/v2.5.18-windows','object':{'sha':'a'*40}})
        if endpoint.endswith('/releases') and method=='POST':
            return reply(201,{'id':123,'draft':True,'tag_name':'v2.5.18-windows'})
        if '/releases/123/assets?' in endpoint:
            name = parse_qs(urlsplit(endpoint).query)['name'][0]
            data = Path(args[-1]).read_bytes()
            asset_id = 101 if fault=='repeat-id' else 101+len(uploads)
            item = {'id':asset_id,'name':name,'state':'uploaded','size':len(data),'url':f'https://api.github.com/repos/fixture/repository/releases/assets/{asset_id}','digest':'sha256:'+hashlib.sha256(data).hexdigest()}
            if fault=='unexpected-name': item['name']='unapproved.exe'
            if fault=='digest': item['digest']='sha256:'+'0'*64
            if fault=='digest-absent': item.pop('digest')
            if fault=='asset-id': item['id']=True
            if fault=='asset-size': item['size']+=1
            if fault=='asset-state': item['state']='starter'
            if fault=='asset-url': item['url']='https://evil.invalid/assets/101'
            uploads.append(item)
            return reply(201,item)
        assert endpoint=='repos/fixture/repository/releases/123' and method=='GET'
        return reply(200,{'id':999 if fault=='release-id' else 123,'draft':True,'tag_name':'v2.5.18-windows','assets':[] if fault=='membership' else uploads})
    monkeypatch.setattr(subprocess,'run',transport)
    error = None
    try:
        runpy.run_path(str(ROOT/command[1]),run_name='__main__')
    except RuntimeError as exc:
        error = str(exc)
    assert all(path.read_bytes()==data for path,data in originals.items())
    manifest_path=tmp_path/'release-asset-manifest.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    return error,calls,uploads,manifest


@pytest.mark.parametrize('local,remote', [
    ('Insurance AI.exe','Insurance_AI.exe'),
    ('모아둠.exe','ubaa8uc544ub460.exe'),
    ('.Insurance.AI.setup.exe','Insurance.AI.setup.exe'),
    ('Insurance AI.v2.5.18.msi','Insurance_AI.v2.5.18.msi'),
])
def test_edge_named_assets(tmp_path,monkeypatch,local,remote):
    error,calls,uploads,manifest=edge_run(tmp_path,monkeypatch,[local,local+'.sig'])
    assert error is None
    assert {item['name'] for item in uploads}=={remote,remote+'.sig'}
    assert manifest['verified'] is True and manifest['release_id']==123
    assert {row['remote_name'] for row in manifest['assets']}=={remote,remote+'.sig'}
    assert all(row['asset_id']>0 for row in manifest['assets'])


def test_edge_short_page(tmp_path,monkeypatch):
    def pages(page):
        if page==1:return ([{'tag_name':'other'}],'<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2>; rel="next"')
        return ([{'tag_name':'v2.5.18-windows','draft':True}],None)
    error,calls,_,_=edge_run(tmp_path,monkeypatch,['safe.exe','safe.exe.sig'],pages)
    assert error is not None
    assert not any(method=='POST' for method,_ in calls)
    assert any('page=2' in endpoint for _,endpoint in calls)


@pytest.mark.parametrize('names', [
    ['a b.exe','a b.exe.sig','a_b.exe','a_b.exe.sig'],
    ['same.exe','same.exe.sig','../msi/same.exe','../msi/same.exe.sig'],
    ['A.exe','A.exe.sig','../msi/a.EXE','../msi/a.EXE.sig'],
    ['.a.exe','.a.exe.sig','a.exe','a.exe.sig'],
    ['모.exe','모.exe.sig','ubaa8.exe','ubaa8.exe.sig'],
    ['orphan.exe.sig'], ['unsigned.msi'], ['other.txt'],
])
def test_edge_preflight_rejects_before_writes(tmp_path,monkeypatch,names):
    error,calls,_,_=edge_run(tmp_path,monkeypatch,names)
    assert error is not None
    assert not any(method=='POST' for method,_ in calls)


@pytest.mark.parametrize('link', [
    '<https://evil.invalid/repos/fixture/repository/releases?per_page=100&page=2>; rel="next"',
    '<https://api.github.com/repos/other/repository/releases?per_page=100&page=2>; rel="next"',
    '<https://user@api.github.com/repos/fixture/repository/releases?per_page=100&page=2>; rel="next"',
    '<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2#fragment>; rel="next"',
    '<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2#>; rel="next"',
    '<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=1>; rel="next"',
    '<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=101>; rel="next"',
    '<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2>; rel="next", <https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2>; rel="next"',
    'not-a-link', '',
    '<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=9>; rel="last"',
    '<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2&page=3>; rel="next"',
    '<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=3>; rel="next"',
])
def test_edge_invalid_pagination_stops_before_writes(tmp_path,monkeypatch,link):
    error,calls,_,_=edge_run(tmp_path,monkeypatch,['safe.exe','safe.exe.sig'],lambda page:([{'tag_name':'other'}],link))
    assert error is not None
    assert not any(method=='POST' for method,_ in calls)
    assert len([endpoint for _,endpoint in calls if '/releases?' in endpoint])==1


@pytest.mark.parametrize('fault',['unexpected-name','digest','asset-id','asset-size','asset-state','asset-url','repeat-id','membership','release-id'])
def test_edge_upload_identity_and_digest_fail_closed(tmp_path,monkeypatch,fault):
    error,calls,_,manifest=edge_run(tmp_path,monkeypatch,['safe.exe','safe.exe.sig'],fault=fault)
    assert error is not None
    assert not manifest['verified']
    assert all(method in ('GET','POST') for method,_ in calls)


def test_edge_digest_absent_is_explicit_not_claimed_verified_digest(tmp_path,monkeypatch):
    error,_,_,manifest=edge_run(tmp_path,monkeypatch,['safe.exe','safe.exe.sig'],fault='digest-absent')
    assert error is None and manifest['verified']
    assert all(row['digest_verified'] is False for row in manifest['assets'])


@pytest.mark.parametrize('local,remote', [('.a.b.exe...','a.b.exe'),('.a.b.exe....sig','a.b.exe.sig'),(' a.msi .sig','a.msi.sig')])
def test_edge_trailing_dot_names_preserve_multi_extension_signature(local,remote):
    publisher=runpy.run_path(str(ROOT/'scripts/create_release_only.py'))
    assert publisher['remote_name'](local)==remote


@pytest.mark.parametrize('mode',['limit','cycle','duplicate-headers'])
def test_edge_inventory_boundaries(tmp_path,monkeypatch,mode):
    def pages(page):
        nxt=page+1
        if mode=='cycle' and page==2:nxt=1
        link=f'<https://api.github.com/repos/fixture/repository/releases?per_page=100&page={nxt}>; rel="next"'
        if mode=='duplicate-headers':link+='\r\nLink: '+link
        return ([{'tag_name':'other'}],link)
    error,calls,_,_=edge_run(tmp_path,monkeypatch,['safe.exe','safe.exe.sig'],pages)
    assert error is not None and not any(method=='POST' for method,_ in calls)
    assert len([endpoint for _,endpoint in calls if '/releases?' in endpoint])=={'limit':100,'cycle':2,'duplicate-headers':1}[mode]


def test_edge_short_page_without_draft_reaches_proven_last_page(tmp_path,monkeypatch):
    def pages(page):
        link='<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2>; rel="next"' if page==1 else '<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2>; rel="last", <https://api.github.com/repos/fixture/repository/releases?per_page=100&page=1>; rel="prev"'
        return ([{'tag_name':'other'}],link)
    error,calls,_,manifest=edge_run(tmp_path,monkeypatch,['safe.exe','safe.exe.sig'],pages)
    assert error is None and manifest['verified']
    assert len([endpoint for _,endpoint in calls if '/releases?' in endpoint])==2


def test_edge_inconsistent_last_page_stops_before_writes(tmp_path,monkeypatch):
    def pages(page):
        link='<https://api.github.com/repos/fixture/repository/releases?per_page=100&page=2>; rel="next", <https://api.github.com/repos/fixture/repository/releases?per_page=100&page=3>; rel="last"' if page==1 else None
        return ([{'tag_name':'other'}],link)
    error,calls,_,_=edge_run(tmp_path,monkeypatch,['safe.exe','safe.exe.sig'],pages)
    assert error is not None and not any(method=='POST' for method,_ in calls)


def test_edge_signature_length_bound_applies_to_final_remote_name():
    publisher=runpy.run_path(str(ROOT/'scripts/create_release_only.py'))
    with pytest.raises(RuntimeError):
        publisher['remote_name']('a'*236+'.exe.sig')
