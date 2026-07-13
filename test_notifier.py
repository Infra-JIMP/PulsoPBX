import unittest

from notifier import WhatsAppNotifier


class WhatsAppNotifierTests(unittest.TestCase):
    def test_graph_api_version_is_used_in_messages_url(self):
        notifier = WhatsAppNotifier(
            token="token",
            phone_number_id="123456",
            graph_api_version="v25.0",
            template_name="ramal_alerta",
            use_template=True,
            recipients=["5511999999999"],
        )
        self.assertEqual(
            notifier._url,
            "https://graph.facebook.com/v25.0/123456/messages",
        )

    def test_invalid_graph_api_version_is_rejected(self):
        with self.assertRaises(ValueError):
            WhatsAppNotifier(
                token="token",
                phone_number_id="123456",
                graph_api_version="latest/unsafe",
                template_name="ramal_alerta",
                use_template=True,
                recipients=["5511999999999"],
            )


if __name__ == "__main__":
    unittest.main()
