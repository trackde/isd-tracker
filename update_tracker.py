#!/usr/bin/env python3
"""
ISD Legislative Tracker — Daily Status Update Script
Indivisible Southern Delaware | admin@indivisiblesode.org

Fetches current bill status from LegiScan and updates index.html.
Run automatically by GitHub Actions each weekday morning.

LegiScan status codes → ISD tracker status strings:
  1  Introduced       → introduced
  2  Engrossed        → crossedover  (passed one chamber)
  3  Enrolled         → passed       (passed both chambers, sent to governor)
  4  Passed           → passed
  5  Vetoed           → dead
  6  Failed           → dead
  7  Veto Override    → passed
  8  Chaptered        → enacted      (signed into law)
  9  Refer            → committee    (referred to committee)
  10 Report Pass      → floorvote    (cleared committee, awaiting floor vote)
  11 Report Fail      → dead
  12 Draft            → introduced
"""

import os
import re
import json
import time
import requests
from datetime import datetime

# ── Configuration ─────────────────────────────────────────────────────────────
API_KEY   = os.environ['LEGISCAN_API_KEY']
BASE_URL  = 'https://api.legiscan.com/'
HTML_FILE = 'index.html'

# LegiScan numeric status code → ISD tracker status string
STATUS_MAP = {
    1:  'introduced',
    2:  'crossedover',
    3:  'passed',
    4:  'passed',
    5:  'dead',
    6:  'dead',
    7:  'passed',
    8:  'enacted',
    9:  'committee',
    10: 'floorvote',
    11: 'dead',
    12: 'introduced',
}


# ── API helpers ───────────────────────────────────────────────────────────────

def api_call(params, retries=2):
    """Make a LegiScan API call; return parsed JSON or None on failure."""
    params = dict(params)
    params['key'] = API_KEY
    for attempt in range(retries + 1):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get('status') == 'OK':
                return data
            print(f"  API returned non-OK status: {data.get('status')}")
            return None
        except Exception as e:
            print(f"  API call failed (attempt {attempt+1}): {e}")
            if attempt < retries:
                time.sleep(2)
    return None


def get_monitor_list():
    """
    Fetch all bills in the LegiScan monitor list.
    Returns dict keyed by bill number (e.g. "HB94") → bill data dict.
    """
    data = api_call({'op': 'getMonitorList', 'record': 'current'})
    if not data:
        return {}

    ml = data.get('monitorlist', {})
    bills = {}

    def add_bill(b):
        # Normalize bill number: remove spaces (HB 94 → HB94)
        num = b.get('number', '').replace(' ', '')
        if num:
            bills[num] = b

    if isinstance(ml, list):
        for b in ml:
            add_bill(b)
    elif isinstance(ml, dict):
        for key, val in ml.items():
            if isinstance(val, dict) and 'number' in val:
                add_bill(val)
            elif isinstance(val, list):
                for b in val:
                    add_bill(b)

    return bills


def get_bill_detail(bill_id):
    """
    Fetch full bill detail by numeric bill_id.
    Returns (status_code, title) or (None, None).
    """
    data = api_call({'op': 'getBill', 'id': bill_id})
    if data and 'bill' in data:
        bill = data['bill']
        return bill.get('status'), bill.get('title', '')
    return None, None


# ── HTML update helpers ───────────────────────────────────────────────────────

def extract_tracker_bills(html):
    """
    Extract all bill IDs currently in the BILLS array.
    Returns a set of strings like {"HB94", "SB2", ...}.
    """
    return set(re.findall(r'id:\s*"([A-Z]+\d+)"', html))


def update_bill_status_in_html(html, bill_id, new_status):
    """
    Find the line containing id: "BILL_ID" and update its status field.
    Only touches the status value; all other fields are preserved.
    """
    lines = html.split('\n')
    for i, line in enumerate(lines):
        if f'id: "{bill_id}"' in line and 'status:' in line:
            updated_line = re.sub(
                r'(status:\s*)"[^"]*"',
                rf'\g<1>"{new_status}"',
                line
            )
            if updated_line != line:
                print(f"  {bill_id}: {_extract_status(line)} → {new_status}")
                lines[i] = updated_line
            else:
                print(f"  {bill_id}: already {new_status} (no change)")
    return '\n'.join(lines)


def _extract_status(line):
    """Pull the current status value out of a bill line for logging."""
    m = re.search(r'status:\s*"([^"]*)"', line)
    return m.group(1) if m else '?'


