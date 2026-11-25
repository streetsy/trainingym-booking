#!/usr/bin/env python3

import argparse
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from time import sleep

import yaml

from api import Trainingym, Dias, now_madrid, log

# Statuses (see Trainingym.booking_attempt) after which there is nothing more to retry
FINISHED = {"booked", "already", "full", "dry-run"}

STATUS_TEXT = {
    "booked": "✅ booked",
    "already": "✅ already booked",
    "full": "❌ class is full",
    "waiting": "⏳ not bookable (never opened in time)",
    "unconfirmed": "⚠️ booking sent but not confirmed, check the app",
    "dry-run": "🧪 dry run, would book",
}


def print_activities(activities):
    msg = list()
    for act in activities:
        date = act["date"].strftime("%a %d %H:%M")
        txt = f"{act['name']} el {date} ({act['id']})"
        msg.append(txt)
    print("\n".join(msg))


def load_yaml(filepath: str = "clases.yaml"):
    activities = dict()
    with open(filepath, 'r') as file:
        data = yaml.safe_load(file)
        for dia in Dias:
            if dia.name in data.keys():
                activities[dia.value] = data[dia.name]
                activities[dia.value]["start_time"] = datetime.strptime(data[dia.name].get("desde"), "%I%p").time()
    return activities

def parse_arguments():
    parser = argparse.ArgumentParser(description="Book Trainingym classes for one or more members")
    parser.add_argument("email", nargs="?", default=None, help="Email address to use for login (single member)")
    parser.add_argument("password", nargs="?", default=None, help="Password to use for login (single member)")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--classes", default="clases.yaml", help="Class wish list file (default clases.yaml); use another file for testing")
    parser.add_argument("--interval", type=int, default=15, help="Seconds between attempts (default 15)")
    parser.add_argument("--max-minutes", type=float, default=10, help="Stop retrying this long after booking opens (default 10)")
    parser.add_argument("--test-any-class", action="store_true", help="Test mode: consider ANY class ~72h away, ignoring clases.yaml (combine with --dry-run to only report)")
    parser.add_argument("--cancel-id", type=int, default=None, help="Cancel this class id for every member, then stop")
    parser.add_argument("--window", default=None, help='Test only: hours-from-now window to look at, e.g. "45-55" (normal runs use 71-72.25)')
    parser.add_argument("--list-classes", action="store_true", help="Test only: list the classes in the --window range with their ids, then stop")
    parser.add_argument("--only-id", type=int, default=None, help="Test only: book just this one class id")
    parser.add_argument("--dry-run", action="store_true", help="Do everything except actually sending the booking")

    return parser.parse_args()

def load_config(filepath):
    try:
        with open(filepath, "r") as config_file:
            config_data = yaml.safe_load(config_file)
            if config_data is None:
                raise ValueError(f"Invalid YAML data in {filepath}")
            return config_data
    except FileNotFoundError:
        raise FileNotFoundError(f"{filepath} not found")

def load_accounts(args, config):
    """
    Accounts to book for. Either a list in config.yaml:

        accounts:
          - email: a@example.com
            password: secret
          - email: b@example.com
            password: secret

    or the old single "email:" / "password:" keys, or email + password on the command line.
    Entries with an empty email or password are skipped.
    """
    if args.email and args.password:
        accounts = [{"email": args.email, "password": args.password}]
    elif config.get("accounts"):
        accounts = config["accounts"]
    else:
        accounts = [{"email": config.get("email"), "password": config.get("password")}]

    accounts = [{"email": str(a.get("email") or "").strip(), "password": str(a.get("password") or "").strip()} for a in accounts]
    accounts = [a for a in accounts if a["email"] and a["password"]]
    if not accounts:
        raise ValueError("Missing email or password")
    return accounts


def wait_until(moment: datetime):
    """ Sleep until the given Madrid time (returns immediately if it has passed) """
    while True:
        remaining = (moment - now_madrid()).total_seconds()
        if remaining <= 0:
            return
        sleep(min(remaining, 1))


