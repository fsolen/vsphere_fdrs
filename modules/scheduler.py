import logging
import time
from typing import List, Tuple, Optional
from pyVmomi import vim

logger = logging.getLogger('fdrs')

# Default timeout for vMotion operations (5 minutes)
DEFAULT_MIGRATION_TIMEOUT = 300


class Scheduler:
    """Executes planned VM migrations via vMotion."""
    
    __slots__ = ('connection_manager', 'dry_run', 'si', 'timeout')

    def __init__(self, connection_manager, dry_run: bool = False, timeout: int = DEFAULT_MIGRATION_TIMEOUT):
        self.connection_manager = connection_manager
        self.dry_run = dry_run
        self.si = connection_manager.service_instance
        self.timeout = timeout

    def execute_migrations(self, migrations: List[Tuple]) -> Tuple[int, int]:
        """
        Perform or simulate the VM migrations.
        
        Returns:
            Tuple of (successful_count, failed_count)
        """
        if not migrations:
            logger.info("[Scheduler] No migrations to perform.")
            return (0, 0)

        mode = "DRY-RUN" if self.dry_run else "REAL"
        logger.info(f"[Scheduler] Executing {len(migrations)} planned migrations. Mode: {mode}")

        success_count = 0
        fail_count = 0

        for vm, target_host in migrations:
            if hasattr(vm, 'config') and getattr(vm.config, 'template', False):
                logger.info(f"[Scheduler] Skipping template VM '{vm.name}'")
                continue
            try:
                if self.dry_run:
                    logger.info(f"[DRY-RUN] Would migrate VM '{vm.name}' -> Host '{target_host.name}'")
                    success_count += 1
                else:
                    self._migrate_vm(vm, target_host)
                    success_count += 1
            except Exception as e:
                logger.error(f"[Scheduler] Failed to migrate VM '{vm.name}': {e}")
                fail_count += 1

        logger.info(f"[Scheduler] Migration summary: {success_count} successful, {fail_count} failed")
        return (success_count, fail_count)

    def _migrate_vm(self, vm, target_host) -> None:
        """
        Actually migrate a VM to another host using vMotion.
        
        Raises:
            Exception: If migration fails or times out
        """
        source_host = vm.runtime.host.name if vm.runtime.host else "Unknown"
        logger.info(f"[Scheduler] Starting vMotion: '{vm.name}' from '{source_host}' -> '{target_host.name}'")

        relocate_spec = vim.vm.RelocateSpec()
        relocate_spec.host = target_host
        
        # Get resource pool from target host's cluster
        if hasattr(target_host.parent, 'resourcePool'):
            relocate_spec.pool = target_host.parent.resourcePool

        task = vm.RelocateVM_Task(spec=relocate_spec)
        self._wait_for_task(task, f"vMotion {vm.name}")

        logger.info(f"[Scheduler] vMotion completed: '{vm.name}' now on '{target_host.name}'")

    def _wait_for_task(self, task, action_name: str, poll_interval: float = 1.0) -> None:
        """
        Wait for a vCenter task to complete with timeout.
        
        Args:
            task: vCenter task object
            action_name: Description of the task for logging
            poll_interval: Seconds between status checks
        
        Raises:
            TimeoutError: If task exceeds timeout
            Exception: If task fails
        """
        logger.debug(f"[Scheduler] Waiting for task: {action_name}...")
        elapsed = 0
        
        while task.info.state in ('running', 'queued'):
            if elapsed >= self.timeout:
                raise TimeoutError(f"Task '{action_name}' timed out after {self.timeout}s")
            time.sleep(poll_interval)
            elapsed += poll_interval
            
            # Log progress every 30 seconds
            if elapsed % 30 == 0:
                logger.debug(f"[Scheduler] Task '{action_name}' still running ({elapsed}s elapsed)")

        if task.info.state == 'success':
            logger.debug(f"[Scheduler] Task '{action_name}' completed in {elapsed}s")
        else:
            error_msg = str(task.info.error) if task.info.error else "Unknown error"
            raise Exception(f"Task '{action_name}' failed: {error_msg}")
