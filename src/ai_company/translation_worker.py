"""One bounded translation queue pass. Does not create a timer or change a flow."""
import argparse
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from ai_company.adapters.translation_cli import TranslationCLI
from ai_company.translations import TranslationStore, initialize


def run_once(store, adapter=None, *, execution_alive=None):
    adapter = adapter or TranslationCLI()
    # Readiness is provided by the trusted adapter implementation, never HTTP or model output.
    readiness = adapter.ready if adapter.available else False
    probe = execution_alive or getattr(adapter, 'execution_alive', None)
    job = store.claim(uuid4().hex, adapter_ready=readiness, execution_alive=probe)
    if job is None:
        return None
    if not adapter.ready(job['config']):
        store.finish(job['id'], job['lease_token'], {'category': 'blocked', 'reason': 'tool_free_execution_not_verified'})
        return store._public(store._get(job['id']))
    def on_start(identity):
        if not store.started(job['id'], job['lease_token'], identity):
            raise RuntimeError('translation lease was superseded before execution')
    try:
        # Persist the uncertainty boundary before handing control to any adapter.
        on_start(None)
        outcome = adapter.execute(job, on_start)
    except Exception:
        # An adapter exception cannot prove child termination. Keep the lease and
        # require the adapter's recovery probe before another model invocation.
        return {'id': job['id'], 'status': 'running', 'reason': 'adapter_termination_unconfirmed'}
    store.finish(job['id'], job['lease_token'], outcome)
    return store._public(store._get(job['id']))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run one bounded translation worker pass; Luna remains blocked, Haiku requires explicit local configuration.')
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--database', type=Path, help='Existing explicitly selected SQLite state; no implicit operational path')
    sources.add_argument('--state-dir', type=Path, help='Explicit management state; synchronize its projects before one pass')
    parser.add_argument('--config', type=Path, help='Trusted local translation JSON configuration; never accepted from HTTP')
    parser.add_argument('--execute', action='store_true', help='Enable the verified tool-free Claude CLI path for explicitly configured Haiku')
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text()) if args.config else None
    if args.state_dir:
        from ai_company.management import ManagementStore
        from ai_company.collaboration import document_sources
        from ai_company.translations import configuration
        configuration(config)
        management = ManagementStore(args.state_dir)
        try:
            store = TranslationStore(management.db)
            initialize(management.db)
            failures = []
            for project in management.list_projects():
                try:
                    store.sync(project['id'], document_sources(management.overview(project['id'])), config)
                except (ValueError, KeyError, TypeError):
                    failures.append({'project_id': project['id'], 'reason': 'source_sync_invalid'})
            adapter = TranslationCLI(args.state_dir / 'translation-runtime') if args.execute else None
            result = run_once(store, adapter)
            print(json.dumps({'result': result or {'status': 'idle_or_blocked'}, 'sync_failures': failures}, ensure_ascii=False))
        finally:
            management.close()
        return
    path = args.database.resolve(strict=True)
    db = sqlite3.connect('file:' + str(path) + '?mode=rw', uri=True, timeout=5)
    try:
        initialize(db)
        adapter = TranslationCLI(path.parent / 'translation-runtime') if args.execute else None
        result = run_once(TranslationStore(db), adapter)
        print(json.dumps(result or {'status': 'idle_or_blocked'}, ensure_ascii=False))
    finally:
        db.close()


if __name__ == '__main__':
    main()
