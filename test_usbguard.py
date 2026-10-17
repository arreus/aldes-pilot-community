"""USB recovery budget and transaction safety; no network or USB access."""
import unittest

from usbguard import BUDGET, WINDOW, decide, parse


class USBGuardTests(unittest.TestCase):
    def test_enabled_usb_is_untouched(self):
        self.assertEqual(decide({'usb':'1','tx':'IDLE'},[],100),(None,[]))

    def test_disabled_usb_can_be_reenabled(self):
        self.assertEqual(decide({'usb':'0','tx':'IDLE'},[],100),('usbon',[100]))

    def test_pending_transaction_is_never_interrupted(self):
        self.assertEqual(decide({'usb':'0','tx':'PENDING'},[],100),(None,[]))

    def test_missing_usb_status_does_not_enable_usb(self):
        self.assertEqual(decide({'tx':'IDLE'},[],100),(None,[]))

    def test_hourly_budget_blocks_repeated_recovery(self):
        attempts = []
        for tick in range(BUDGET):
            action, attempts = decide({'usb':'0','tx':'IDLE'},attempts,100+tick)
            self.assertEqual(action,'usbon')
        self.assertEqual(decide({'usb':'0','tx':'IDLE'},attempts,110),('blocked',attempts))

    def test_expired_attempts_release_budget(self):
        self.assertEqual(decide({'usb':'0','tx':'IDLE'},[100]*BUDGET,100+WINDOW),
                         ('usbon',[100+WINDOW]))

    def test_status_reply_is_required(self):
        self.assertEqual(parse('STATUS usb=0 mounted=0 tx=IDLE')['usb'],'0')
        with self.assertRaises(RuntimeError):
            parse('ERROR unavailable')


if __name__ == '__main__':
    unittest.main()
