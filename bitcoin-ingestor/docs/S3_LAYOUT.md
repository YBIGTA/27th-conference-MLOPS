# S3 Data Layout

## Directory Structure

The S3 bucket follows a hierarchical partitioning scheme optimized for time-based queries and efficient data organization.

```
s3://<RAW_BUCKET>/
└── raw/
    └── exchange=binance/
        └── stream=trade/
            └── symbol=BTCUSDT/
                └── dt=2025-01-15/
                    └── 14/
                        └── 32/
                            ├── part-i-0abc12345-1736950320567.jsonl.gz
                            ├── manifest-i-0abc12345-1736950320567.json
                            ├── part-i-0def67890-1736950321234.jsonl.gz
                            ├── manifest-i-0def67890-1736950321234.json
                            └── ...
```

## Partitioning Hierarchy

### Level 1: Data Type
```
raw/
```
- Prefix for all raw (unprocessed) data
- Future: `processed/`, `aggregated/`, etc.

### Level 2: Exchange
```
exchange=binance/
```
- Identifies the data source exchange
- Enables multi-exchange support
- Hive-style partitioning for Athena/Glue

### Level 3: Stream Type
```
stream=trade/
```
- Type of market data stream
- Examples: `trade`, `kline`, `depth`, `ticker`
- Allows different stream types for same symbol

### Level 4: Trading Symbol
```
symbol=BTCUSDT/
```
- Specific trading pair
- Uppercase convention
- Easily filterable

### Level 5: Date Partition
```
dt=YYYY-MM-DD/
```
- UTC date partition
- Format: `dt=2025-01-15`
- Efficient for date-range queries

### Level 6: Hour Partition
```
HH/
```
- UTC hour (00-23)
- Enables hourly data organization
- Example: `14/` for 2 PM UTC

### Level 7: Minute Partition
```
MM/
```
- UTC minute (00-59)
- Finest time granularity
- Example: `32/` for 32 minutes past the hour

## File Naming Convention

### Data Files
```
part-{instance_id}-{timestamp_ms}.jsonl.gz
```

**Components:**
- `part-`: Prefix indicating data partition
- `{instance_id}`: EC2 instance identifier (e.g., `i-0abc12345`)
- `{timestamp_ms}`: Unix timestamp in milliseconds when file was created
- `.jsonl.gz`: JSON Lines format, gzip-compressed

**Examples:**
- `part-i-0abc12345-1736950320567.jsonl.gz`
- `part-local-001-1736950321234.jsonl.gz`

### Manifest Files
```
manifest-{instance_id}-{timestamp_ms}.json
```

**Components:**
- `manifest-`: Prefix indicating manifest file
- `{instance_id}`: Same as corresponding data file
- `{timestamp_ms}`: Same as or close to corresponding data file
- `.json`: JSON format (not compressed)

**Examples:**
- `manifest-i-0abc12345-1736950320567.json`
- `manifest-local-001-1736950321234.json`

## File Format Details

### Data Files (.jsonl.gz)

**Format:** JSON Lines (newline-delimited JSON)
**Compression:** gzip
**Encoding:** UTF-8

**Example content (uncompressed):**
```json
{"e":"trade","E":1736950320567,"s":"BTCUSDT","t":3512345,"p":"42150.50","q":"0.025","b":234567,"a":234568,"T":1736950320565,"m":true,"M":true}
{"e":"trade","E":1736950320570,"s":"BTCUSDT","t":3512346,"p":"42150.75","q":"0.100","b":234569,"a":234570,"T":1736950320568,"m":false,"M":true}
```

**Properties:**
- Each line is a complete JSON object
- No array wrapper
- Easily streamable and parseable
- Typical compression ratio: 60-70%

### Manifest Files (.json)

**Format:** JSON
**Compression:** None (small files)
**Encoding:** UTF-8

**Example:**
```json
{
  "version": "1",
  "source": {
    "exchange": "binance",
    "stream": "trade",
    "symbol": "BTCUSDT",
    "instance_id": "i-0abc12345"
  },
  "payload": {
    "s3_key": "raw/exchange=binance/stream=trade/symbol=BTCUSDT/dt=2025-01-15/14/32/part-i-0abc12345-1736950320567.jsonl.gz",
    "record_count": 12873,
    "bytes_uncompressed": 3689452,
    "bytes_gzip": 2048123,
    "time_min_ms": 1736950310123,
    "time_max_ms": 1736950320560,
    "id_first": 3500000,
    "id_last": 3512872,
    "sha256": "a1b2c3d4e5f6..."
  },
  "created_at_ms": 1736950320570
}
```

## Query Patterns

### Athena/Glue Integration (Future)

**Create External Table:**
```sql
CREATE EXTERNAL TABLE raw_trades (
  e STRING,
  E BIGINT,
  s STRING,
  t BIGINT,
  p STRING,
  q STRING,
  b BIGINT,
  a BIGINT,
  T BIGINT,
  m BOOLEAN,
  M BOOLEAN
)
PARTITIONED BY (
  exchange STRING,
  stream STRING,
  symbol STRING,
  dt STRING,
  hour STRING,
  minute STRING
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://<RAW_BUCKET>/raw/';
```

**Query Example:**
```sql
SELECT *
FROM raw_trades
WHERE exchange = 'binance'
  AND stream = 'trade'
  AND symbol = 'BTCUSDT'
  AND dt = '2025-01-15'
  AND hour = '14'
LIMIT 100;
```

## Storage Estimates

### Per Symbol
- Trades per second: ~1,000 (average)
- Bytes per trade (compressed): ~200
- Data rate: ~200 KB/s = ~12 MB/min = ~720 MB/hour = ~17 GB/day

### Per Instance
- Files per minute: ~12 (5-second rotation)
- Files per hour: ~720
- Files per day: ~17,280

### Multi-Instance (3x)
- Total files per day: ~52,000
- Total data per day: ~51 GB (with compression)

## Best Practices

### Data Retention
- Keep raw data for 90 days (configurable)
- Archive to Glacier for longer retention
- Use S3 lifecycle policies

### Cost Optimization
- Use S3 Standard for recent data (<30 days)
- Transition to S3 Standard-IA after 30 days
- Archive to Glacier after 90 days

### Data Organization
- Maintain consistent naming conventions
- Use UTC timestamps exclusively
- Keep partition depth reasonable (avoid over-partitioning)

### Monitoring
- Track file upload success rate
- Monitor partition sizes
- Alert on missing data
