import unittest
from unittest.mock import MagicMock, patch

from trigger_scan import trigger


class TriggerScanTests(unittest.TestCase):
    @patch("trigger_scan.urllib.request.urlopen")
    def test_private_service_host_uses_configured_http_scheme(self, urlopen):
        response = MagicMock()
        response.status = 202
        response.read.return_value = b'{"message":"Scan started."}'
        urlopen.return_value.__enter__.return_value = response

        result = trigger(
            "playbook-internal:10000",
            "secret",
            scheme="http",
        )

        request = urlopen.call_args.args[0]
        self.assertEqual(result, 0)
        self.assertEqual(
            request.full_url,
            "http://playbook-internal:10000/api/opportunities/run",
        )
        self.assertEqual(request.get_method(), "POST")

    def test_invalid_implicit_scheme_is_rejected(self):
        with self.assertRaises(SystemExit):
            trigger("playbook-internal:10000", "secret", scheme="ftp")


if __name__ == "__main__":
    unittest.main()
