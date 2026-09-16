/* Cache only the public application shell. Private API responses and all writes
   always go to the network; no background sync or queued approval replay. */
const CACHE='ai-company-shell-v6';
const SHELL=['/','/index.html','/styles.css','/theme.js','/fonts/fonts.css','/fonts/ibmplexsans-400.woff2','/fonts/ibmplexsans-500.woff2','/fonts/ibmplexsans-600.woff2','/app.js','/execution-ui.js','/manager-ui.js','/manager.css','/plan-ui.js','/report-ui.js','/collaboration-ui.js','/documents-ui.js','/icon.svg','/manifest.webmanifest'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)));});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('ai-company-shell-')&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',event=>{const url=new URL(event.request.url);if(event.request.method!=='GET'||url.origin!==self.location.origin||url.search||!SHELL.includes(url.pathname))return;event.respondWith(fetch(event.request).then(response=>{if(response.ok){const copy=response.clone();event.waitUntil(caches.open(CACHE).then(cache=>cache.put(event.request,copy)));}return response;}).catch(()=>caches.match(event.request).then(response=>response||Response.error())));});
