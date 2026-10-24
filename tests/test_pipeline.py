import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.classifier import PostingModel, classify
from app.demo import demo_inbox
from app.inbox import normalize, parse_inbox
from app.store import Store

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def email(body='Your application has been received.', days=8, **kwargs):
    return normalize({'from':'Hiring <jobs@example.com>', 'subject':'Application update',
                      'body':body, 'date':(NOW - timedelta(days=days)).isoformat(),
                      'company':'Example', 'role':'Engineer', **kwargs})


class ClassifierTests(unittest.TestCase):
    def test_five_labels(self):
        for body, label in [('Application received.', 'applied'), ('Please choose an interview slot.', 'interview'),
                            ('Unfortunately, we are moving forward with other candidates.', 'rejected'),
                            ('We are pleased to offer you the job.', 'offer'),
                            ('Pay the mandatory training fee.', 'scam')]:
            with self.subTest(label=label):
                self.assertEqual(classify(email(body))['label'], label)

    def test_scam_overrides_offer_and_has_evidence(self):
        result = classify(email('Your job offer is ready. Send your passport and bank account details.'))
        self.assertEqual(result['label'], 'scam')
        self.assertIn('passport', result['reasons'][0]['evidence'])

    def test_explicit_no_fee_is_not_a_scam(self):
        self.assertEqual(classify(email('We are pleased to offer you this job. We never ask you to pay a training fee.'))['label'], 'offer')

    def test_quoted_interview_does_not_override_rejection(self):
        self.assertEqual(classify(email('Unfortunately you were not selected.\nOn Monday, HR wrote:\nYour interview is tomorrow.'))['label'], 'rejected')

    def test_model_alone_cannot_flag(self):
        class Model:
            def predict(self, text):
                return .999
        self.assertEqual(classify(email(), Model())['label'], 'applied')


class ParserTests(unittest.TestCase):
    def test_json_array_and_wrapper(self):
        item = email()
        for raw in ([item], {'emails':[item]}):
            self.assertEqual(len(parse_inbox(json.dumps(raw).encode(), 'inbox.json')), 1)

    def test_invalid_imports(self):
        for raw in (b'{', b'[]', b'{}', b'[{"date":"not-a-date"}]'):
            with self.assertRaises(ValueError):
                parse_inbox(raw, 'inbox.json')
        with self.assertRaises(ValueError):
            parse_inbox(b'hello', 'inbox.txt')

    def test_normalizes_timezones(self):
        self.assertEqual(email(date='2026-10-24T17:30:00+05:30')['date'], NOW.isoformat())

    def test_mbox_multipart_and_headers(self):
        data = b'''From jobs@example.com Sat Oct 24 12:00:00 2026
From: Hiring <jobs@example.com>
Date: Sat, 24 Oct 2026 12:00:00 +0000
Subject: Interview invitation
Message-ID: <one@example.com>
X-Company: Example
X-Role: Engineer
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="boundary"

--boundary
Content-Type: text/plain; charset=utf-8

Please attend an interview.
--boundary
Content-Type: text/html; charset=utf-8

<p>Please attend an interview.</p>
--boundary--
'''
        result = parse_inbox(data, 'inbox.mbox')
        self.assertEqual(result[0]['company'], 'Example')
        self.assertEqual(result[0]['body'], 'Please attend an interview.')
        self.assertEqual(classify(result[0])['label'], 'interview')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_deduplication_classifies_once(self):
        with patch('app.store.classify', wraps=classify) as classifier:
            self.store.import_emails([email(), email()])
            result = self.store.import_emails([email()])
            self.assertEqual(classifier.call_count, 1)
        self.assertEqual(result, {'added':0,'skipped':1})

    def test_date_order_not_import_order_controls_stage(self):
        self.store.import_emails([email('Your interview is tomorrow.', days=1)])
        self.store.import_emails([email(days=15)])
        app = self.store.snapshot(NOW)['applications'][0]
        self.assertEqual(app['status'], 'interview')
        self.assertEqual(app['email_count'], 2)
        self.assertFalse(app['followup_due'])

    def test_seven_day_boundary(self):
        self.store.import_emails([email(days=7)])
        self.assertTrue(self.store.snapshot(NOW)['applications'][0]['followup_due'])
        self.assertFalse(self.store.snapshot(NOW - timedelta(seconds=1))['applications'][0]['followup_due'])

    def test_closed_and_flagged_never_due(self):
        for i, body in enumerate(('Your job offer is attached.', 'Unfortunately, you were not selected.', 'Pay the training fee.')):
            self.store.import_emails([email(body, days=20, application_id=str(i))])
        self.assertEqual(self.store.snapshot(NOW)['followup_count'], 0)

    def test_scam_flag_survives_later_email(self):
        self.store.import_emails([email('Pay the training fee.', days=10), email('Your interview is tomorrow.', days=1)])
        self.assertEqual(self.store.snapshot(NOW)['applications'][0]['status'], 'scam')

    def test_followup_resets_clock(self):
        old = datetime.now(timezone.utc) - timedelta(days=9)
        self.store.import_emails([email(date=old.isoformat())])
        app = self.store.snapshot()['applications'][0]
        self.assertTrue(app['followup_due'])
        self.store.log_followup(app['id'])
        self.assertFalse(self.store.snapshot()['applications'][0]['followup_due'])
        with self.assertRaises(ValueError):
            self.store.log_followup(app['id'])

    def test_reply_headers_link_without_metadata(self):
        parent = email(company='', role='', message_id='<parent@example.com>', days=10)
        child = email('Please attend an interview.', company='', role='', subject='Next steps', days=1,
                      in_reply_to='<parent@example.com>')
        self.store.import_emails([child, parent])
        self.assertEqual(len(self.store.snapshot(NOW)['applications']), 1)

    def test_different_roles_stay_separate(self):
        self.store.import_emails([email(role='Designer'), email(role='Engineer', body='Thanks for applying.')])
        self.assertEqual(len(self.store.snapshot(NOW)['applications']), 2)

    def test_unrelated_ambiguous_messages_stay_separate(self):
        self.store.import_emails([email(role='', company=''), email('Another application received.', role='', company='')])
        self.assertEqual(len(self.store.snapshot(NOW)['applications']), 2)

    def test_database_persists(self):
        self.store.import_emails([email()])
        self.assertEqual(Store(self.store.path).snapshot(NOW)['email_count'], 1)

    def test_transaction_rolls_back_classifier_failure(self):
        with patch('app.store.classify', side_effect=[classify(email()), RuntimeError('failed')]):
            with self.assertRaises(RuntimeError):
                self.store.import_emails([email(), email('Second email')])
        self.assertEqual(self.store.snapshot(NOW)['email_count'], 0)

    def test_twenty_email_demo(self):
        self.store.import_emails(parse_inbox(demo_inbox(), 'demo.json'))
        state = self.store.snapshot(datetime.now(timezone.utc).replace(hour=23, minute=59))
        self.assertEqual(state['email_count'], 20)
        self.assertEqual(len(state['applications']), 16)
        self.assertEqual(state['counts'], {'applied':6,'interview':4,'rejected':2,'offer':1,'scam':3})
        self.assertEqual(state['followup_count'], 5)


if __name__ == '__main__':
    unittest.main()
