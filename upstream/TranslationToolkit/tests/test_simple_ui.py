import os
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('KPM_TEST_GUI') == '1', 'GUI 시험 설정 필요')
class SimpleUITests(unittest.TestCase):
    def test_default_mode_fields_help_and_worker_result(self):
        from KoreanPatchManager import App
        from korean_patch_manager.simple_ui import show_preparation_help
        app = App(); errors = []
        app.report_callback_exception = lambda *args: errors.append(args)
        try:
            app.withdraw(); app.update()
            self.assertEqual(app.mode.get(), '간편 모드')
            panel = app.simple_panel
            panel.next(); app.update()
            self.assertEqual([key for key, _ in panel.required_fields()], ['game', 'package', 'output'])
            panel.fmt.set('CIA'); panel.render()
            self.assertIn('keys', [key for key, _ in panel.required_fields()])
            panel.next()
            self.assertEqual(panel.step, 1)
            self.assertIn('선택', panel.validation.cget('text'))
            panel.purpose.set('install'); panel.render()
            self.assertEqual(panel.required_fields(), [])
            app.mode.set('전체 모드'); app.update(); app.mode.set('간편 모드'); app.update()
            for topic in ('game', 'keys', 'certs', 'boot9', 'romfs', 'package'):
                window = show_preparation_help(app, topic); app.update(); window.destroy()
            app.run_job('간편 시험', lambda: 'saved result.cci', on_done=panel.finished)
            deadline = time.monotonic() + 3
            while app.busy and time.monotonic() < deadline:
                app.update(); time.sleep(.01)
            self.assertFalse(app.busy)
            self.assertTrue(panel.result_ok)
            self.assertIn('result.cci', panel.result_text)
            self.assertEqual(panel.step, 3)
            self.assertEqual(errors, [])
        finally: app.destroy()
