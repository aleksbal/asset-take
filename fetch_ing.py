#!/usr/bin/env python3
"""Read-only FinTS client for ING Deutschland (BLZ 50010517).

Prompts for your ING Zugangsnummer and Internetbanking-PIN; nothing is stored
except the FinTS system id (see STATE_FILE), which is not a credential.

Usage:
    export FINTS_PRODUCT_ID=<your registered product id>
    ./.venv/bin/python fetch_ing.py [--days 30] [--holdings]
"""
import argparse
import datetime
import getpass
import os
import sys
from pathlib import Path

from fints.client import FinTS3PinTanClient, FinTSClientMode, FinTSOperations, NeedTANResponse

BLZ = "50010517"
SERVER = "https://fints.ing.de/fints/"
STATE_FILE = Path(__file__).with_name(".fints_state.bin")
PRODUCT_VERSION = "0.1"


def load_state():
    if STATE_FILE.exists():
        return STATE_FILE.read_bytes()
    return None


def save_state(client):
    data = client.deconstruct(including_private=False)
    STATE_FILE.touch(mode=0o600, exist_ok=True)
    os.chmod(STATE_FILE, 0o600)
    STATE_FILE.write_bytes(data)


def ask_tan(client, response):
    """Show the bank's challenge and forward the TAN the user reads off their app."""
    print("\n" + "=" * 70)
    print("TAN required (second factor)")
    print("-" * 70)
    print(response.challenge)
    if response.challenge_matrix:
        mime, data = response.challenge_matrix
        ext = "png" if "png" in mime else "bin"
        path = Path(f"tan_challenge.{ext}")
        path.write_bytes(data)
        print(f"\n[photoTAN/QR image written to {path.resolve()} — open it and scan]")
    if response.challenge_hhduc:
        print(f"HHD_UC: {response.challenge_hhduc}")
    print("=" * 70)
    tan = input("Enter TAN (or press Enter after confirming in the ING app): ").strip()
    return client.send_tan(response, tan)


def resolve(client, response):
    """Unwrap a response that may be a pending TAN challenge."""
    while isinstance(response, NeedTANResponse):
        response = ask_tan(client, response)
    return response


def choose_tan_mechanism(client):
    mechanisms = list(client.get_tan_mechanisms().items())
    if not mechanisms:
        return
    print("\nAvailable TAN mechanisms:")
    for i, (mid, m) in enumerate(mechanisms):
        print(f"  [{i}] {mid}  {m.name}  (tech: {m.tech_id})")
    if len(mechanisms) == 1:
        choice = 0
        print(f"-> only one, using [{0}]")
    else:
        choice = int(input("Choose mechanism [0]: ").strip() or "0")
    client.set_tan_mechanism(mechanisms[choice][0])

    if client.is_tan_media_required() and not client.selected_tan_medium:
        media = client.get_tan_media()[1]
        print("\nAvailable TAN media:")
        for i, m in enumerate(media):
            print(f"  [{i}] {m.tan_medium_name}")
        idx = 0 if len(media) == 1 else int(input("Choose medium [0]: ").strip() or "0")
        client.set_tan_medium(media[idx])


def print_supported(info):
    ops = info["bank"].get("supported_operations", {})
    print("\nOperations the bank advertises:")
    for op in (FinTSOperations.GET_SEPA_ACCOUNTS, FinTSOperations.GET_BALANCE,
               FinTSOperations.GET_TRANSACTIONS, FinTSOperations.GET_CREDIT_CARD_TRANSACTIONS,
               FinTSOperations.GET_HOLDINGS):
        print(f"  {op.name:<32} {'yes' if ops.get(op) else 'NO'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="how many days of transactions")
    ap.add_argument("--holdings", action="store_true", help="also attempt get_holdings() (Depot)")
    args = ap.parse_args()

    product_id = os.environ.get("FINTS_PRODUCT_ID")
    if not product_id:
        sys.exit("FINTS_PRODUCT_ID is not set. python-fints >= 4 requires a product id "
                 "registered with the Deutsche Kreditwirtschaft. See README.md.")

    user_id = os.environ.get("ING_USER") or input("ING Zugangsnummer: ").strip()
    pin = getpass.getpass("Internetbanking-PIN: ")

    client = FinTS3PinTanClient(
        BLZ, user_id, pin, SERVER,
        product_id=product_id,
        product_version=PRODUCT_VERSION,
        from_data=load_state(),
        mode=FinTSClientMode.INTERACTIVE,
    )

    choose_tan_mechanism(client)

    with client:
        if client.init_tan_response:
            resolve(client, client.init_tan_response)

        info = client.get_information()
        print(f"\nBank: {info['bank'].get('name')}")
        print_supported(info)

        accounts = resolve(client, client.get_sepa_accounts())
        print(f"\nAccounts ({len(accounts)}):")
        for acc in accounts:
            print(f"  IBAN {acc.iban}  BIC {acc.bic}  Nr {acc.accountnumber}")

        since = datetime.date.today() - datetime.timedelta(days=args.days)
        for acc in accounts:
            print("\n" + "-" * 70)
            print(f"Account {acc.iban}")
            try:
                balance = resolve(client, client.get_balance(acc))
                print(f"  Balance: {balance.amount.amount} {balance.amount.currency} "
                      f"(as of {balance.date})")
            except Exception as e:
                print(f"  balance failed: {type(e).__name__}: {e}")

            try:
                txns = resolve(client, client.get_transactions(acc, since, datetime.date.today()))
                print(f"  Transactions since {since}: {len(txns)}")
                for t in txns[-10:]:
                    d = t.data
                    print(f"    {d['date']}  {d['amount'].amount:>10}  "
                          f"{(d.get('applicant_name') or '')[:28]:<28} "
                          f"{(d.get('purpose') or '')[:40]}")
            except Exception as e:
                print(f"  transactions failed: {type(e).__name__}: {e}")

            if args.holdings:
                try:
                    holdings = resolve(client, client.get_holdings(acc))
                    print(f"  Holdings: {len(holdings)}")
                    for h in holdings:
                        print(f"    {h.name} ISIN {h.isin} x{h.pieces} @ {h.price} = {h.total_value}")
                except Exception as e:
                    print(f"  holdings failed: {type(e).__name__}: {e}")

    save_state(client)
    print(f"\nState saved to {STATE_FILE.name} (system id only, no credentials).")


if __name__ == "__main__":
    main()
