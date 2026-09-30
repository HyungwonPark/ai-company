import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review_pages', ROOT / 'scripts/build_review_pages.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SOURCE = '35df64ac5a2bfe0dd126d53128c9e63da7404aa0'
CODE = 'e659fc66cadfb5af77f85327314671b46a0383e0'


class ReviewPagesTests(unittest.TestCase):
    def test_source_binding_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp) / 'one', Path(temp) / 'two'
            manifest = module.build(SOURCE, CODE, first)
            module.build(SOURCE, CODE, second)
            self.assertEqual((first / 'AI-Company-review-preview.zip').read_bytes(), (second / 'AI-Company-review-preview.zip').read_bytes())
            for page, path in module.DOCUMENTS.items():
                self.assertEqual(manifest['sources'][path], module.digest(module.git_source(SOURCE, path)))
            for filename, sha in manifest['files'].items():
                self.assertEqual(module.digest((first / filename).read_bytes()), sha)
            with zipfile.ZipFile(first / 'AI-Company-review-preview.zip') as archive:
                self.assertEqual(json.loads(archive.read('manifest.json')), manifest)
            with self.assertRaises(ValueError):
                module.build(SOURCE, CODE, first)

    def test_version_and_observation_time_stay_distinct(self):
        source = module.git_source(SOURCE, module.DOCUMENTS['index']).decode()
        result = module.render(source, 'index', SOURCE, CODE, False)
        self.assertIn('2026-09-29 16:17 KST', result)
        self.assertIn(SOURCE, result)
        self.assertIn(CODE, result)
        self.assertIn('실시간 상태', result)
        self.assertNotIn('2026-09-30', result)

    def test_links_are_pinned_and_aliases_work(self):
        self.assertEqual(module.link_target('manual.md', 'index', SOURCE, False), '/review/manual')
        self.assertEqual(module.link_target('index.md#후보별-확인-수준', 'manual', SOURCE, False), '/review#후보별-확인-수준')
        self.assertEqual(module.link_target('manual.md', 'index', SOURCE, True), 'manual.html')
        self.assertEqual(module.link_target('../work-reviews/README.md', 'index', SOURCE, False), f'{module.REPOSITORY}/blob/{SOURCE}/docs/work-reviews/README.md')
        for target in ('javascript:alert(1)', '//evil.invalid', '/private', '../../../secrets', 'https://github.com/HyungwonPark/ai-company/blob/main/README.md'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                module.link_target(target, 'index', SOURCE, False)

    def test_html_and_mermaid_are_inert(self):
        result = module.render('# 문서\n<script>alert(1)</script>\n\n```mermaid\nflowchart TD\nA-->B\n```', 'index', SOURCE, CODE, False)
        self.assertNotIn('<script', result)
        self.assertIn('&lt;script&gt;', result)
        self.assertIn('흐름 도식 원문', result)
        self.assertNotIn('<iframe', result)
        with self.assertRaises(ValueError):
            module.render('![원문](https://evil.invalid/x.png)', 'index', SOURCE, CODE, False)

    def test_missing_version_fails_without_creating_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'new'
            with self.assertRaises(ValueError):
                module.build('35df64a', CODE, output)
            self.assertFalse(output.exists())

    def test_manual_headings_match_existing_fragment_links(self):
        result = module.render(module.git_source(SOURCE, module.DOCUMENTS['manual']).decode(), 'manual', SOURCE, CODE, False)
        for anchor in ('1-목표와-완료-조건', '4-개발검수적용-절차', '6-대기중단복구', '8-문서근거접근-정보'):
            self.assertIn(f'id="{anchor}"', result)
        self.assertNotIn('<h2 id="목차"', result)


if __name__ == '__main__':
    unittest.main()
