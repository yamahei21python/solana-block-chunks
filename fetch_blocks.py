#!/usr/bin/env python3
"""Fetch Solana mainnet blocks via public RPC and emit gzip + manifest for a GitHub Release.

No API keys. Stdlib only. Each line of the payload is one JSON object:
  {"slot": <u64>, "result": <raw getBlock result>}
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

PRIMARY = "https://api.mainnet-beta.solana.com"
FALLBACK = "https://solana-rpc.publicnode.com"
TIMEOUT = 30
RETRIES = 3


class RPC:
    def __init__(self):
        self.url = PRIMARY
        self.fallback_used = False
        self.req_id = 0

    def call(self, method, params):
        self.req_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self.req_id, "method": method, "params": params}
        ).encode()
        for attempt in range(RETRIES):
            req = urllib.request.Request(
                self.url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "solana-block-chunks/1.0",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if "error" in data:
                    return {"rpc_error": data["error"]}
                return {"result": data.get("result")}
            except urllib.error.HTTPError as exc:
                code = exc.code
                if code in (403, 429) and not self.fallback_used:
                    self.url = FALLBACK
                    self.fallback_used = True
                    print(f"RPC {code}: switching to fallback endpoint", file=sys.stderr)
                if code in (429, 500, 502, 503, 504) and attempt < RETRIES - 1:
                    time.sleep(1 + attempt)
                    continue
                return {"http_error": code}
            except Exception as exc:  # noqa: BLE001 - network layer
                if attempt < RETRIES - 1:
                    time.sleep(1 + attempt)
                    continue
                return {"network_error": str(exc)[:200]}
        return {"error": "retries_exhausted"}


def err_kind(res):
    if not isinstance(res, dict):
        return None
    for key in ("rpc_error", "http_error", "network_error", "error"):
        if key in res:
            return key
    return None


def get_block(rpc, slot, details, version):
    """Return (result, mode) where mode may ask caller to downgrade details."""
    cfg = {
        "maxSupportedTransactionVersion": version,
        "transactionDetails": details,
        "rewards": False,
    }
    res = rpc.call("getBlock", [slot, cfg])
    if err_kind(res) == "rpc_error":
        msg = str(res["rpc_error"].get("message", ""))
        code = res["rpc_error"].get("code")
        # Public endpoints reject version 0 for some blocks; version 1 keeps full data.
        if details == "full" and version == 0 and code == -32015:
            cfg["maxSupportedTransactionVersion"] = 1
            res = rpc.call("getBlock", [slot, cfg])
            if not err_kind(res):
                return res["result"], ("full", 1)
        if details == "full" and (
            code == -32015 or "not supported" in msg.lower() or "too large" in msg.lower()
        ):
            return None, ("signatures", 0)
    if err_kind(res):
        return None, None
    return res["result"], (details, version)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first-slot", type=int, required=True)
    ap.add_argument("--slot-count", type=int, default=1000)
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--transaction-details", choices=("full", "signatures"), default="full")
    ap.add_argument("--max-supported-transaction-version", type=int, default=0)
    args = ap.parse_args()

    if args.slot_count < 1:
        raise SystemExit("slot_count must be >= 1")
    if args.slot_count > 100000:
        raise SystemExit("slot_count capped at 100000 per run")

    os.makedirs(args.out_dir, exist_ok=True)
    first = args.first_slot
    last = first + args.slot_count - 1
    tag = f"sol_{first}_{last}"
    raw_path = os.path.join(args.out_dir, f"{tag}.json")
    gz_path = raw_path + ".gz"

    rpc = RPC()
    details = args.transaction_details
    version = args.max_supported_transaction_version
    block_count = 0
    tx_count = 0
    empty_slots = 0

    with open(raw_path, "w", encoding="utf-8") as fh:
        for slot in range(first, first + args.slot_count):
            result, mode = get_block(rpc, slot, details, version)
            if mode is not None and mode != (details, version):
                details, version = mode
                print(f"downgraded getBlock config -> details={details} version={version}", file=sys.stderr)
            if result is None:
                empty_slots += 1
            else:
                rec = {"slot": slot, "result": result}
                fh.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")
                block_count += 1
                txs = result.get("transactions") if isinstance(result, dict) else None
                if txs is None:
                    txs = result.get("signatures") if isinstance(result, dict) else None
                tx_count += len(txs) if isinstance(txs, list) else 0
            if args.sleep > 0:
                time.sleep(args.sleep)

    db_bytes = os.path.getsize(raw_path)
    with open(raw_path, "rb") as fin, gzip.open(gz_path, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout)
    gz_bytes = os.path.getsize(gz_path)
    with open(gz_path, "rb") as fh:
        sha256 = hashlib.sha256(fh.read()).hexdigest()

    manifest = {
        "tag": tag,
        "first_slot": first,
        "last_slot": last,
        "requested_slots": args.slot_count,
        "block_count": block_count,
        "tx_count": tx_count,
        "empty_or_error_slots": empty_slots,
        "db_bytes": db_bytes,
        "gzip_bytes": gz_bytes,
        "sha256": sha256,
        "sha256_target": f"{tag}.json.gz",
        "transaction_details": details,
        "max_supported_transaction_version": version,
        "rpc_primary": PRIMARY,
        "rpc_fallback": FALLBACK,
        "fallback_used": rpc.fallback_used,
        "request_sleep_seconds": args.sleep,
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    with open(os.path.join(args.out_dir, "release-notes.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# {tag}\n\nSolana mainnet blocks `{first}`..`{last}`.\n\n")
        fh.write("```json\n" + json.dumps(manifest, indent=2, ensure_ascii=False) + "\n```\n")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
