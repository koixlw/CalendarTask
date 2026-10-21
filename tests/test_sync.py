"""同步模块测试 — 不联网, 用本地 .ics 夹具。

覆盖:
  1. iCal <-> CalendarItem 双向转换 (round-trip)
  2. RRULE 映射 (daily/weekly/monthly + interval + until)
  3. VALARM -> type=2 识别
  4. 私有扩展 (X-PYCAL-COLOR / TYPE / COUNTDOWN)
  5. 账号 CRUD + 密码混淆可逆
  6. sync_links 记录
  7. 冲突检测 (三方对比 hash)

跑:  python tests/test_sync.py
"""

from __future__ import annotations
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = ""):
    if cond:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(f"{name} {extra}")
        print(f"  [FAIL] {name} {extra}")


# ============================================================================
def test_roundtrip():
    print("\n=== 1. iCal 双向转换 round-trip ===")
    from icalendar import Event
    from pycal.db.models import CalendarItem, RepeatRule, Countdown, Reminder
    from pycal.sync.ical_convert import (
        item_to_ical, ical_to_item, ical_to_repeat_rule, ical_to_countdown,
        content_hash,
    )

    item = CalendarItem(
        item_id="test-uid-1",
        date="2026-10-21",
        type=0,
        title="开会讨论",
        content="会议室 B\n准备材料",
        color_ref=0xF12424,
        is_completed=0,
        priority=2,
    )
    ev = item_to_ical(item)
    back = ical_to_item(ev)

    check("UID 保留", back.item_id == "cdav_test-uid-1",
          f"got {back.item_id}")
    check("SUMMARY 保留", back.title == "开会讨论", f"got {back.title}")
    check("DESCRIPTION 保留", "准备材料" in back.content)
    check("DTSTART -> date", back.date == "2026-10-21", f"got {back.date}")
    check("X-PYCAL-COLOR 保留", back.color_ref == 0xF12424,
          f"got {back.color_ref}")
    check("X-PYCAL-PRIORITY 保留", back.priority == 2, f"got {back.priority}")
    check("type 保留", back.type == 0, f"got {back.type}")


def test_repeat_rule():
    print("\n=== 2. RRULE 映射 ===")
    from pycal.db.models import CalendarItem, RepeatRule
    from pycal.sync.ical_convert import (
        item_to_ical, ical_to_repeat_rule,
    )

    item = CalendarItem(item_id="rep-1", date="2026-01-01", type=1,
                        title="每天跑步")
    rule = RepeatRule(item_id="rep-1", freq="daily", interval_n=2,
                      until_date="2026-12-31")
    ev = item_to_ical(item, repeat_rule=rule)
    back = ical_to_repeat_rule(ev, "rep-1")

    check("RRULE 存在", ev.get("RRULE") is not None)
    check("freq=DAILY", back and back.freq == "daily", f"got {back.freq if back else None}")
    check("INTERVAL=2", back and back.interval_n == 2,
          f"got {back.interval_n if back else None}")
    check("UNTIL 解析", back and back.until_date == "2026-12-31",
          f"got {back.until_date if back else None}")

    # monthly
    item2 = CalendarItem(item_id="rep-2", date="2026-01-15", type=1, title="月度")
    rule2 = RepeatRule(item_id="rep-2", freq="monthly", interval_n=1)
    ev2 = item_to_ical(item2, repeat_rule=rule2)
    back2 = ical_to_repeat_rule(ev2, "rep-2")
    check("freq=MONTHLY", back2 and back2.freq == "monthly",
          f"got {back2.freq if back2 else None}")


