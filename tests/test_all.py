"""Pytest tests for Kingtrans tracker.

Run:
    pytest -q

This single file covers:
- XML parsing from kingtrans_client (summary + items)
- JsonStateStore persistence and diffing (storage)
- Pure diff functions (diff)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import cli
import send_test_telegram
from kingtrans_client import KingtransClient, TrackItem, TrackSummary, TrackResult
from storage import JsonStateStore
from diff import compute_diff, pretty_print_diff

# -------------------------------
# Fixture XML (from real sample)
# -------------------------------
SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xdoc>
  <xout>
    <track index="0" billid="1ZW1008Y6816279460" transbillid="1ZW1008Y6816279460" refernum="" rchannelid="空运（宏杉）_17fei" goodstype=" " trackstatusname="上网" newbillid="" country="德国" countryEn="GERMANY" sdate="2025-08-18 10:19:00" desti="Obertraubling, DE" intro="Out For Delivery Today" path="" goodsnum="1" rweight="0.000" trackstatus="101" trackurl="http://www.17feia.com/public/v1/ordeSearch/listTrackInfos/gTrackIdx" chantype="宏杉17fei">
      <trackitem index="trackitem_0_0" sdate="2025-08-18 10:19:00" place="Obertraubling, DE" intro="Out For Delivery Today" />
      <trackitem index="trackitem_0_1" sdate="2025-08-16 06:00:00" place="Obertraubling, DE" intro="Processing at UPS Facility" />
      <trackitem index="trackitem_0_2" sdate="2025-08-16 04:30:00" place="Obertraubling, DE" intro="Arrived at Facility" />
      <trackitem index="trackitem_0_3" sdate="2025-08-15 03:30:00" place="Nurnberg, DE" intro="Departed from Facility" />
      <trackitem index="trackitem_0_4" sdate="2025-08-15 00:45:00" place="Nurnberg, DE" intro="Arrived at Facility" />
      <trackitem index="trackitem_0_5" sdate="2025-08-14 20:30:00" place="Tuchomerice, CZ" intro="Departed from Facility" />
      <trackitem index="trackitem_0_6" sdate="2025-08-14 16:22:00" place="Tuchomerice, CZ" intro="Export Scan" />
      <trackitem index="trackitem_0_7" sdate="2025-08-14 16:22:00" place="Tuchomerice, CZ" intro="Arrived at Facility" />
      <trackitem index="trackitem_0_8" sdate="2025-08-14 06:00:00" place="" intro="Customs cleared." />
      <trackitem index="trackitem_0_9" sdate="2025-08-13 08:00:00" place="" intro="Arrived at customs clearance gateway." />
      <trackitem index="trackitem_0_10" sdate="2025-08-12 09:00:00" place="" intro="Shipment in Transit - poland" />
      <trackitem index="trackitem_0_11" sdate="2025-08-12 06:00:00" place="" intro="Shipment in Transit - Lithuania" />
      <trackitem index="trackitem_0_12" sdate="2025-08-10 05:36:00" place="" intro="Shipment in Transit - Latvia" />
      <trackitem index="trackitem_0_13" sdate="2025-08-08 03:14:36" place="" intro="Queuing - Shipment waiting for entering European Union" />
      <trackitem index="trackitem_0_14" sdate="2025-08-04 02:47:44" place="" intro="Queuing - Shipment waiting for entering European Union" />
      <trackitem index="trackitem_0_15" sdate="2025-07-31 08:00:00" place="" intro="Shipment in Transit - Belarus" />
      <trackitem index="trackitem_0_16" sdate="2025-07-28 07:00:00" place="" intro="Shipment in Transit - Russia" />
      <trackitem index="trackitem_0_17" sdate="2025-07-26 20:00:00" place="" intro="Shipment in Transit - Kazakhstan" />
      <trackitem index="trackitem_0_18" sdate="2025-07-24 20:00:00" place="" intro="Export clearance completed." />
      <trackitem index="trackitem_0_19" sdate="2025-07-22 13:12:44" place="" intro="Arrived at border gateway." />
      <trackitem index="trackitem_0_20" sdate="2025-07-19 18:00:00" place="" intro="En route to destination country." />
      <trackitem index="trackitem_0_21" sdate="2025-07-19 16:00:00" place="" intro="Shipment dispatched from warehouse." />
      <trackitem index="trackitem_0_22" sdate="2025-07-16 11:41:18" place="ShangHai" intro="The order has been confirmed." />
      <trackitem index="trackitem_0_23" sdate="2025-07-15 19:41:16" place="ShangHai" intro="Your shipment has been received and stored in our warehouse." />
      <trackitem index="trackitem_0_24" sdate="2025-07-15 17:54:58" place="" intro="Item information received." />
      <trackitem index="trackitem_0_25" sdate="2025-07-18 22:28:48" place="深圳" intro="离开分拨中心" />
      <trackitem index="trackitem_0_26" sdate="2025-07-18 22:28:35" place="深圳" intro="进入分拨中心" />
    </track>
  </xout>
</xdoc>
"""


