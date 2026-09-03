import json
import unittest
from datetime import datetime
from unittest import mock

from signal_sim.events import Event
from signal_sim.sources.altdata import QuiverSource, live


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self._body


class QuiverExpandedLiveTests(unittest.TestCase):
    def _run(self, payloads, datasets=None):
        requests = []

        def urlopen(request):
            requests.append(request)
            dataset = request.full_url.rsplit("/", 1)[-1]
            return _Response(payloads[dataset])

        with mock.patch("signal_sim.secrets.read_env", return_value="test-key"), mock.patch(
            "signal_sim.sources.altdata.urllib.request.urlopen", side_effect=urlopen
        ):
            events = live(datasets)
        return events, requests

    def test_missing_key_stops_before_http(self):
        with mock.patch("signal_sim.sources.altdata.read_env", return_value=None), mock.patch(
            "signal_sim.sources.altdata.urllib.request.urlopen"
        ) as mock_urlopen:
            with self.assertRaisesRegex(NotImplementedError, r"no verified key \+ terms"):
                live()
        mock_urlopen.assert_not_called()

    def test_default_live_maps_real_congress_json_and_headers(self):
        events, requests = self._run(
            {
                "congresstrading": [
                    {
                        "Amount": 1001,
                        "BioGuideID": "X000001",
                        "Description": "NVIDIA Corporation",
                        "House": "Representatives",
                        "Range": "$1,001 - $15,000",
                        "ReportDate": "2026-08-10",
                        "Representative": "Alex Example",
                        "Ticker": "NVDA",
                        "Transaction": "Purchase",
                        "TransactionDate": "2026-07-15",
                    }
                ]
            }
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["ticker"], "NVDA")
        self.assertEqual(event["person"], "Alex Example")
        self.assertEqual(event["transaction"], "purchase")
        self.assertEqual(event["amount_range_usd"], [1001.0, 15000.0])
        self.assertEqual(event["kind"], "congress_trade")
        self.assertEqual(event["source"], "quiver")
        self.assertEqual(event["rank_at"], event["filed_at"])
        for field in ("occurred_at", "filed_at", "observed_at"):
            self.assertIsNotNone(datetime.fromisoformat(event[field]).utcoffset())
        self.assertGreaterEqual(
            datetime.fromisoformat(event["observed_at"]),
            datetime.fromisoformat(event["filed_at"]),
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0].full_url,
            "https://api.quiverquant.com/beta/live/congresstrading",
        )
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer test-key")
        self.assertEqual(
            requests[0].get_header("User-agent"), "signal-sim-paper/0.1"
        )

    def test_congress_filters_universe_unknown_transactions_and_uses_amount(self):
        events, _requests = self._run(
            {
                "congresstrading": [
                    {
                        "Amount": "$5,000",
                        "ReportDate": "2026-08-10T00:00:00Z",
                        "Representative": "Included",
                        "Ticker": "XLE",
                        "Transaction": "Sale",
                        "TransactionDate": "2026-08-01T00:00:00Z",
                    },
                    {
                        "Range": "$1,001 - $15,000",
                        "ReportDate": "2026-08-10T00:00:00Z",
                        "Representative": "Outside",
                        "Ticker": "AAPL",
                        "Transaction": "Purchase",
                        "TransactionDate": "2026-08-01T00:00:00Z",
                    },
                    {
                        "Range": "$1,001 - $15,000",
                        "ReportDate": "2026-08-10T00:00:00Z",
                        "Representative": "Unknown action",
                        "Ticker": "DIS",
                        "Transaction": "Exchange",
                        "TransactionDate": "2026-08-01T00:00:00Z",
                    },
                ]
            }
        )

        self.assertEqual([event["ticker"] for event in events], ["XLE"])
        self.assertEqual(events[0]["transaction"], "sale")
        self.assertEqual(events[0]["amount_range_usd"], [5000.0, 5000.0])

    def test_all_datasets_map_and_filter_without_network(self):
        datasets = ("congresstrading", "insiders", "govcontracts", "quivernews")
        payloads = {
            "congresstrading": [],
            "insiders": [
                {
                    "Ticker": "DIS",
                    "Date": "2026-09-01",
                    "Name": "Pat Director",
                    "AcquiredDisposedCode": "A",
                },
                {
                    "Ticker": "AAPL",
                    "Date": "2026-09-01",
                    "Name": "Outside Director",
                    "AcquiredDisposedCode": "D",
                },
                {
                    "Ticker": "NVDA",
                    "Date": "2026-09-02",
                    "Name": "Sam Officer",
                    "AcquiredDisposedCode": "D",
                },
            ],
            "govcontracts": [
                {
                    "Ticker": "XLE",
                    "Date": "2026-08-20",
                    "AwardDate": "2026-08-21",
                    "Agency": "Energy Department",
                    "Description": "Fuel supply award",
                },
                {
                    "Ticker": "MSFT",
                    "Date": "2026-08-20",
                    "AwardDate": "2026-08-21",
                },
            ],
            "quivernews": [
                {
                    "Ticker": "NVDA",
                    "DateTime": "2026-09-02T14:30:00Z",
                    "Headline": "NVIDIA launches a new product",
                    "URL": "https://example.test/nvda",
                },
                {
                    "Ticker": "TSLA",
                    "DateTime": "2026-09-02T14:30:00Z",
                    "Headline": "Outside universe",
                },
            ],
        }

        events, requests = self._run(payloads, datasets)

        self.assertEqual(len(requests), 4)
        self.assertEqual(
            [request.full_url.rsplit("/", 1)[-1] for request in requests],
            list(datasets),
        )
        for request in requests:
            self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
            self.assertEqual(
                request.get_header("User-agent"), "signal-sim-paper/0.1"
            )

        insiders = [event for event in events if isinstance(event, dict)]
        self.assertEqual(
            {event["transaction"] for event in insiders}, {"purchase", "sale"}
        )
        insider = next(event for event in insiders if event["transaction"] == "purchase")
        self.assertEqual(insider["kind"], "insider")
        self.assertEqual(insider["ticker"], "DIS")
        self.assertEqual(insider["person"], "Pat Director")
        self.assertEqual(insider["transaction"], "purchase")
        self.assertEqual(insider["filed_at"], insider["occurred_at"])

        contracts = [
            event for event in events if isinstance(event, Event) and event.kind == "gov_contract"
        ]
        self.assertEqual(len(contracts), 1)
        self.assertEqual(contracts[0].ticker, "XLE")
        self.assertEqual(
            contracts[0].filed_at,
            datetime.fromisoformat("2026-08-21T00:00:00+00:00"),
        )

        news = [event for event in events if isinstance(event, Event) and event.kind == "news"]
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0].ticker, "NVDA")
        self.assertEqual(news[0].source, "quiver")
        self.assertEqual(news[0].headline, "NVIDIA launches a new product")

    def test_class_live_remains_congress_choke(self):
        with mock.patch("signal_sim.sources.altdata.live", return_value=[]) as mock_live:
            self.assertEqual(QuiverSource().live(), [])
        mock_live.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
