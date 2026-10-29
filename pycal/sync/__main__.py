"""CLI 同步入口 — python -m pycal.sync

用法:
  python -m pycal.sync --list                     # 列出所有账号
  python -m pycal.sync --add-google                # 交互式添加 Google 账号
  python -m pycal.sync --add-icloud
  python -m pycal.sync --add-outlook
  python -m pycal.sync --add-custom URL USER PASS [NAME]
  python -m pycal.sync --test <account_id>         # 测试连接
  python -m pycal.sync --sync <account_id>         # 同步单个
  python -m pycal.sync --sync-all                  # 同步全部
  python -m pycal.sync --rm <account_id>           # 删除账号
  python -m pycal.sync --policy local_wins         # 冲突策略
"""

from __future__ import annotations
import argparse
import getpass
import sys
from datetime import date

from pycal.sync.accounts import (
    list_accounts, create_account, delete_account, get_account,
)
from pycal.sync.client import CaldavClient, CaldavError
from pycal.sync.engine import SyncEngine, sync_all
from pycal.sync.presets import get_preset
from pycal.console import setup_stdout


def cmd_list() -> int:
    accs = list_accounts()
    if not accs:
        print("没有配置任何同步账号。")
        print("用 --add-google / --add-icloud / --add-outlook 添加。")
        return 0
    print(f"{'ID':<4} {'服务商':<12} {'名称':<20} {'状态':<10} 上次同步")
    print("-" * 80)
    for a in accs:
        mark = "✓" if a.enabled else "✗"
        ro = " [只读]" if a.read_only else ""
        print(
            f"{a.account_id:<4} {a.provider:<12} "
            f"{a.display_name[:18]+ro:<20} {mark:<10} {a.last_sync or '—'}"
        )
    return 0


def cmd_add(provider: str, username: str = "", password: str = "") -> int:
    spec = get_preset(provider)
    print(f"=== 添加 {spec.label} ===")
    print(spec.notes)
    print()
    base = input("服务器 URL (回车用默认): ").strip() or spec.base_url
    user = (username or input("用户名: ")).strip()
    if not password:
        password = getpass.getpass("密码 / 应用专用密码: ")
    if not (user and password):
        print("用户名和密码不能为空")
        return 1
    acc_id = create_account(provider, base, user, password)
    print(f"✓ 已创建账号 #{acc_id}")
    # 立即测试
    acc = get_account(acc_id)
    if acc:
        print("\n正在测试连接 …")
        try:
            with CaldavClient(acc) as c:
                cals = c.list_calendars()
                if cals:
                    print(f"✓ 连接成功! 发现 {len(cals)} 个日历:")
                    for name, href in cals:
                        print(f"    · {name}")
                        print(f"      {href}")
                else:
                    print("✓ 连接成功, 但没有可用日历")
        except CaldavError as e:
            print(f"✗ {e.message}")
            if e.hint:
                print(f"  提示: {e.hint}")
    return 0


def cmd_test(account_id: int) -> int:
    acc = get_account(account_id)
    if not acc:
        print(f"账号 #{account_id} 不存在")
        return 1
    print(f"测试 {acc.display_name} ({acc.base_url}) …")
    try:
        with CaldavClient(acc) as c:
            cals = c.list_calendars()
            print(f"✓ 连接成功, {len(cals)} 个日历:")
            for name, href in cals:
                print(f"    · {name}")
    except CaldavError as e:
        print(f"✗ {e.message}")
        if e.hint:
            print(f"  提示: {e.hint}")
        return 1
    return 0


def cmd_sync(account_id: int, policy: str) -> int:
    acc = get_account(account_id)
    if not acc:
        print(f"账号 #{account_id} 不存在")
        return 1
    print(f"同步 {acc.display_name} …")
    eng = SyncEngine(acc, conflict_policy=policy,
                     progress_cb=lambda s: print(f"  {s}"))
    r = eng.sync()
    print(f"\n结果: {r.summary()}")
    for e in r.errors:
        print(f"  ✗ {e}")
    return 0 if r.ok else 1


def cmd_sync_all(policy: str) -> int:
    results = sync_all(conflict_policy=policy,
                       progress_cb=lambda s: print(f"  {s}"))
    if not results:
        print("没有启用的账号")
        return 0
    for r in results:
        print(f"\n{r.account_name}: {r.summary()}")
        for e in r.errors:
            print(f"  ✗ {e}")
    return 0 if all(r.ok for r in results) else 1


def main() -> int:
    setup_stdout()
    p = argparse.ArgumentParser(prog="pycal.sync")
    p.add_argument("--list", action="store_true")
    p.add_argument("--add-google", action="store_true")
    p.add_argument("--add-icloud", action="store_true")
    p.add_argument("--add-outlook", action="store_true")
    p.add_argument("--add-custom", nargs="*", metavar="VALUE",
                   help="自定义 CalDAV: --add-custom URL USER [PASS] [NAME]")
    p.add_argument("--test", type=int, metavar="ID")
    p.add_argument("--sync", type=int, metavar="ID")
    p.add_argument("--sync-all", action="store_true")
    p.add_argument("--rm", type=int, metavar="ID")
    p.add_argument("--policy", default="newest_wins",
                   choices=["newest_wins", "local_wins", "remote_wins"])
    args = p.parse_args()

    if args.list:
        return cmd_list()
    if args.add_google:
        return cmd_add("google")
    if args.add_icloud:
        return cmd_add("icloud")
    if args.add_outlook:
        return cmd_add("outlook")
    if args.add_custom:
        parts = args.add_custom
        if len(parts) < 2:
            print("用法: --add-custom <URL> <USER> [PASS] [NAME]")
            return 1
        url, user = parts[0], parts[1]
        pw = parts[2] if len(parts) > 2 else getpass.getpass("密码: ")
        name = parts[3] if len(parts) > 3 else "自定义 CalDAV"
        acc_id = create_account("nextcloud", url, user, pw, name)
        print(f"✓ 已创建账号 #{acc_id}: {name}")
        return cmd_test(acc_id)
    if args.test:
        return cmd_test(args.test)
    if args.sync:
        return cmd_sync(args.sync, args.policy)
    if args.sync_all:
        return cmd_sync_all(args.policy)
    if args.rm:
        delete_account(args.rm)
        print(f"✓ 已删除账号 #{args.rm}")
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())