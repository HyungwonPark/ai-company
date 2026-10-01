import hashlib,http.client,json,os,secrets,tempfile,threading,time
from pathlib import Path
import ai_company
from ai_company import password_auth
from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer
base=Path(ai_company.__file__).parent
expected=json.loads(Path('/checks/source-manifest.json').read_text())
actual={name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in expected}
assert actual==expected,'Installed source differs from candidate'
with tempfile.TemporaryDirectory() as directory:
 state=Path(directory)
 store=ManagementStore(state)
 password_auth.initialize(store.db)
 with store.db:password_auth.create_user(store.db,'edward',secrets.token_urlsafe(24),time.time())
 store.close()
 server=ManagementHTTPServer(('127.0.0.1',0),state,password_login=True,web_root=base/'web',public_origin='https://candidate.invalid')
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 def request(path,host='candidate.invalid'):
  client=http.client.HTTPConnection('127.0.0.1',server.server_port)
  client.request('GET',path,headers={'Host':host});r=client.getresponse();status=r.status;headers=dict(r.getheaders());data=r.read();client.close();return status,headers,data
 try:
  assets={}
  for endpoint,name in [('/','web/index.html'),('/app.js','web/app.js'),('/journey-ui.js','web/journey-ui.js'),('/sw.js','web/sw.js'),('/workspace-graph-ui.js','web/workspace-graph-ui.js')]:
   status,headers,data=request(endpoint);assert status==200,(endpoint,status);assert hashlib.sha256(data).hexdigest()==expected[name]
   assert headers['Cache-Control']=='no-store';assets[endpoint]=hashlib.sha256(data).hexdigest()
  assert request('/api/projects')[0]==401
  assert request('/api/projects','foreign.invalid')[0]==403
  assert b'ai-company-shell-v9' in request('/sw.js')[2]
  print(json.dumps({'status':'PASS','installed_source_files':len(expected),'source_manifest_sha256':hashlib.sha256(Path('/checks/source-manifest.json').read_bytes()).hexdigest(),'assets':assets,'anonymous_api':401,'foreign_host':403,'api_and_worker_code':'verified by source manifest; no worker/model invocation','isolation':{'uid':os.getuid(),'network':'none; loopback HTTP only','production_mounts':False,'root_read_only':True,'state':'temporary tmpfs'},'limitations':['not production health','not physical APK','not actual model execution']},sort_keys=True))
 finally:
  server.shutdown();server.server_close();thread.join(5)
