import os
import time
import unittest
from unittest.mock import patch

from korean_patch_manager import guidance


@unittest.skipUnless(os.environ.get('KPM_TEST_GUI') == '1', 'GUI 시험은 KPM_TEST_GUI=1로 실행합니다')
class GuiGuidanceTests(unittest.TestCase):
    def test_help_navigation_checks_and_job_feedback(self):
        from KoreanPatchManager import App
        app = App()
        errors = []
        app.report_callback_exception = lambda *args: errors.append(args)
        try:
            app.withdraw()
            app.update()
            for name, scenario in guidance.SCENARIOS.items():
                app.scenario.set(name)
                app.update()
                self.assertEqual(app.guide_text.get('1.0', 'end').strip(), scenario['text'])
                app.open_scenario_tab()
                app.update()
                self.assertEqual(app.book.tab(app.book.select(), 'text'), scenario['tab'])
            app.scenario.set('받은 통합 패키지 적용 · 배너 포함 가능')
            app.vars['repack_format'].set('CIA')
            self.assertIn('certs.bin', app.checks_text.get('1.0', 'end'))
            app.vars['repack_format'].set('CCI')
            self.assertNotIn('certs.bin', app.checks_text.get('1.0', 'end'))
            with patch('KoreanPatchManager.messagebox.showinfo') as info:
                app.field_help('keys', '키 폴더')
                self.assertIn('빈 폴더', info.call_args.args[1])

            def wait_for_job():
                deadline = time.monotonic() + 3
                while app.busy and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertFalse(app.busy)

            app.run_job('시험', lambda: '검사 결과')
            wait_for_job()
            self.assertIn('완료', app.status_label.cget('text'))
            self.assertIn('검사 결과', app.log.get('1.0', 'end'))
            with patch('KoreanPatchManager.messagebox.showerror') as error:
                def fail():
                    raise ValueError('시험 오류')
                app.run_job('실패 시험', fail)
                wait_for_job()
                self.assertIn('실패', app.status_label.cget('text'))
                self.assertTrue(error.called)
            self.assertEqual(errors, [])
        finally:
            app.destroy()