def run_member(client: Trainingym, want_list: dict, expected_ids: set, start_at: datetime, interval: int, deadline: datetime):
    """
    Try to book for one member: first attempt at start_at, then one every `interval` seconds,
    until the class is booked/full or the deadline passes. Returns (name, last results or None).
    """
    name = client.person_fullname
    results = None
    attempt = 0
    booked_by_us = set()  # class ids this run booked itself (later attempts see them as "already")

    while True:
        wait_until(start_at + timedelta(seconds=attempt * interval))
        attempt += 1
        try:
            results = client.booking_attempt(want_list)
            for t, st in results:
                if st == "booked":
                    booked_by_us.add(t["id"])
            results = [(t, "booked" if st == "already" and t["id"] in booked_by_us else st) for t, st in results]
        except Exception as e:  # network hiccup etc: keep trying until the deadline
            results = None
            log(f"[{name}] attempt {attempt} failed: {e!r}")
        else:
            shown = ", ".join(f"{t['name']} {t['start']:%a %H:%M} -> {s}" for t, s in results) or "no matching class"
            log(f"[{name}] attempt {attempt} at {now_madrid():%H:%M:%S}: {shown}")
            finished = {t["id"] for t, status in results if status in FINISHED}
            if expected_ids <= finished:
                break
            if not results:
                log(f"[{name}] attempt {attempt}: the gym did not show the class, retrying")

        if now_madrid() + timedelta(seconds=interval) > deadline:
            break

    return name, results


def main():
    args = parse_arguments()
    config = load_config(args.config)
    accounts = load_accounts(args, config)
    want_list = load_yaml(args.classes)

    # 1. Log everyone in first, so nothing slow is left to do when booking opens
    clients, lines = [], []
    for account in accounts:
        client = Trainingym()
        client.dry_run = args.dry_run
        client.any_class = args.test_any_class
        client.only_id = args.only_id
        if args.window:
            client.window_min, client.window_max = (float(x) for x in args.window.split("-"))
        try:
            client.login(account["email"], account["password"])
        except Exception as e:
            log(f"Login failed for {account['email']}: {e!r}")
            lines.append(f"❌ {account['email']}: login failed")
            continue
        log(f"Logged in: {client.person_fullname}")
        clients.append(client)

    if not clients:
        print("\n".join(lines))
        sys.exit(1)

    if args.cancel_id:
        for client in clients:
            resp = client.activityCancel(args.cancel_id)
            log(f"[{client.person_fullname}] cancel request for {args.cancel_id}: HTTP {resp.status_code}")
        sleep(3)
        print(f"Cancellation requested for class {args.cancel_id}")
        for client in clients:
            client.refresh()
            print(f"\nBookings now for {client.person_fullname}:")
            print_activities(client.next_activities())
        return

    if args.list_classes:
        lister = clients[0]
        lister.any_class = True
        found = sorted(lister.find_target_classes(want_list), key=lambda t: t["start"])
        print(f"Classes {lister.window_min:g}-{lister.window_max:g}h away (id | start | name | places left):")
        for t in found:
            print(f"{t['id']} | {t['start']:%a %d %H:%M} | {t['name']} | {t['places']}")
        if not found:
            print("none")
        return

    # 2. Work out which class has just opened, and when exactly its booking opens
    targets = clients[0].find_target_classes(want_list)
    if not targets:
        print(f"No class from clases.yaml is opening for booking right now (nothing {clients[0].window_min:g}-{clients[0].window_max:g}h away).")
        if lines:
            print("\n".join(lines))
        return

    expected_ids = {t["id"] for t in targets}
    class_text = ", ".join(sorted({f"{t['name']} {t['start']:%a %d %b %H:%M}" for t in targets}))
    opens_at = min(t["opens"] for t in targets)
    start_at = max(opens_at, now_madrid())
    deadline = start_at + timedelta(minutes=args.max_minutes)
    if opens_at > now_madrid():
        log(f"Booking for {class_text} opens at {opens_at:%H:%M:%S}, waiting {(opens_at - now_madrid()).total_seconds():.0f}s")
    else:
        log(f"Booking for {class_text} already open (since {opens_at:%H:%M:%S}), starting now")

    # 3. Everyone books at the same time, retrying every `interval` seconds
    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        futures = [pool.submit(run_member, c, want_list, expected_ids, start_at, args.interval, deadline) for c in clients]
        outcomes = [f.result() for f in futures]

    # 4. Summary (stdout -> Telegram)
    print(f"Booking: {class_text}")
    for name, results in outcomes:
        if results is None:
            lines.append(f"⚠️ {name}: error, see the Actions log")
        elif not results:
            lines.append(f"⚠️ {name}: the gym never showed the class, check the app")
        else:
            for target, status in results:
                lines.append(f"{STATUS_TEXT.get(status, status)} - {name} (class ID {target['id']})")
    print("\n".join(lines))

    # Same confirmation as before: what each member has booked now
    sleep(2)
    for client in clients:
        print(f"\nConfirmed bookings for {client.person_fullname}:")
        for _try in range(4):
            try:
                client.refresh()
                print_activities(client.next_activities(strict=True))
                break
            except Exception as e:
                log(f"[{client.person_fullname}] could not read bookings ({e!r})")
                sleep(2)
        else:
            print("(could not read the list, check the app)")

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"❌ Booking script crashed: {e!r}")
        traceback.print_exc()
        sys.exit(1)
