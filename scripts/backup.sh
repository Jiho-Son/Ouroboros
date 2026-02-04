#!/usr/bin/env bash
# Automated backup script for The Ouroboros trading system
# Runs daily/weekly/monthly backups

set -euo pipefail

# Configuration
DB_PATH="${DB_PATH:-data/trade_logs.db}"
BACKUP_DIR="${BACKUP_DIR:-data/backups}"
PYTHON="${PYTHON:-python3}"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    log_error "Database not found: $DB_PATH"
    exit 1
fi

# Create backup directory
mkdir -p "$BACKUP_DIR"

log_info "Starting backup process..."
log_info "Database: $DB_PATH"
log_info "Backup directory: $BACKUP_DIR"

# Determine backup policy based on day of week and month
DAY_OF_WEEK=$(date +%u)  # 1-7 (Monday-Sunday)
DAY_OF_MONTH=$(date +%d)

if [ "$DAY_OF_MONTH" == "01" ]; then
    POLICY="monthly"
    log_info "Running MONTHLY backup (first day of month)"
elif [ "$DAY_OF_WEEK" == "7" ]; then
    POLICY="weekly"
    log_info "Running WEEKLY backup (Sunday)"
else
    POLICY="daily"
    log_info "Running DAILY backup"
fi

# Run Python backup script
$PYTHON -c "
from pathlib import Path
from src.backup.scheduler import BackupScheduler, BackupPolicy
from src.backup.health_monitor import HealthMonitor

# Create scheduler
scheduler = BackupScheduler(
    db_path='$DB_PATH',
    backup_dir=Path('$BACKUP_DIR')
)

# Create backup
policy = BackupPolicy.$POLICY.upper()
metadata = scheduler.create_backup(policy, verify=True)
print(f'Backup created: {metadata.file_path}')
print(f'Size: {metadata.size_bytes / 1024 / 1024:.2f} MB')
print(f'Checksum: {metadata.checksum}')

# Cleanup old backups
removed = scheduler.cleanup_old_backups()
total_removed = sum(removed.values())
if total_removed > 0:
    print(f'Removed {total_removed} old backup(s)')

# Health check
monitor = HealthMonitor('$DB_PATH', Path('$BACKUP_DIR'))
status = monitor.get_overall_status()
print(f'System health: {status.value}')
"

if [ $? -eq 0 ]; then
    log_info "Backup completed successfully"
else
    log_error "Backup failed"
    exit 1
fi

log_info "Backup process finished"
