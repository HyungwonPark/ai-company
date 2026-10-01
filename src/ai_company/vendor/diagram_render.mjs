/* Thin offline adapter around the unmodified, pinned Archify workflow compiler. */
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import dns from 'node:dns';
import { syncBuiltinESMExports } from 'node:module';
const denied=()=>{throw new Error('diagram renderer network access is disabled');};
http.request=http.get=https.request=https.get=net.connect=net.createConnection=dns.lookup=dns.resolve=denied;
dns.promises.lookup=dns.promises.resolve=denied;
globalThis.fetch=denied;
syncBuiltinESMExports();
process.env.ARCHIFY_UPDATE_CHECK_DISABLED='1';
const {compileWorkflow}=await import('./archify/renderers/workflow/workflow-compiler.mjs');
try {
 if(fs.statSync(process.argv[2]).size>2_000_000)throw new Error('diagram input exceeds limit');
 const workflow=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
 const result=compileWorkflow({workflow,qualityProfile:'standard'});
 if(!result.ok){process.stdout.write(JSON.stringify({ok:false,diagnostics:result.diagnostics}));process.exitCode=1;}
 else process.stdout.write(JSON.stringify({ok:true,svg:result.svg,compiler_receipt:result.receipt}));
} catch(error){process.stdout.write(JSON.stringify({ok:false,error:'Archify compilation failed',diagnostics:error.diagnostics||[]}));process.exitCode=1;}
