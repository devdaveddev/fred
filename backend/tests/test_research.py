"""Pre-trained model jobs must recover structure the generator planted, without being told it."""
from datetime import date

import pandas as pd
import pytest

from app.ml.embeddings import embed
from app.research import segments, voc
from app.seed import generate


@pytest.fixture(scope="module")
def data():
    g = generate(today=date(2026, 11, 12))
    acc = pd.DataFrame(g.accounts).rename(columns={"id": "account_id"})
    leads = pd.DataFrame(g.leads).rename(columns={"id": "lead_id"})
    leads["created_at"] = pd.to_datetime(leads["created_at"], utc=True).dt.tz_localize(None)
    mrr = pd.DataFrame([(r["a"], r["m"], r["v"]) for r in g.mrr], columns=["account_id", "month", "mrr"])
    mrr["month"] = pd.to_datetime(mrr["month"])
    fb = pd.DataFrame(g.feedback).rename(columns={"a": "account_id", "ts": "created_at"})
    fb["created_at"] = pd.to_datetime(fb["created_at"]).dt.tz_localize(None)
    return acc, leads, mrr, fb, mrr["month"].max()


def test_segments_match_hidden_niches(data):
    acc, leads, mrr, _, as_of = data
    r = segments.discover(acc, mrr, leads, as_of, embed)
    niche = dict(zip(acc["account_id"], acc["niche"]))
    by_seg = pd.Series({a: niche[a] for a in r["assignments"]}).groupby(pd.Series(r["assignments"]))
    purity = by_seg.apply(lambda s: s.value_counts().iloc[0] / len(s))
    assert (purity >= 0.9).mean() >= 0.7  # most discovered segments are a single hidden niche
    best = [a for a, c in r["assignments"].items() if c == r["best"]["segment_id"]]
    worst = [a for a, c in r["assignments"].items() if c == r["worst"]["segment_id"]]
    from app.seed_text import NICHES
    quality = {n: q for v in NICHES.values() for n, q, _, _ in v}
    assert pd.Series([quality[niche[a]] for a in best]).mode()[0] == "great"
    assert pd.Series([quality[niche[a]] for a in worst]).mode()[0] == "poor"
    assert len(r["lookalikes"]) > 0


def test_zero_shot_tagging_accuracy(data):
    *_, fb, _ = data
    ex = fb[fb["kind"] == "exit_survey"]
    labels, _ = voc.tag(ex["body"].tolist(), embed)
    assert (pd.Series(labels).to_numpy() == ex["theme"].to_numpy()).mean() >= 0.85


def test_voice_of_customer_ranks_planted_top_reason(data):
    _, _, mrr, fb, as_of = data
    v = voc.analyse(fb, mrr, as_of, embed)
    assert v["churn_reasons"][0]["theme"] == "missing_integration"
    assert v["nps"] is not None and -100 <= v["nps"]["score"] <= 100
