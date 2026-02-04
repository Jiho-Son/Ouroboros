# Disaster Recovery Guide

Complete guide for backing up and restoring The Ouroboros trading system.

## Table of Contents

- [Backup Strategy](#backup-strategy)
- [Creating Backups](#creating-backups)
- [Restoring from Backup](#restoring-from-backup)
- [Health Monitoring](#health-monitoring)
- [Export Formats](#export-formats)
- [RTO/RPO](#rtorpo)
- [Testing Recovery](#testing-recovery)

## Backup Strategy

The system implements a 3-tier backup retention policy:

| Policy | Frequency | Retention | Purpose |
|--------|-----------|-----------|---------|
| **Daily** | Every day | 30 days | Quick recovery from recent issues |
| **Weekly** | Sunday | 1 year | Medium-term historical analysis |
| **Monthly** | 1st of month | Forever | Long-term archival |

### Storage Structure

```
data/backups/
├── daily/          # Last 30 days
├── weekly/         # Last 52 weeks
└── monthly/        # Forever (cold storage)
```

## Creating Backups

### Automated Backups (Recommended)

Set up a cron job to run daily:

```bash
# Edit crontab
crontab -e

# Run backup at 2 AM every day
0 2 * * * cd /path/to/The-Ouroboros && ./scripts/backup.sh >> logs/backup.log 2>&1
```

### Manual Backups

```bash
# Run backup script
./scripts/backup.sh

# Or use Python directly
python3 -c "
from pathlib import Path
from src.backup.scheduler import BackupScheduler, BackupPolicy

scheduler = BackupScheduler('data/trade_logs.db', Path('data/backups'))
metadata = scheduler.create_backup(BackupPolicy.DAILY, verify=True)
print(f'Backup created: {metadata.file_path}')
"
```

### Export to Other Formats

```bash
python3 -c "
from pathlib import Path
from src.backup.exporter import BackupExporter, ExportFormat

exporter = BackupExporter('data/trade_logs.db')
results = exporter.export_all(
    Path('exports'),
    formats=[ExportFormat.JSON, ExportFormat.CSV],
    compress=True
)
"
```

## Restoring from Backup

### Interactive Restoration

```bash
./scripts/restore.sh
```

The script will:
1. List available backups
2. Ask you to select one
3. Create a safety backup of current database
4. Restore the selected backup
5. Verify database integrity

### Manual Restoration

```python
from pathlib import Path
from src.backup.scheduler import BackupScheduler

scheduler = BackupScheduler('data/trade_logs.db', Path('data/backups'))

# List backups
backups = scheduler.list_backups()
for backup in backups:
    print(f"{backup.timestamp}: {backup.file_path}")

# Restore specific backup
scheduler.restore_backup(backups[0], verify=True)
```

## Health Monitoring

### Check System Health

```python
from pathlib import Path
from src.backup.health_monitor import HealthMonitor

monitor = HealthMonitor('data/trade_logs.db', Path('data/backups'))

# Run all checks
report = monitor.get_health_report()
print(f"Overall status: {report['overall_status']}")

# Individual checks
checks = monitor.run_all_checks()
for name, result in checks.items():
    print(f"{name}: {result.status.value} - {result.message}")
```

### Health Checks

The system monitors:

- **Database Health**: Accessibility, integrity, size
- **Disk Space**: Available storage (alerts if < 10 GB)
- **Backup Recency**: Ensures backups are < 25 hours old

### Health Status Levels

- **HEALTHY**: All systems operational
- **DEGRADED**: Warning condition (e.g., low disk space)
- **UNHEALTHY**: Critical issue (e.g., database corrupted, no backups)

## Export Formats

### JSON (Human-Readable)

```json
{
  "export_timestamp": "2024-01-15T10:30:00Z",
  "record_count": 150,
  "trades": [
    {
      "timestamp": "2024-01-15T09:00:00Z",
      "stock_code": "005930",
      "action": "BUY",
      "quantity": 10,
      "price": 70000.0,
      "confidence": 85,
      "rationale": "Strong momentum",
      "pnl": 0.0
    }
  ]
}
```

### CSV (Analysis Tools)

Compatible with Excel, pandas, R:

```csv
timestamp,stock_code,action,quantity,price,confidence,rationale,pnl
2024-01-15T09:00:00Z,005930,BUY,10,70000.0,85,Strong momentum,0.0
```

### Parquet (Big Data)

Columnar format for Spark, DuckDB:

```python
import pandas as pd
df = pd.read_parquet('exports/trades_20240115.parquet')
```

## RTO/RPO

### Recovery Time Objective (RTO)

**Target: < 5 minutes**

Time to restore trading operations:
1. Identify backup to restore (1 min)
2. Run restore script (2 min)
3. Verify database integrity (1 min)
4. Restart trading system (1 min)

### Recovery Point Objective (RPO)

**Target: < 24 hours**

Maximum acceptable data loss:
- Daily backups ensure ≤ 24-hour data loss
- For critical periods, run backups more frequently

## Testing Recovery

### Quarterly Recovery Test

Perform full disaster recovery test every quarter:

1. **Create test backup**
   ```bash
   ./scripts/backup.sh
   ```

2. **Simulate disaster** (use test database)
   ```bash
   cp data/trade_logs.db data/trade_logs_test.db
   rm data/trade_logs_test.db  # Simulate data loss
   ```

3. **Restore from backup**
   ```bash
   DB_PATH=data/trade_logs_test.db ./scripts/restore.sh
   ```

4. **Verify data integrity**
   ```python
   import sqlite3
   conn = sqlite3.connect('data/trade_logs_test.db')
   cursor = conn.execute('SELECT COUNT(*) FROM trades')
   print(f"Restored {cursor.fetchone()[0]} trades")
   ```

5. **Document results** in `logs/recovery_test_YYYYMMDD.md`

### Backup Verification

Always verify backups after creation:

```python
from pathlib import Path
from src.backup.scheduler import BackupScheduler

scheduler = BackupScheduler('data/trade_logs.db', Path('data/backups'))

# Create and verify
metadata = scheduler.create_backup(BackupPolicy.DAILY, verify=True)
print(f"Checksum: {metadata.checksum}")  # Should not be None
```

## Emergency Procedures

### Database Corrupted

1. Stop trading system immediately
2. Check most recent backup age: `ls -lht data/backups/daily/`
3. Restore: `./scripts/restore.sh`
4. Verify: Run health check
5. Resume trading

### Disk Full

1. Check disk space: `df -h`
2. Clean old backups: Run cleanup manually
   ```python
   from pathlib import Path
   from src.backup.scheduler import BackupScheduler
   scheduler = BackupScheduler('data/trade_logs.db', Path('data/backups'))
   scheduler.cleanup_old_backups()
   ```
3. Consider archiving old monthly backups to external storage
4. Increase disk space if needed

### Lost All Backups

If local backups are lost:
1. Check if exports exist in `exports/` directory
2. Reconstruct database from CSV/JSON exports
3. If no exports: Check broker API for trade history
4. Manual reconstruction as last resort

## Best Practices

1. **Test Restores Regularly**: Don't wait for disaster
2. **Monitor Disk Space**: Set up alerts at 80% usage
3. **Keep Multiple Generations**: Never delete all backups at once
4. **Verify Checksums**: Always verify backup integrity
5. **Document Changes**: Update this guide when backup strategy changes
6. **Off-Site Storage**: Consider external backup for monthly archives

## Troubleshooting

### Backup Script Fails

```bash
# Check database file permissions
ls -l data/trade_logs.db

# Check disk space
df -h data/

# Run backup manually with debug
python3 -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from pathlib import Path
from src.backup.scheduler import BackupScheduler, BackupPolicy
scheduler = BackupScheduler('data/trade_logs.db', Path('data/backups'))
scheduler.create_backup(BackupPolicy.DAILY, verify=True)
"
```

### Restore Fails Verification

```bash
# Check backup file integrity
python3 -c "
import sqlite3
conn = sqlite3.connect('data/backups/daily/trade_logs_daily_20240115.db')
cursor = conn.execute('PRAGMA integrity_check')
print(cursor.fetchone()[0])
"
```

### Health Check Fails

```python
from pathlib import Path
from src.backup.health_monitor import HealthMonitor

monitor = HealthMonitor('data/trade_logs.db', Path('data/backups'))

# Check each component individually
print("Database:", monitor.check_database_health())
print("Disk Space:", monitor.check_disk_space())
print("Backup Recency:", monitor.check_backup_recency())
```

## Contact

For backup/recovery issues:
- Check logs: `logs/backup.log`
- Review health status: Run health monitor
- Raise issue on GitHub if automated recovery fails