def add_new_bill_to_html(html, bill_id, legiscan_status, title):
    """
    Append a new bill entry to the BILLS array with a needs-review flag.
    The entry is added at the bottom of the array before the closing ];
    """
    isd_status = STATUS_MAP.get(legiscan_status, 'introduced')
    short_title = (title[:110] + '...') if len(title) > 110 else title

    new_entry = (
        f'\n  // ── NEW BILL — NEEDS REVIEW (auto-added {datetime.now().strftime("%Y-%m-%d")}) ──\n'
        f'  {{ id: "{bill_id}", topic: "uncategorized", isd: "support", priority: false, '
        f'status: "{isd_status}",\n'
        f'    description: "NEW — needs review. Official title: {short_title}",\n'
        f'    note: "Auto-added from LegiScan monitor list. '
        f'Open Cowork to set topic, description, and position." }},'
    )

    # Insert just before the closing ]; of the BILLS array
    # The array ends with the last bill entry followed by a blank line then ];
    insert_at = html.rfind('\n];')
    if insert_at != -1:
        html = html[:insert_at] + new_entry + html[insert_at:]
        print(f"  {bill_id}: ADDED as new bill (needs review)")
    else:
        print(f"  {bill_id}: WARNING — could not find insertion point in HTML")
    return html


def update_footer_date(html):
    """Update the 'Last updated' line in the page footer."""
    today = datetime.now().strftime('%B %-d, %Y')
    html = re.sub(
        r'Last updated:.*?(?=&nbsp;)',
        f'Last updated: {today} ',
        html
    )
    return html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"ISD Tracker Update — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # Read current HTML file
    with open(HTML_FILE, 'r', encoding='utf-8') as f:
        html = f.read()

    tracker_bills = extract_tracker_bills(html)
    print(f"Bills currently in tracker: {len(tracker_bills)}")
    print(f"  {', '.join(sorted(tracker_bills))}\n")

    # Fetch LegiScan monitor list
    print("Fetching LegiScan monitor list...")
    monitor_bills = get_monitor_list()
    print(f"Bills in LegiScan monitor list: {len(monitor_bills)}")
    if monitor_bills:
        print(f"  {', '.join(sorted(monitor_bills.keys()))}\n")
    else:
        print("  (none returned — check API key and monitor list)\n")

    changes_made = False

    # ── Update status for bills already in the tracker ────────────────────────
    print("Checking status updates...")
    for bill_id in sorted(tracker_bills):
        if bill_id not in monitor_bills:
            print(f"  {bill_id}: not in monitor list — skipping")
            continue

        bill_data = monitor_bills[bill_id]
        legiscan_status = bill_data.get('status')

        if legiscan_status is None and 'bill_id' in bill_data:
            # Status not in monitor list entry — fetch full bill detail
            print(f"  {bill_id}: fetching full detail...")
            legiscan_status, _ = get_bill_detail(bill_data['bill_id'])
            time.sleep(0.5)  # be polite to the API

        if legiscan_status is not None:
            isd_status = STATUS_MAP.get(int(legiscan_status), 'introduced')
            html = update_bill_status_in_html(html, bill_id, isd_status)
            changes_made = True
        else:
            print(f"  {bill_id}: could not determine status — skipping")

    # ── Detect new bills added to monitor list ────────────────────────────────
    new_bills = sorted(set(monitor_bills.keys()) - tracker_bills)
    if new_bills:
        print(f"\n⚠️  New bills detected in monitor list: {new_bills}")
        for bill_id in new_bills:
            bill_data  = monitor_bills[bill_id]
            ls_status  = bill_data.get('status', 1)
            title      = bill_data.get('title', bill_data.get('description', ''))

            if not title and 'bill_id' in bill_data:
                print(f"  {bill_id}: fetching title...")
                _, title = get_bill_detail(bill_data['bill_id'])
                time.sleep(0.5)

            html = add_new_bill_to_html(html, bill_id, int(ls_status), title or bill_id)
            changes_made = True
    else:
        print("\nNo new bills detected.")

    # ── Update footer date ─────────────────────────────────────────────────────
    html = update_footer_date(html)

    # ── Write updated file ────────────────────────────────────────────────────
    with open(HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n{'='*60}")
    print(f"✅ Done. index.html saved.")
    if new_bills:
        print(f"\n⚠️  ACTION NEEDED: {len(new_bills)} new bill(s) were auto-added.")
        print("   Open Cowork to set topic, description, and ISD position:")
        for b in new_bills:
            print(f"   • {b}")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