def test_alarm_and_type_inference():
    print("\n=== 3. VALARM / type 推断 ===")
    from icalendar import Event
    from pycal.db.models import CalendarItem, Reminder
    from pycal.sync.ical_convert import item_to_ical, ical_to_item

    # 提醒 -> VALARM
    item = CalendarItem(item_id="rem-1", date="2026-10-21", type=2,
                        title="交报告")
    rm = Reminder(item_id="rem-1", remind_at=datetime(2026, 10, 21, 9, 0))
    ev = item_to_ical(item, reminder=rm)
    check("VALARM 已附加",
          any(getattr(c, "name", "") == "VALARM" for c in ev.subcomponents))

    # 无 X-TYPE 时靠 VALARM 推断
    ev2 = Event()
    ev2.add("uid", "infer-1")
    ev2.add("summary", "推断测试")
    ev2.add("dtstart", date(2026, 10, 22))
    from icalendar import Alarm
    al = Alarm()
    al.add("action", "DISPLAY")
    al.add("description", "x")
    ev2.add_component(al)
    back = ical_to_item(ev2)
    check("VALARM 推断 type=2", back and back.type == 2,
          f"got {back.type if back else None}")

    # 靠 RRULE 推断 type=1
    ev3 = Event()
    ev3.add("uid", "infer-2")
    ev3.add("summary", "重复推断")
    ev3.add("dtstart", date(2026, 10, 22))
    ev3.add("rrule", {"FREQ": "WEEKLY"})
    back3 = ical_to_item(ev3)
    check("RRULE 推断 type=1", back3 and back3.type == 1,
          f"got {back3.type if back3 else None}")


def test_countdown():
    print("\n=== 4. 倒数日映射 ===")
    from pycal.db.models import CalendarItem, Countdown
    from pycal.sync.ical_convert import (
        item_to_ical, ical_to_item, ical_to_countdown,
    )

    item = CalendarItem(item_id="cd-1", date="2026-12-25", type=3,
                        title="圣诞节")
    cd = Countdown(item_id="cd-1", target_date="2026-12-25", repeat_yearly=1)
    ev = item_to_ical(item, countdown=cd)
    back_item = ical_to_item(ev)
    back_cd = ical_to_countdown(ev, "cd-1")

    check("TRANSP=TRANSPARENT", str(ev.get("TRANSP")) == "TRANSPARENT")
    check("type=3 保留", back_item and back_item.type == 3)
    check("target_date 保留", back_cd and back_cd.target_date == "2026-12-25",
          f"got {back_cd.target_date if back_cd else None}")
    check("repeat_yearly 保留", back_cd and back_cd.repeat_yearly == 1)