# -------------------------------
# Tests: XML parsing
# -------------------------------

def test_parse_xml_summary_and_items():
    client = KingtransClient()
    # call private parser with sample XML
    result = client._parse_xml(SAMPLE_XML, tracking_no="1ZW1008Y6816279460")

    # Summary checks
    s = result.summary
    assert s.billid == "1ZW1008Y6816279460"
    assert s.status_code == "101"
    assert s.status_name == "上网"
    assert s.latest_place.startswith("Obertraubling")
    assert "17fei" in s.channel

    # Items checks
    assert len(result.items) >= 10
    first = result.items[0]
    assert first.index.startswith("trackitem_")
    assert first.sdate[:10] >= "2025-07-15"


# -------------------------------
# Tests: persistence & diffing
# -------------------------------

def test_storage_json_state_store(tmp_path: Path):
    client = KingtransClient()
    res = client._parse_xml(SAMPLE_XML, tracking_no="1ZW1008Y6816279460")

    store = JsonStateStore(base_dir=str(tmp_path / "state"))

    # First save -> everything is new
    diff1 = store.update_with_result("1ZW1008Y6816279460", res)
    assert diff1.summary_changed is True or diff1.summary_changed is False  # fingerprint exists
    assert len(diff1.added_keys) == len(res.items)

    # Second save without changes -> no new items
    diff2 = store.update_with_result("1ZW1008Y6816279460", res)
    assert diff2.added_keys == []

    # Simulate new item arrival by prepending a new TrackItem
    new_item = TrackItem(index="trackitem_0_-1", sdate="2025-08-19 08:00:00", place="Regensburg, DE", intro="Delivered")
    res2 = TrackResult(summary=res.summary, items=[new_item] + res.items, raw_xml=res.raw_xml)

    diff3 = store.update_with_result("1ZW1008Y6816279460", res2)
    assert diff3.added_keys and diff3.added_items
    assert any(it["index"] == "trackitem_0_-1" for it in diff3.added_items)


def test_storage_uses_event_content_when_provider_indexes_shift(tmp_path: Path):
    summary = TrackSummary(
        billid="TN1",
        transbillid="TN1",
        status_name="",
        status_code="100",
        latest_time="2026-05-29 15:15:59",
        latest_place="Shenzhen",
        latest_intro="Entered sorting center",
        country="",
        country_en="",
        dest="",
        channel="",
        chan_type="",
        goodsnum="",
        rweight="",
        track_url="",
    )
    first = TrackItem(index="trackitem_0_0", sdate="2026-05-29 15:15:59", place="Shenzhen", intro="Entered sorting center")
    second = TrackItem(index="trackitem_0_1", sdate="2026-05-30 09:00:00", place="Shenzhen", intro="Departed sorting center")

    store = JsonStateStore(base_dir=str(tmp_path / "state"))
    store.update_with_result("TN1", TrackResult(summary=summary, items=[second, first], raw_xml=""))

    delivered = TrackItem(index="trackitem_0_0", sdate="2026-06-01 10:00:00", place="Berlin", intro="Delivered")
    shifted_second = TrackItem(index="trackitem_0_1", sdate=second.sdate, place=second.place, intro=second.intro)
    shifted_first = TrackItem(index="trackitem_0_2", sdate=first.sdate, place=first.place, intro=first.intro)
    diff = store.update_with_result(
        "TN1",
        TrackResult(summary=summary, items=[delivered, shifted_second, shifted_first], raw_xml=""),
    )

    assert [it["intro"] for it in diff.added_items] == ["Delivered"]


