# Architecture Overview

## System Design

The market data ingestion system is designed as a minimal, robust pipeline for collecting real-time trade data from Binance and storing it in AWS S3.

```
┌─────────────────┐
│  Binance API    │
│  WebSocket      │
└────────┬────────┘
         │ Trade events
         │ (JSON stream)
         ▼
┌─────────────────────────────────┐
│   Collector Instance (EC2)      │
│  ┌──────────────────────────┐   │
│  │  BinanceWSClient         │   │
│  │  - Async WS connection   │   │
│  │  - Auto-reconnect        │   │
│  └──────────┬───────────────┘   │
│             │ Event stream      │
│             ▼                   │
│  ┌──────────────────────────┐   │
│  │  RotatingWriter          │   │
│  │  - Buffer to local file  │   │
│  │  - Rotation triggers:    │   │
│  │    * 2 MB file size      │   │
│  │    * 5 seconds elapsed   │   │
│  └──────────┬───────────────┘   │
│             │ On rotation       │
│             ▼                   │
│  ┌──────────────────────────┐   │
│  │  Compression (gzip)      │   │
│  └──────────┬───────────────┘   │
│             │ .jsonl.gz         │
│             ▼                   │
│  ┌──────────────────────────┐   │
│  │  S3Uploader              │   │
│  │  - Upload data file      │   │
│  │  - Generate manifest     │   │
│  │  - Upload manifest       │   │
│  │  - Retry logic (3x)      │   │
│  └──────────┬───────────────┘   │
└─────────────┼───────────────────┘
              │
              ▼
     ┌────────────────┐
     │   AWS S3       │
     │  Raw Bucket    │
     └────────────────┘
```

## Components

### 1. WebSocket Client (`ws_client.py`)

**Responsibilities:**
- Establish WebSocket connection to Binance
- Handle incoming trade events
- Auto-reconnect on connection failures
- Parse JSON messages

**Key Features:**
- Asynchronous (asyncio-based)
- Configurable ping/pong for connection health
- 1-second reconnection delay
- Graceful error handling

### 2. Rotating Writer (`rotator.py`)

**Responsibilities:**
- Write events to local JSONL files
- Monitor file size and elapsed time
- Trigger rotation based on thresholds
- Coordinate compression and upload

**Rotation Triggers:**
- File size ≥ 2 MB (2,097,152 bytes)
- Elapsed time ≥ 5 seconds
- Whichever comes first

**Statistics Tracked:**
- Record count
- Min/max timestamps
- First/last trade IDs
- File sizes (uncompressed & compressed)

### 3. S3 Uploader (`s3_uploader.py`)

**Responsibilities:**
- Upload files to S3
- Apply server-side encryption (AES256)
- Retry failed uploads (up to 3 attempts)
- Exponential backoff on failures

**Key Features:**
- Boto3-based
- Configurable retry logic
- Support for both file and JSON uploads
- Detailed logging

### 4. Manifest Generator (`manifest.py`)

**Responsibilities:**
- Create metadata for each uploaded file
- Include data quality information
- Provide file integrity hash (SHA256)

**Manifest Contents:**
- Source information (exchange, stream, symbol, instance)
- Payload metadata (record count, timestamps, file sizes)
- SHA256 hash for verification
- Creation timestamp

### 5. Main Collector (`collector_simple.py`)

**Responsibilities:**
- Application entry point
- Configuration loading
- Component initialization
- Signal handling for graceful shutdown

## Data Flow

1. **Receive**: WebSocket client receives trade events from Binance
2. **Buffer**: Events are written to local JSONL file
3. **Rotate**: When threshold is met, file rotation is triggered
4. **Compress**: Local file is gzip-compressed
5. **Upload**: Compressed file is uploaded to S3
6. **Manifest**: Manifest JSON is generated and uploaded
7. **Cleanup**: Local files are deleted
8. **Repeat**: Process continues with new file

## Fault Tolerance

### Connection Failures
- Automatic reconnection with 1-second delay
- Infinite retry loop
- No data loss (current file preserved)

### Upload Failures
- 3 retry attempts with exponential backoff
- Local files preserved on failure
- Logged for manual intervention

### Process Crashes
- Systemd auto-restart (5-second delay)
- Local files may be orphaned (acceptable)
- Next rotation will continue normally

## Scalability

### Multiple Instances
- 3 EC2 t3.micro instances recommended
- Each instance operates independently
- Duplicate events across instances (by design)
- Deduplication handled downstream (future)

### Instance Identification
- Each instance has unique `INSTANCE_ID`
- Embedded in filenames: `part-{instance_id}-{timestamp}.jsonl.gz`
- Prevents file name collisions

## Security

### Encryption
- Server-side encryption (AES256) on S3
- Encryption at rest (S3 default)
- TLS for WebSocket connection

### IAM
- Least-privilege IAM role for EC2
- S3 PutObject permission only
- No bucket-level modifications

### Network
- No public bucket access
- VPC endpoints recommended (optional)

## Performance Characteristics

### Expected Load
- ~1,000-3,000 trades/second per symbol (peak)
- ~100-300 KB/s data rate
- File rotation every 5 seconds (typical)

### Resource Usage
- CPU: <10% (t3.micro)
- Memory: <200 MB
- Disk: <100 MB (transient)
- Network: <1 Mbps

## Future Enhancements (Post-MVP)

1. **Kinesis Integration**
   - Real-time streaming to Kinesis Data Streams
   - Lambda processing for 1-second OHLCV

2. **Gap Recovery**
   - REST API fallback for missed trades
   - Reconciliation using trade IDs

3. **Monitoring**
   - CloudWatch metrics
   - Alerting on upload failures
   - Dashboard for data flow

4. **Multi-Symbol**
   - Parallel collection for multiple symbols
   - Resource pooling

5. **Containerization**
   - Docker packaging
   - ECS deployment
   - Auto-scaling
