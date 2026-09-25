"""A supervised process start must notify each active delivery wrapper once."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_company.dispatcher import Dispatcher
from ai_company.harness.guidance import reference


class SpawnFinished(RuntimeError):
    pass


class SkillSpawnTests(unittest.TestCase):
    def test_guidance_and_role_skill_chain_each_prior_callback_once(self):
        for guided, skilled, original, documents in ((True, False, True, []), (False, True, True, []),
                (True, True, True, [{"skill_id": "s"}]), (True, True, False, [])):
            with self.subTest(guided=guided, skilled=skilled, original=original, documents=documents):
                phases, base_spawns = [], []

                def executor(*_args, **kwargs):
                    kwargs["on_spawn"]({"pid": 12345})
                    raise SpawnFinished

                fake = SimpleNamespace(
                    queue=SimpleNamespace(get=lambda _job: {"status": "RUNNING", "attempt_count": 1,
                                                            "job_id": "job"}),
                    _guidance_event=lambda _record, phase, **_facts: phases.append(phase),
                    _external=lambda function, *args, **kwargs: function(*args, **kwargs),
                    clock=lambda: 1000, executor=executor)
                fake._guided_external = lambda *args, **kwargs: Dispatcher._guided_external(fake, *args, **kwargs)
                plan = {}
                if guided:
                    plan["guidance"] = reference().model_dump(mode="json")
                if skilled:
                    plan["skill_delivery"] = {"task_id": "task", "delivery_id": "delivery",
                        "selection_digest": "selection", "role_key": "impl", "documents": documents}
                spec = SimpleNamespace(execution_scope="contribution",
                    agents=[SimpleNamespace(agent_id="dev", provider="codex")],
                    policy=SimpleNamespace(configuration_evidence="fixture", max_runtime_seconds=120),
                    task=SimpleNamespace(task_id="task"), mode="fixture", plan=plan)
                state = {"task_id": "task", "stage": "developer", "usage": {"runtime_seconds": 0},
                    "spec_digest": "spec", "active": {"agent_id": "dev", "job_id": "job",
                    "execution_id": "execution", "role": "developer", "generation": 0,
                    "provider": "codex", "task_digest": "task", "policy_digest": "policy"}}
                options = {"timeout_seconds": 60}
                if original:
                    options["on_spawn"] = base_spawns.append
                with patch("ai_company.harness.guidance.augment", return_value=("prompt", "hash")):
                    with self.assertRaises(SpawnFinished):
                        Dispatcher._executor(fake, spec, state)("codex", "/tmp", "prompt", None, **options)
                self.assertEqual(len(base_spawns), int(original))
                self.assertEqual(phases.count("process_started"), int(guided))
                self.assertEqual(phases.count("skill_process_started"), int(skilled))