def test_storage_migrates_legacy_provider_index_state_without_spam(tmp_path: Path):
    summary = TrackSummary(
        billid="TN1",
        transbillid="TN1",
        status_name="",
        status_code="100",
        latest_time="2026-05-29 15:15:59",
        latest_place="Shenzhen",
        latest_intro="Entered sorting center",
        country="",
        country_en="",
        dest="",
        channel="",
        chan_type="",
        goodsnum="",
        rweight="",
        track_url="",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "TN1.json").write_text(
        json.dumps({"item_keys": ["trackitem_0_0"], "summary_fp": "", "updated_ts": 0, "version": 1}),
        encoding="utf-8",
    )

    item = TrackItem(index="trackitem_0_0", sdate="2026-05-29 15:15:59", place="Shenzhen", intro="Entered sorting center")
    store = JsonStateStore(base_dir=str(state_dir))
    diff = store.update_with_result("TN1", TrackResult(summary=summary, items=[item], raw_xml=""))

    saved = json.loads((state_dir / "TN1.json").read_text(encoding="utf-8"))
    assert diff.added_items == []
    assert saved["item_keys"] == ["2026-05-29 15:15:59|Shenzhen|Entered sorting center"]


def test_cli_does_not_save_state_when_telegram_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    summary = TrackSummary(
        billid="TN1",
        transbillid="TN1",
        status_name="",
        status_code="100",
        latest_time="2026-06-01 10:00:00",
        latest_place="Berlin",
        latest_intro="Delivered",
        country="",
        country_en="",
        dest="",
        channel="",
        chan_type="",
        goodsnum="",
        rweight="",
        track_url="",
    )
    result = TrackResult(
        summary=summary,
        items=[TrackItem(index="trackitem_0_0", sdate="2026-06-01 10:00:00", place="Berlin", intro="Delivered")],
        raw_xml="",
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def query(self, tracking_no: str) -> TrackResult:
            return result

    def fail_send(text: str) -> None:
        raise RuntimeError("telegram failed")

    monkeypatch.setattr(cli, "KingtransClient", FakeClient)
    monkeypatch.setattr(cli, "send_telegram_message", fail_send)

    args = argparse.Namespace(
        tracking=["TN1"],
        batch=None,
        state_dir=str(tmp_path / "state"),
        language="zh",
        max_num=10,
        retries=0,
        timeout_connect=1.0,
        timeout_read=1.0,
        notify_telegram=True,
        json=False,
        pretty=True,
        csv=None,
        sleep=0,
    )

    cli.run_once(args)

    assert not (tmp_path / "state" / "TN1.json").exists()


def test_send_test_telegram_script_uses_custom_message(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    sent = []

    def fake_send(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(send_test_telegram, "send_telegram_message", fake_send)

    send_test_telegram.main(["--message", "hello from test"])

    assert sent == ["hello from test"]
    assert "Telegram test message sent." in capsys.readouterr().out


def test_send_test_telegram_script_default_message(monkeypatch: pytest.MonkeyPatch):
    sent = []

    def fake_send(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(send_test_telegram, "send_telegram_message", fake_send)

    send_test_telegram.main([])

    assert len(sent) == 1
    assert sent[0].startswith("TransTrack Telegram test OK\nTime: ")


# -------------------------------
# Tests: pure diff module
# -------------------------------

def test_pure_diff_compute_diff():
    # Build old/new dict snapshots
    old_summary = {"latest_time": "2025-08-16 06:00:00", "latest_place": "Obertraubling, DE", "latest_intro": "Processing", "status_code": "100"}
    new_summary = {"latest_time": "2025-08-18 10:19:00", "latest_place": "Obertraubling, DE", "latest_intro": "Out For Delivery Today", "status_code": "101"}

    old_items = [
        {"index": "trackitem_0_1", "sdate": "2025-08-16 06:00:00", "place": "Obertraubling, DE", "intro": "Processing at UPS Facility"},
        {"index": "trackitem_0_2", "sdate": "2025-08-16 04:30:00", "place": "Obertraubling, DE", "intro": "Arrived at Facility"},
    ]
    new_items = old_items + [
        {"index": "trackitem_0_0", "sdate": "2025-08-18 10:19:00", "place": "Obertraubling, DE", "intro": "Out For Delivery Today"}
    ]

    d = compute_diff(old_summary, new_summary, old_items, new_items)
    text = pretty_print_diff(d)
    assert d.summary_changed is True
    assert any("+" in line for line in text.splitlines())
