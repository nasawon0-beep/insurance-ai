import base64,json
from types import SimpleNamespace
import pytest
from database import crypto
from rag.store import VectorStore

@pytest.mark.parametrize('fault',['wrong-key','wrong-owner','text','embedding','filename','page','doc_id'])
def test_rag_rejects_foreign_key_owner_and_tampering(tmp_path,fault):
 from cryptography.exceptions import InvalidTag
 key=b'A'*32;owner=['https://fixture.supabase.co/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa']
 path=tmp_path/'protected.sqlite3';s=VectorStore(path,cipher=crypto.FieldCipher(key,'fixture'),owner=owner)
 s.add([SimpleNamespace(chunk_id='c',doc_id='d',filename='PRIVATE.pdf',page=1,text='PRIVATE')],[[1,0]],'fixture')
 if fault in ['text','embedding','filename']:
  s.conn.execute(f"UPDATE chunks SET {fault}=?",('enc:v2:AAAA',));s.conn.commit()
 elif fault=='page':s.conn.execute('UPDATE chunks SET page=2');s.conn.commit()
 elif fault=='doc_id':s.conn.execute("UPDATE chunks SET doc_id='foreign'");s.conn.commit()
 s.close()
 if fault=='wrong-key':key=b'B'*32
 if fault=='wrong-owner':owner=[owner[0],'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb']
 s=VectorStore(path,cipher=crypto.FieldCipher(key,'fixture'),owner=owner)
 try:
  with pytest.raises((InvalidTag,ValueError)):s.search([1,0])
 finally:s.close()


def test_explicit_legacy_rag_conversion_preserves_original(tmp_path,monkeypatch):
 import sqlite3
 from rag import store
 monkeypatch.setenv('CUSTOMER_DB_KEY_B64',base64.b64encode(b'M'*32).decode());crypto.reset_cache()
 source=tmp_path/'legacy.sqlite3';dest=tmp_path/'protected.sqlite3'
 conn=sqlite3.connect(source)
 conn.execute('create table chunks(chunk_id text,doc_id text,filename text,page integer,text text,embedding text,embed_model text)')
 conn.execute('insert into chunks values(?,?,?,?,?,?,?)',('c','d','PRIVATE.pdf',1,'LEGACY PRIVATE','[1,0]','fixture'));conn.commit();conn.close()
 before=source.read_bytes()
 with pytest.raises(ValueError):VectorStore(source)
 store.convert_legacy_store(source,dest,cipher=crypto.get_cipher(),owner=[],confirmed=True)
 assert source.read_bytes()==before and b'LEGACY PRIVATE' not in dest.read_bytes()
 result=VectorStore(dest)
 try:assert result.search([1,0])[0]['text']=='LEGACY PRIVATE'
 finally:result.close();crypto.reset_cache()


def test_rag_authenticated_encryption_and_search(tmp_path,monkeypatch):
 monkeypatch.setenv('CUSTOMER_DB_KEY_B64',base64.b64encode(b'R'*32).decode());crypto.reset_cache()
 p=tmp_path/'rag.sqlite3';s=VectorStore(p)
 try:
  s.add([SimpleNamespace(chunk_id='c',doc_id='d',filename='PRIVATE-NAME.pdf',page=1,text='PRIVATE-TEXT')],[[1.0,0.0]],'fixture')
  s.upsert_doc('d','hash','PRIVATE-NAME.pdf',1,'fixture')
  assert b'PRIVATE-TEXT' not in p.read_bytes() and b'PRIVATE-NAME' not in p.read_bytes()
  assert s.search([1.0,0.0])[0]['text']=='PRIVATE-TEXT'
  assert s.docs()[0]['filename']=='PRIVATE-NAME.pdf'
  row=s.conn.execute('select text from chunks').fetchone()[0]
  s.conn.execute("update chunks set chunk_id='different'");s.conn.commit()
  with pytest.raises(Exception):s.search([1.0,0.0])
 finally:s.close();crypto.reset_cache()
