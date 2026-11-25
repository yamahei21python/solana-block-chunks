# solana-block-chunks

Solana mainnet の生ブロックを公開 RPC で取得し、gzip して GitHub Release に置く工場。
API キー不要。DL は利用側の任意。

## 実行

`Actions > Fetch Solana blocks > Run workflow`

| input | 既定 | 説明 |
|---|---|---|
| `first_slot` | `473601205` | 開始 slot |
| `slot_count` | `1000` | 連続 slot 数 (1〜100000、1 job の loop) |

1 run = 1 Release。tag は `sol_<first>_<last>`。

## 取得方式

- プライマリ: `https://api.mainnet-beta.solana.com`
- フォールバック: `https://solana-rpc.publicnode.com` (HTTP 403/429 で自動切替、以降は固定)
- `getBlock(slot, {"maxSupportedTransactionVersion":0, "transactionDetails":"full", "rewards":false})`
- 1 request ごとに 0.2 秒 sleep (公開 RPC のレート制限対策、再試行 3 回)
- 公開 endpoint が `-32015` を返す block は `maxSupportedTransactionVersion:1` で再取得し `full` を維持
- それでも失敗する場合のみ `transactionDetails:"signatures"` に自動ダウングレード。実際のモードは manifest の `transaction_details` を参照

## ペイロード (NDJSON)

`sol_<first>_<last>.json.gz` を gunzip した 1 行 = 1 block:

```json
{"slot": 473601205, "result": { "blockHeight": ..., "blockhash": ..., "blockTime": ..., "previousBlockhash": ..., "parentSlot": ..., "transactions": [...], "signatures": [...] }}
```

- `result` は RPC の `getBlock` レスポンスそのもの (改変なし)
- `transactionDetails:full` なら `transactions` に完全なトランザクション、`signatures` なら署名文字列のみ
- スキップされた slot / エラー slot は行を出さず、`empty_or_error_slots` に計上

## manifest.json (Release の別 asset + notes)

`first_slot`, `last_slot`, `requested_slots`, `block_count`, `tx_count`, `empty_or_error_slots`,
`db_bytes` (非圧縮バイト), `gzip_bytes`, `sha256` (gzip asset の SHA-256),
`sha256_target`, `transaction_details`, `max_supported_transaction_version`,
`rpc_primary`, `rpc_fallback`, `fallback_used`, `request_sleep_seconds`

## 制限

- 公開 RPC のため SLA なし。403/429/5xx は切替・再試行で吸収するが、取得欠損はあり得る
- `transactionDetails:full` は公開 endpoint で拒否される block があり、その場合は `signatures` に落ちる (manifest に明記)
- 100000 slot/run 上限。それ以上は複数 run に分割
- slot の timestamp は blockTime (Unix 秒)。最新 slot 付近は取得不可になり得る
- 実測 (100 slot, `transaction_details:full`): `db_bytes=300898463` (約 287 MiB)、
  `gzip_bytes=59643034` (約 57 MiB)、`tx_count=41932`。1 block 平均約 3 MB 非圧縮 / 0.6 MB gzip。
- 1 日あたりの生データ量 (推測、上記実測からの外挿): slot 間隔約 0.4 秒 → 約 216000 slot/日。
  非圧縮約 650 GB/日、gzip 約 129 GB/日。実際値は manifest の `db_bytes` / `gzip_bytes` から算出。