def test_accounts_crud():
    print("\n=== 5. 账号 CRUD + 密码混淆 ===")
    from pycal.db.connection import _make_conn
    from pycal.sync.accounts import (
        create_account, list_accounts, get_account, update_account,
        delete_account, set_link, get_link, links_for_account,
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        con = _make_conn(tmp)
        aid = create_account(
            "google",
            "https://apidata.googleusercontent.com/caldav/v2/",
            "user@gmail.com", "abcd efgh ijkl mnop",
            display_name="我的 Google", con=con,
        )
        check("创建账号", aid > 0)

        accs = list_accounts(con)
        check("列出账号", len(accs) == 1, f"got {len(accs)}")

        acc = get_account(aid, con)
        check("密码可逆解密", acc.password == "abcd efgh ijkl mnop",
              f"got {acc.password!r}")
        check("provider 正确", acc.provider == "google")

        # 确认 DB 里不是明文
        row = con.execute(
            "SELECT password_enc FROM sync_accounts WHERE account_id = ?",
            (aid,),
        ).fetchone()
        check("DB 里非明文", "abcd efgh" not in (row["password_enc"] or ""),
              f"got {row['password_enc']!r}")

        update_account(aid, display_name="改过的名字", con=con)
        acc2 = get_account(aid, con)
        check("更新生效", acc2.display_name == "改过的名字")

        # sync_links
        set_link("item-a", aid, "/cal/item-a.ics", "etag123",
                 "hash_a", con=con)
        link = get_link("item-a", aid, con)
        check("link 写入", link is not None and link["remote_href"] == "/cal/item-a.ics")
        check("link etag", link["remote_etag"] == "etag123")
        links = links_for_account(aid, con)
        check("links_for_account", "item-a" in links)

        delete_account(aid, con)
        check("删除账号", len(list_accounts(con)) == 0)
        check("级联删 link", len(links_for_account(aid, con)) == 0)
    finally:
        _cleanup(con, tmp)


def test_conflict_detection():
    print("\n=== 6. 冲突检测 (三方 hash 对比) ===")
    from pycal.db.connection import _make_conn
    from pycal.db.models import CalendarItem
    from pycal.db.repository import upsert_item
    from pycal.sync.accounts import create_account, set_link
    from pycal.sync.ical_convert import content_hash, item_to_ical, ical_to_item

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        con = _make_conn(tmp)
        aid = create_account("outlook", "https://outlook.office365.com/",
                             "a@b.com", "pw", con=con)

        item = CalendarItem(item_id="cf-1", date="2026-10-21",
                            title="原始", content="x")
        upsert_item(con, item)
        base_hash = content_hash(item)
        set_link("cf-1", aid, "/cal/cf-1.ics", "e1", base_hash, con=con)

        # 情况 A: 都没改 -> hash 相同
        same_local = CalendarItem(item_id="cf-1", date="2026-10-21",
                                  title="原始", content="x")
        check("A 未改动 = hash 相同", content_hash(same_local) == base_hash)

        # 情况 B: 本地改
        local_changed = CalendarItem(item_id="cf-1", date="2026-10-21",
                                     title="本地改", content="x")
        check("B 本地改 = hash 不同", content_hash(local_changed) != base_hash)

        # 情况 C: 远端改 (走 iCal 往返后 hash 不同)
        remote_orig = CalendarItem(item_id="cf-1", date="2026-10-21",
                                   title="原始", content="x")
        remote_changed_item = CalendarItem(item_id="cf-1", date="2026-10-21",
                                           title="远端改", content="x")
        ev = item_to_ical(remote_changed_item)
        remote_back = ical_to_item(ev)
        check("C 远端改 = hash 不同", content_hash(remote_back) != base_hash,
              f"{content_hash(remote_back)} vs {base_hash}")

        # 双向都是"未改动"时才算无冲突 (用未改的远端副本)
        remote_unchanged = ical_to_item(item_to_ical(remote_orig))
        check("两边都未改 = 无冲突",
              content_hash(same_local) == base_hash
              and content_hash(remote_unchanged) == base_hash,
              f"local={content_hash(same_local)[:8]} "
              f"remote={content_hash(remote_unchanged)[:8]} "
              f"base={base_hash[:8]}")
    finally:
        _cleanup(con, tmp)


def test_sync_import():
    print("\n=== 7. sync 包导入完整性 ===")
    import importlib
    for mod in [
        "pycal.sync", "pycal.sync.presets", "pycal.sync.accounts",
        "pycal.sync.ical_convert", "pycal.sync.client", "pycal.sync.engine",
        "pycal.sync.__main__",
    ]:
        try:
            importlib.import_module(mod)
            check(f"import {mod}", True)
        except Exception as e:
            check(f"import {mod}", False, str(e)[:100])

    from pycal.sync.presets import PRESETS, get_preset
    check("Google preset 存在", "google" in PRESETS)
    check("iCloud preset 存在", "icloud" in PRESETS)
    check("Outlook preset 存在", "outlook" in PRESETS)
    check("Google URL 正确",
          "googleusercontent.com" in get_preset("google").base_url)
    check("iCloud URL 正确",
          get_preset("icloud").base_url == "https://caldav.icloud.com/")


def _cleanup(con, path: str):
    """Windows 下 SQLite WAL 会锁文件, 先关连接再删。"""
    try:
        con.close()
    except Exception:
        pass
    import gc, time
    gc.collect()
    for _ in range(3):
        try:
            os.unlink(path)
            return
        except PermissionError:
            time.sleep(0.1)
    for suffix in ("-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


def main() -> int:
    print("=" * 70)
    print("PyCal 同步模块测试")
    print("=" * 70)
    test_roundtrip()
    test_repeat_rule()
    test_alarm_and_type_inference()
    test_countdown()
    test_accounts_crud()
    test_conflict_detection()
    test_sync_import()

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("\n失败项:")
        for f in FAIL:
            print(f"  ✗ {f}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())