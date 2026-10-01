"""Exercise records created before the v2 PM contract without rewriting their digest."""

import json
from unittest.mock import patch

from ai_company.management import ManagementStore


def mark_legacy_request(store, request_id):
    request = store.get_pm_request(request_id)
    request.pop('requirements_contract_version', None)
    store.db.execute('UPDATE management_pm_requests SET document=? WHERE message_id=?',
                     (json.dumps(request), request_id))


def use_legacy_requests(testcase):
    original = ManagementStore._append_pm_request

    def append_v1(store, project, value):
        message = original(store, project, value)
        mark_legacy_request(store, message['id'])
        return message

    active = patch.object(ManagementStore, '_append_pm_request', append_v1)
    active.start()
    testcase.addCleanup(active.stop)
