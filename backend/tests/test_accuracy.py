"""The accuracy report replays history without look-ahead and reports honestly."""
import pandas as pd
import pytest
from finance_fixture import build

from app import accuracy


@pytest.fixture(scope="module")
def data():
    fd, mrr, _ = build()
    return fd, mrr


def test_forecast_backtest_uses_only_the_past(data, monkeypatch):
    _, mrr = data
    seen = []
    real = accuracy.fc.forecast

    def spy(past, horizon=12):
        seen.append(past["month"].max())
        return real(past, horizon)

    monkeypatch.setattr(accuracy.fc, "forecast", spy)
    bt = accuracy.forecast_backtest(mrr)
    for r in bt["rows"]:
        assert pd.Timestamp(r["made_in"]) < pd.Timestamp(r["month"])
    assert max(seen) < mrr["month"].max()  # never saw the final month while predicting it
    assert {1, 3} <= set(bt["summary"])


def test_forecast_is_reasonable_on_demo(data):
    _, mrr = data
    s = accuracy.forecast_backtest(mrr)["summary"]
    assert s[1]["mape"] < 0.05 and s[3]["mape"] >= s[1]["mape"] * 0.5  # longer horizon isn't magically better
    assert 0 <= s[1]["band_coverage"] <= 1


def test_bridges_explain_the_gaps_exactly(data):
    fd, mrr = data
    ec = accuracy.explanation_coverage(fd, mrr)
    assert ec["months"] and ec["share"] > 0.99  # driver and person-level bridges are exact by construction


def test_early_warnings_are_judged_on_later_months_only(data):
    fd, mrr = data
    ew = accuracy.early_warnings(fd, mrr)
    for c in ew["calls"]:
        assert c["months_after"] >= 1
    by_line = {c["line"]: c for c in ew["calls"]}
    # planted: software subscriptions step up and stay up -> a right call
    assert by_line["Software subscriptions"]["right"]
    # planted: events spend is lumpy -> Fred's August run-rate call didn't hold, and the report must say so
    assert not by_line["Events & sponsorships"]["right"]
    assert ew["right"] < ew["n"]
    # hosting drift is only classified in the latest month: nothing later to judge it on yet
    assert "Hosting (cloud)" not in by_line


def test_cut_hides_future_books(data):
    fd, _ = data
    m = fd.closed_months[-3]
    cut = accuracy._cut(fd, m)
    assert cut.actual["month"].max() == m and cut.closed_months[-1] == m


def test_billing_and_books_agree_on_demo(data):
    fd, mrr = data
    da = accuracy.data_agreement(fd, mrr)
    assert da["rows"] and da["max_abs_diff"] < 0.02


def test_verdict_is_plain_language(data):
    fd, mrr = data

    class A:
        churn_model = lead_model = None

    class WS:
        def mrr_frame(self):
            return mrr

    rep = accuracy.report(WS(), A(), fd)
    checks = {v["check"] for v in rep["verdict"]}
    assert {"Forecast", "Explanations", "Data agreement"} <= checks
    assert all(v["state"] in ("good", "watch", "bad") for v in rep["verdict"])
