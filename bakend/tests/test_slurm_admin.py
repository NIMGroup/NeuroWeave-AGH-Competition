from __future__ import annotations

import importlib.util
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch


script = Path(__file__).resolve().parents[2] / 'scripts/configure_slurm_gpu.py'
spec = importlib.util.spec_from_file_location('slurm_admin_under_test', script)
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


class SlurmAdminTests(unittest.TestCase):
    config = b'ClusterName=mycluster\n\nNodeName=node01 Gres=gpu:1\nGresTypes=gpu\n'
    registered = '2026-09-07T11:49:12'

    def probe(self, node='node01'):
        return {'host': node, 'conf_nonblank_sha256': admin.config_fingerprint(self.config),
                'started': datetime.fromisoformat(self.registered).timestamp()}

    def status(self, **changes):
        return {'state': 'DOWN', 'gres': 'gpu:1', 'cfgtres': 'cpu=128,mem=245000M,billing=128',
                'slurmdstarttime': self.registered, **changes}

    def test_blank_lines_and_line_endings_do_not_change_fingerprint(self):
        other = self.config.replace(b'\n\n', b'\n \t\n\n').replace(b'\n', b'\r\n')
        self.assertNotEqual(admin.digest(self.config), admin.digest(other))
        self.assertEqual(admin.config_fingerprint(self.config), admin.config_fingerprint(other))

    def test_actual_configuration_changes_are_not_ignored(self):
        for old, new in [(b'gpu:1', b'gpu:2'), (b'mycluster', b'other'), (b'node01', b'node02')]:
            with self.subTest(new=new):
                self.assertNotEqual(admin.config_fingerprint(self.config),
                                    admin.config_fingerprint(self.config.replace(old, new)))

    def test_kernel_build_hash_does_not_hide_later_node_fields(self):
        text = 'NodeName=node01 OS=Linux 6.8.0 #136~22.04.1 SMP State=DOWN Gres=gpu:1'
        self.assertEqual(admin.status_fields(text)['state'], 'DOWN')
        self.assertEqual(admin.status_fields(text)['gres'], 'gpu:1')

    def test_gpu_gres_does_not_require_gpu_accounting(self):
        admin.verify_registration('node01', self.status(), self.probe(), admin.config_fingerprint(self.config))

    def test_gpu_accounting_when_present_must_agree(self):
        for count in ('0', '2', '10'):
            with self.subTest(count=count), self.assertRaisesRegex(RuntimeError, 'contradicts'):
                admin.verify_registration('node01', self.status(cfgtres='cpu=128,gres/gpu='+count),
                                          self.probe(), admin.config_fingerprint(self.config))

    def test_missing_or_wrong_gres_is_rejected(self):
        for gres in ('', '(null)', 'gpu:0', 'gpu:2'):
            with self.subTest(gres=gres), self.assertRaisesRegex(RuntimeError, 'registered one GPU'):
                admin.verify_registration('node01', self.status(gres=gres), self.probe(),
                                          admin.config_fingerprint(self.config))

    def test_unresponsive_and_drained_nodes_are_rejected(self):
        for state in ('DOWN*', 'IDLE*', 'DRAIN', 'UNKNOWN', 'ALLOCATED'):
            with self.subTest(state=state), self.assertRaisesRegex(RuntimeError, 'unexpected state'):
                admin.verify_registration('node01', self.status(state=state), self.probe(),
                                          admin.config_fingerprint(self.config))

    def test_stale_registration_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            admin.verify_registration('node01', self.status(slurmdstarttime='2026-09-06T11:49:12'),
                                      self.probe(), admin.config_fingerprint(self.config))

    def test_configuration_difference_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'differ'):
            admin.verify_registration('node01', self.status(), self.probe(), 'different')

    def run_finalize(self, resume, bad_node=False):
        calls = []

        def command(argv):
            calls.append(argv)
            if argv == ['scontrol', 'show', 'config']:
                return 'GresTypes = gpu'
            if argv[-1].endswith('--verify-node'):
                return json.dumps(self.probe(argv[-2]))
            if argv[:3] == ['scontrol', 'show', 'node']:
                gres = 'gpu:2' if bad_node and argv[3] == 'node02' else 'gpu:1'
                return f'OS=Linux 6.8.0 #136~22.04.1 SMP State=DOWN Gres={gres} CfgTRES=cpu=128,mem=245000M SlurmdStartTime={self.registered}'
            return ''

        with patch.object(admin, 'CONF', Mock(read_bytes=lambda: self.config)), \
                patch.object(admin, 'planned_files', return_value={}), \
                patch.object(admin, 'queue_empty'), patch.object(admin, 'run', side_effect=command), \
                patch.object(admin.os, 'geteuid', return_value=0, create=True), patch('builtins.print'):
            if bad_node:
                with self.assertRaises(RuntimeError):
                    admin.finalize('mgr', 'hipeson', resume=resume)
            else:
                admin.finalize('mgr', 'hipeson', resume=resume)
        return [call for call in calls if call[:2] == ['scontrol', 'update']]

    def test_read_only_check_does_not_resume_nodes(self):
        self.assertEqual(self.run_finalize(False), [])

    def test_finalize_resumes_exactly_once_after_all_checks(self):
        self.assertEqual(self.run_finalize(True),
                         [['scontrol', 'update', 'NodeName=node[01-03]', 'State=RESUME']])

    def test_failed_finalization_never_resumes_nodes(self):
        self.assertEqual(self.run_finalize(True, bad_node=True), [])
