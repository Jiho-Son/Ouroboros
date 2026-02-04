#!/usr/bin/env bash
# Restore script for The Ouroboros trading system
# Restores database from a backup file

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

# Check if backup directory exists
if [ ! -d "$BACKUP_DIR" ]; then
    log_error "Backup directory not found: $BACKUP_DIR"
    exit 1
fi

log_info "Available backups:"
log_info "=================="

# List available backups
$PYTHON -c "
from pathlib import Path
from src.backup.scheduler import BackupScheduler

scheduler = BackupScheduler(
    db_path='$DB_PATH',
    backup_dir=Path('$BACKUP_DIR')
)

backups = scheduler.list_backups()

if not backups:
    print('No backups found.')
    exit(1)

for i, backup in enumerate(backups, 1):
    size_mb = backup.size_bytes / 1024 / 1024
    print(f'{i}. [{backup.policy.value.upper()}] {backup.file_path.name}')
    print(f'   Date: {backup.timestamp.strftime(\"%Y-%m-%d %H:%M:%S UTC\")}')
    print(f'   Size: {size_mb:.2f} MB')
    print()
"

# Ask user to select backup
echo ""
read -p "Enter backup number to restore (or 'q' to quit): " BACKUP_NUM

if [ "$BACKUP_NUM" == "q" ]; then
    log_info "Restore cancelled"
    exit 0
fi

# Confirm restoration
log_warn "WARNING: This will replace the current database!"
log_warn "Current database will be backed up to: ${DB_PATH}.before_restore"
read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    log_info "Restore cancelled"
    exit 0
fi

# Perform restoration
$PYTHON -c "
from pathlib import Path
from src.backup.scheduler import BackupScheduler

scheduler = BackupScheduler(
    db_path='$DB_PATH',
    backup_dir=Path('$BACKUP_DIR')
)

backups = scheduler.list_backups()
backup_index = int('$BACKUP_NUM') - 1

if backup_index < 0 or backup_index >= len(backups):
    print('Invalid backup number')
    exit(1)

selected = backups[backup_index]
print(f'Restoring: {selected.file_path.name}')

scheduler.restore_backup(selected, verify=True)
print('Restore completed successfully')
"

if [ $? -eq 0 ]; then
    log_info "Database restored successfully"
else
    log_error "Restore failed"
    exit 1
fi
