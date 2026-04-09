from pyVim import connect
from pyVmomi import vim
import ssl
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger('fdrs')

# Session keep-alive interval (seconds)
DEFAULT_KEEPALIVE_INTERVAL = 300  # 5 minutes


class ConnectionManager:
    """
    Handles connection and disconnection to vCenter Server.
    
    Features:
    - Session keep-alive to prevent timeout during long operations
    - Proper cleanup on disconnect
    
    Note: Uses unverified SSL context for lab/dev environments.
    For production, configure proper SSL certificate validation.
    """
    
    __slots__ = ('vcenter_ip', 'username', 'password', 'service_instance', 
                 '_keepalive_thread', '_keepalive_stop', '_keepalive_interval')

    def __init__(self, vcenter_ip: str, username: str, password: str, 
                 keepalive_interval: int = DEFAULT_KEEPALIVE_INTERVAL):
        self.vcenter_ip = vcenter_ip
        self.username = username
        self.password = password
        self.service_instance: Optional[vim.ServiceInstance] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._keepalive_stop = threading.Event()
        self._keepalive_interval = keepalive_interval

    def connect(self) -> vim.ServiceInstance:
        """
        Establishes a connection to vCenter and starts session keep-alive.
        
        Returns:
            vim.ServiceInstance: Connected vCenter service instance
        
        Raises:
            Exception: If connection fails
        """
        try:
            logger.info(f"[ConnectionManager] Connecting to vCenter {self.vcenter_ip}...")

            # Create an unverified SSL context (ignore SSL certs)
            context = ssl._create_unverified_context()

            self.service_instance = connect.SmartConnect(
                host=self.vcenter_ip,
                user=self.username,
                pwd=self.password,
                port=443,
                sslContext=context
            )

            if not self.service_instance:
                logger.error("[ConnectionManager] Failed to connect to vCenter!")
                raise Exception("Service instance is None")

            logger.info("[ConnectionManager] Successfully connected to vCenter!")
            
            # Start keep-alive thread
            self._start_keepalive()
            
            return self.service_instance

        except Exception as e:
            logger.error(f"[ConnectionManager] vCenter connection error: {e}")
            raise

    def _start_keepalive(self) -> None:
        """Start background thread for session keep-alive."""
        if self._keepalive_interval <= 0:
            logger.debug("[ConnectionManager] Session keep-alive disabled")
            return
            
        self._keepalive_stop.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            name="vCenter-KeepAlive",
            daemon=True
        )
        self._keepalive_thread.start()
        logger.debug(f"[ConnectionManager] Session keep-alive started (interval: {self._keepalive_interval}s)")

    def _keepalive_loop(self) -> None:
        """Keep-alive loop that pings vCenter periodically."""
        while not self._keepalive_stop.is_set():
            # Wait for interval or stop signal
            if self._keepalive_stop.wait(timeout=self._keepalive_interval):
                break  # Stop signal received
            
            # Ping vCenter to keep session alive
            try:
                if self.service_instance:
                    # Simple API call to keep session active
                    _ = self.service_instance.content.about.version
                    logger.debug("[ConnectionManager] Session keep-alive ping successful")
            except Exception as e:
                logger.warning(f"[ConnectionManager] Session keep-alive ping failed: {e}")

    def is_connected(self) -> bool:
        """Check if the connection is still active."""
        if not self.service_instance:
            return False
        try:
            # Try to access a simple property
            _ = self.service_instance.content.about.version
            return True
        except Exception:
            return False

    def disconnect(self) -> None:
        """
        Stops keep-alive and disconnects from vCenter.
        """
        # Stop keep-alive thread first
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_stop.set()
            self._keepalive_thread.join(timeout=2)
            logger.debug("[ConnectionManager] Session keep-alive stopped")
        
        try:
            if self.service_instance:
                connect.Disconnect(self.service_instance)
                self.service_instance = None
                logger.info("[ConnectionManager] Disconnected from vCenter cleanly.")
        except Exception as e:
            logger.error(f"[ConnectionManager] Error during disconnection: {e}")
