"""Native integration fixture. Replaces ONLY central Auth transport with synthetic identity."""
import sys,json
sys.stdin.reconfigure(encoding='utf-8',errors='strict')
sys.stdout.reconfigure(encoding='utf-8',errors='strict')
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local-engine'))
if sys.argv[1:]==['plan']:
    from account_migration import plan_migration
    value=json.loads(sys.stdin.read());root=Path(value['root']);source=root/'pdfs';source.mkdir()
    (source/'fixture.bin').write_bytes(b'SYNTHETIC NATIVE PIPE DATA')
    import base64,sqlite3
    (root/'legacy.key').write_bytes(base64.b64encode(b'N'*32))
    conn=sqlite3.connect(root/'legacy-rag.sqlite3')
    conn.execute('CREATE TABLE chunks(chunk_id TEXT,doc_id TEXT,filename TEXT,page INTEGER,text TEXT,embedding TEXT,embed_model TEXT)')
    conn.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',('c','d','NATIVE-PRIVATE.pdf',1,'NATIVE-PRIVATE-TEXT','[1,0]','fixture'))
    conn.commit();conn.close()
    files={'pdfs/fixture.bin':'pdfs/fixture.bin','config/customer-db.key':'legacy.key','data/rag-index.sqlite3':'legacy-rag.sqlite3'}
    mode=value.get('mode','legacy')
    if mode=='legacy':plan=plan_migration(root,root,files)
    else:
        from account_migration import plan_workspace_transition
        from account_workspace import VerifiedPrincipal,workspace_for
        from protected_business_key import encode_key
        from database.crypto import FieldCipher
        owner=VerifiedPrincipal('https://fixture.supabase.co/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        target=workspace_for(root,owner);target.key.parent.mkdir(parents=True);target.database.parent.mkdir(parents=True)
        if mode=='empty-bootstrap':
            target.key.write_bytes(encode_key(b'O'*32,owner.issuer,owner.subject))
            conn=sqlite3.connect(target.database);conn.execute('CREATE TABLE customers(name TEXT)');conn.close()
            plan=plan_workspace_transition(root,root,files,owner,mode=mode)
        elif mode=='same-owner-format':
            target.key.write_bytes((root/'legacy.key').read_bytes());target.rag_database.write_bytes((root/'legacy-rag.sqlite3').read_bytes())
            target.pdfs.mkdir();(target.pdfs/'fixture.bin').write_bytes((source/'fixture.bin').read_bytes())
            conn=sqlite3.connect(target.database);conn.execute('CREATE TABLE customers(name TEXT)');conn.execute('INSERT INTO customers VALUES(?)',(FieldCipher(b'N'*32,'fixture').encrypt('NATIVE-PRIVATE-CUSTOMER'),));conn.commit();conn.close()
            files={p:p for p in ['config/customer-db.key','data/customers.sqlite3','data/rag-index.sqlite3','pdfs/fixture.bin']}
            plan=plan_workspace_transition(target.root,root,files,owner,mode=mode)
        else:raise ValueError('Unknown synthetic mode')
    print(json.dumps(plan))
else:
    import account_maintenance_worker as worker
    original=worker.execute_reviewed_migration
    def checked(**kwargs):
        try:
            result=original(**kwargs)
            if kwargs['operation']=='migrate':
                from account_workspace import VerifiedPrincipal,workspace_for
                from protected_business_key import read_protected
                from database.crypto import FieldCipher
                from rag.store import VectorStore
                owner=VerifiedPrincipal(kwargs['config'].central_url+'/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
                target=workspace_for(kwargs['plan']['installation'],owner)
                key=read_protected(target.key,owner.issuer,owner.subject)
                assert key==b'N'*32
                assert b'NATIVE-PRIVATE' not in target.rag_database.read_bytes()
                store=VectorStore(target.rag_database,cipher=FieldCipher(key,'synthetic-native-check'),owner=[owner.issuer,owner.subject])
                try:assert store.search([1,0])[0]['text']=='NATIVE-PRIVATE-TEXT'
                finally:store.close()
            return result
        except Exception:
            import traceback
            traceback.print_exc()
            raise
    worker.execute_reviewed_migration=checked
    run=worker.run
    class Online:
        status=200
        def open(self,request,timeout):return self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return b'{"id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}'
    sys.stderr.reconfigure(encoding='utf-8',errors='strict')
    sys.exit(run(sys.stdin,sys.stderr,opener=Online()))
