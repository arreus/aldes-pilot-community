"""Synthetic loss-counter and host-restart regressions; no device access."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pico import Pilot, capture_health
from reader import snapshot
from test_reader import chunk


def status(tick, losses=283):
    return (f'[{tick}] STATUS mounted=1 rx_errors=0 '
            f'rx_rearm_failed=0 log perdus={losses}\n')


def report(tick=15000, transfer=1):
    frame = bytearray(90)
    frame[:5] = bytes.fromhex('fffd5aff21')
    frame[13:21] = (2200).to_bytes(2, 'little') * 4
    frame[82] = 1
    frame[-2] = 254
    frame[-1] = -sum(frame) % 256
    return chunk(transfer, tick, frame[:64]) + chunk(transfer+1, tick+1, frame[64:])


class ReconnectTests(unittest.TestCase):
    def available(self, raw, age=0):
        data = snapshot(raw.encode(), 100, 100+age)
        return data['pages'].get('21', {}).get('available', False)

    def test_historical_losses_and_zero_both_recover(self):
        for count in (0, 283):
            with self.subTest(count=count):
                self.assertTrue(self.available(status(10000,count)+report()+status(20000,count)))

    def test_loss_increase_invalidates_old_report(self):
        self.assertFalse(self.available(status(10000)+report()+status(20000,284)))

    def test_stable_counter_does_not_revive_pre_loss_report(self):
        self.assertFalse(self.available(status(10000)+report()+status(20000,284)+status(30000,284)))

    def test_new_report_after_loss_recovers(self):
        self.assertTrue(self.available(status(10000)+report()+status(20000,284)
                                      +report(25000,3)+status(30000,284)))

    def test_reconnect_requires_new_status_baseline_and_report(self):
        old = status(10000)+report()+status(20000)
        boundary = '[collector] CONNECT receive_only\n'
        self.assertFalse(self.available(old+boundary))
        self.assertFalse(self.available(old+boundary+status(30000)))
        self.assertTrue(self.available(old+boundary+status(30000)+report(35000,3)+status(40000)))

    def test_counter_reset_invalidates_old_report(self):
        self.assertFalse(self.available(status(10000)+report()+status(20000,0)))

    def test_missing_counter_invalidates_status(self):
        self.assertFalse(self.available(status(10000)+report()+status(20000).replace('log perdus=283','unknown')))

    def test_stale_capture_remains_unavailable(self):
        self.assertFalse(self.available(status(10000)+report()+status(20000), age=61))

    def test_future_file_timestamp_is_not_fresh(self):
        self.assertFalse(self.available(status(10000)+report()+status(20000), age=-1))

    def test_status_fields_require_exact_values(self):
        for old, new in (('mounted=1','mounted=10'), ('rx_errors=0','rx_errors=01'),
                         ('rx_rearm_failed=0','rx_rearm_failed=01')):
            with self.subTest(field=old):
                self.assertFalse(self.available(status(10000)+report()+status(20000).replace(old,new)))

    def test_single_sample_is_not_a_baseline(self):
        self.assertFalse(self.available(status(10000)+report()))

    def test_usb_unmounted_and_receive_error_remain_blocking(self):
        for field in ('mounted', 'rx_errors', 'rx_rearm_failed'):
            with self.subTest(field=field):
                tail = status(20000).replace(field+'='+('1' if field=='mounted' else '0'),
                                             field+'='+('0' if field=='mounted' else '1'))
                self.assertFalse(self.available(status(10000)+report()+tail))

    def test_reader_and_command_health_agree_on_loss_transitions(self):
        prefix = status(10000)+report()
        for tail, valid in ((status(20000),True), (status(20000,284),False),
                            ('[collector] CONNECT receive_only\n'+status(20000),False)):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'capture'
                path.write_text(prefix+tail)
                with patch('pico._find_capture',return_value=path), patch('pico.DRY_RUN',False):
                    if valid:
                        self.assertTrue(capture_health()['rx_ok'])
                    else:
                        with self.assertRaises(RuntimeError):
                            capture_health()

    def test_host_restart_preserves_intent_without_replaying_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'capture'
            good = status(10000)+report()+status(20000)
            path.write_text(good)
            with patch('pico._find_capture',return_value=path), patch('pico.DRY_RUN',False), \
                 patch('socket.create_connection',side_effect=AssertionError('No device connection')):
                first = Pilot(root/'private')
                first._journal(event='delivered',kind='air',zone=None,value=0,utc=time.time()-100)
                restarted = Pilot(root/'private')
                journal = restarted.journal_path.read_bytes()
                self.assertTrue(restarted.state()['available'])
                os.utime(path,(time.time()-90,time.time()-90))
                self.assertFalse(restarted.state()['available'])
                # The Pico continues running while the host is offline: its
                # cumulative loss counter advances. Reconnection establishes
                # a new baseline and restores observed state without a POST.
                path.write_text(good+'[collector] CONNECT receive_only\n'
                                +status(30000,500)+report(35000,3)+status(40000,500))
                restored = restarted.state()
                self.assertTrue(restored['available'])
                self.assertTrue(restored['air']['matches_request'])
                self.assertEqual(restarted.journal_path.read_bytes(),journal)


if __name__ == '__main__':
    unittest.main()
