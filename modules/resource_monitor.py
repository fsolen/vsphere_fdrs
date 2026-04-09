from pyVmomi import vim
import logging
from typing import Dict, Optional, Any, List, Tuple

logger = logging.getLogger('fdrs')

# Default capacities when config is not available
DEFAULT_DISK_IO_CAPACITY = 4000  # MBps
DEFAULT_NETWORK_CAPACITY = 1250.0  # MBps

# Metrics we need to fetch
METRICS_MAP = {
    "cpu_usage": "cpu.usage",       # Percentage (0-10000)
    "memory_usage": "mem.usage",    # Percentage (0-10000)
    "disk_io_usage": "disk.usage",  # KBps
    "network_io_usage": "net.usage" # KBps
}


class ResourceMonitor:
    """
    Monitor resources (CPU, Memory, Disk I/O, Network I/O) of VMs and Hosts.
    Uses vSphere Performance Manager for metrics collection with batch queries.
    """
    
    __slots__ = ('service_instance', 'performance_manager', 'counter_map', 'config', '_metric_cache')

    def __init__(self, service_instance, config=None):
        self.service_instance = service_instance
        self.performance_manager = service_instance.content.perfManager
        self.counter_map = self._build_counter_map()
        self.config = config
        self._metric_cache: Dict[str, Dict] = {}  # Cache for repeated queries

    def _build_counter_map(self) -> Dict[str, Optional[int]]:
        """
        Builds a map of performance counter names to IDs.
        Only builds the counters we need for efficiency.
        """
        counter_map = {}
        perf_counters = self.performance_manager.perfCounter
        
        # Build a lookup dict once
        perf_dict = {
            f"{counter.groupInfo.key}.{counter.nameInfo.key}": counter.key
            for counter in perf_counters
        }
        
        # Map only the counters we need
        needed_counters = ['cpu.usage', 'mem.usage', 'disk.usage', 'net.usage']
        for counter_name in needed_counters:
            counter_map[counter_name] = perf_dict.get(counter_name)
            
        return counter_map

    def _get_batch_performance_data(self, entities: List, metric_names: List[str], interval: int = 20) -> Dict[str, Dict[str, float]]:
        """
        Batch query performance data for multiple entities and metrics.
        
        This is significantly more efficient than individual queries when processing
        many VMs or hosts, reducing API round-trips.
        
        Args:
            entities: List of VM or Host managed objects
            metric_names: List of metric names (e.g., ['cpu.usage', 'mem.usage'])
            interval: Performance interval in seconds
        
        Returns:
            Dict mapping entity names to metric values:
            {'entity_name': {'cpu.usage': 50.0, 'mem.usage': 30.0, ...}, ...}
        """
        if not entities or not metric_names:
            return {}

        # Build metric IDs for all requested metrics
        metric_ids = []
        for metric_name in metric_names:
            metric_id = self.counter_map.get(metric_name)
            if metric_id:
                metric_ids.append(vim.PerformanceManager.MetricId(counterId=metric_id, instance=''))

        if not metric_ids:
            logger.warning("[ResourceMonitor] No valid metric IDs found for batch query")
            return {}

        # Build query specs for all valid entities
        query_specs = []
        entity_name_map = {}  # Map index to entity name for result processing
        
        for entity in entities:
            entity_name = getattr(entity, 'name', None)
            if not entity_name:
                continue
            if not hasattr(entity, '_moId') or entity._moId is None:
                logger.debug(f"[ResourceMonitor] Entity '{entity_name}' has no valid _moId, skipping batch query")
                continue
                
            query_specs.append(
                vim.PerformanceManager.QuerySpec(
                    entity=entity,
                    metricId=metric_ids,
                    intervalId=interval,
                    maxSample=1
                )
            )
            entity_name_map[len(query_specs) - 1] = entity_name

        if not query_specs:
            return {}

        # Execute batch query
        results = {}
        try:
            query_results = self.performance_manager.QueryPerf(querySpec=query_specs)
            
            for idx, entity_result in enumerate(query_results):
                entity_name = entity_name_map.get(idx)
                if not entity_name:
                    continue
                    
                results[entity_name] = {}
                if entity_result.value:
                    for metric_series in entity_result.value:
                        # Find the metric name from counter ID
                        counter_id = metric_series.id.counterId
                        metric_name = None
                        for name, cid in self.counter_map.items():
                            if cid == counter_id:
                                metric_name = name
                                break
                        
                        if metric_name and metric_series.value:
                            results[entity_name][metric_name] = metric_series.value[0] if metric_series.value[0] is not None else 0
                
                # Fill in missing metrics with 0
                for metric_name in metric_names:
                    if metric_name not in results[entity_name]:
                        results[entity_name][metric_name] = 0
                        
            logger.debug(f"[ResourceMonitor] Batch query completed for {len(results)} entities")
            
        except Exception as e:
            logger.warning(f"[ResourceMonitor] Batch query failed: {e}. Falling back to individual queries.")
            # Fall back to individual queries
            for entity in entities:
                entity_name = getattr(entity, 'name', None)
                if entity_name:
                    results[entity_name] = {}
                    for metric_name in metric_names:
                        results[entity_name][metric_name] = self._get_performance_data(entity, metric_name, interval)
        
        return results

    def get_batch_vm_metrics(self, vms: List) -> Dict[str, Dict[str, float]]:
        """
        Get metrics for multiple VMs in a single batch query.
        
        Args:
            vms: List of VM managed objects
        
        Returns:
            Dict mapping VM names to their metrics
        """
        metric_names = list(METRICS_MAP.values())
        raw_metrics = self._get_batch_performance_data(vms, metric_names)
        
        # Convert raw metrics to expected format
        processed = {}
        for vm_name, metrics in raw_metrics.items():
            processed[vm_name] = {
                'cpu_usage': (metrics.get('cpu.usage', 0) or 0) / 100.0,
                'memory_usage': (metrics.get('mem.usage', 0) or 0) / 100.0,
                'disk_io_usage': (metrics.get('disk.usage', 0) or 0) / 1024.0,  # KBps to MBps
                'network_io_usage': (metrics.get('net.usage', 0) or 0) / 1024.0  # KBps to MBps
            }
        return processed

    def _get_performance_data(self, entity, metric_name, interval=20):
        content = self.service_instance.RetrieveContent() 
        metric_id = self.counter_map.get(metric_name)

        entity_name_for_log = getattr(entity, 'name', str(entity))

        if isinstance(entity, str):
            logger.error(f"[_get_performance_data] Entity for metric '{metric_name}' is a STRING, cannot query.")
            return 0

        if not hasattr(entity, '_moId') or entity._moId is None:
            logger.warning(f"[_get_performance_data] Entity '{entity_name_for_log}' has no valid _moId.")
            return 0

        if not metric_id:
            logger.warning(f"Metric ID for {metric_name} not found in counter map for entity {entity_name_for_log}!")
            return 0

        query_spec_list = [
            vim.PerformanceManager.QuerySpec(
                entity=entity,
                metricId=[vim.PerformanceManager.MetricId(counterId=metric_id, instance='')],
                intervalId=interval,
                maxSample=1
            )
        ]

        try:
            query_results = self.performance_manager.QueryPerf(querySpec=query_spec_list)
            
            if query_results and len(query_results) > 0:
                metric_series_list = query_results[0].value
                if metric_series_list and len(metric_series_list) > 0:
                    metric_series = metric_series_list[0]
                    if hasattr(metric_series, 'value') and metric_series.value and len(metric_series.value) > 0:
                        scalar_value = metric_series.value[0]
                        return scalar_value if scalar_value is not None else 0
            return 0
        except Exception as e:
            logger.warning(f"Error fetching {metric_name} for {entity_name_for_log}: {e}")
            return 0

    def get_vm_metrics(self, vm):
        vm_metrics = {}
        metrics_to_fetch = { 				# Renamed 'metrics' to 'metrics_to_fetch' to avoid confusion
            "cpu_usage": "cpu.usage",       # Percentage
            "memory_usage": "mem.usage",    # Percentage
            "disk_io_usage": "disk.usage",  # KBps (disk.read/write aggregated)
            "network_io_usage": "net.usage" # KBps (tx/rx aggregated)
        }

        for metric_key, counter_key in metrics_to_fetch.items():
            scalar_metric_value = self._get_performance_data(vm, counter_key)

            if scalar_metric_value is None:
                scalar_metric_value = 0.0

            if metric_key == "cpu_usage":
                vm_metrics[metric_key] = scalar_metric_value / 100.0
            elif metric_key == "memory_usage":
                vm_metrics[metric_key] = scalar_metric_value / 100.0
            elif metric_key == "disk_io_usage": # Counter is in KBps
                vm_metrics[metric_key] = scalar_metric_value / 1024.0 # Convert to MBps
            elif metric_key == "network_io_usage": # Counter is in KBps
                vm_metrics[metric_key] = scalar_metric_value / 1024.0 # Convert to MBps
            else:
                vm_metrics[metric_key] = scalar_metric_value

        return vm_metrics

    def get_host_metrics(self, host):
        host_metrics = {}
        metrics_to_fetch = {
            "cpu_usage": "cpu.usage",       # Percentage
            "memory_usage": "mem.usage",    # Percentage
            "disk_io_usage": "disk.usage",  # KBps
            "network_io_usage": "net.usage" # KBps
        }

        for metric_key, counter_key in metrics_to_fetch.items():
            scalar_metric_value = self._get_performance_data(host, counter_key)

            if scalar_metric_value is None: 
                scalar_metric_value = 0.0

            if metric_key == "cpu_usage":      # Counter value is 0-10000
                host_metrics[metric_key] = scalar_metric_value / 100.0
            elif metric_key == "memory_usage": # Counter value is 0-10000
                host_metrics[metric_key] = scalar_metric_value / 100.0
            elif metric_key == "disk_io_usage": # KBps
                host_metrics[metric_key] = scalar_metric_value / 1024.0 # Convert to MBps
            elif metric_key == "network_io_usage": # KBps
                host_metrics[metric_key] = scalar_metric_value / 1024.0 # Convert to MBps
            else:
                host_metrics[metric_key] = scalar_metric_value

        # Add capacity information
        try:
            host_metrics["cpu_capacity"] = host.summary.hardware.numCpuCores * host.summary.hardware.cpuMhz
            host_metrics["memory_capacity"] = host.summary.hardware.memorySize / (1024 * 1024)  # Convert B to MB
            
            # Disk I/O capacity from config (default: 4000 MBps for 2x32 Gbit SAN)
            if self.config:
                host_metrics["disk_io_capacity"] = self.config.get_storage_disk_io_capacity()
            else:
                host_metrics["disk_io_capacity"] = 4000  # Fallback default
            
            # Network capacity calculation (from config or default)
            if self.config:
                network_capacity_val = self.config.get_network_bandwidth()
                logger.debug(f"[ResourceMonitor] Using network bandwidth from config: {network_capacity_val} MBps for host '{host.name}'.")
            else:
                network_capacity_val = 1250.0  # Default: 1250 MBps (assumes dual 10GbE)
            if (host.config and hasattr(host.config, 'network') and 
                host.config.network and hasattr(host.config.network, 'pnic') and 
                host.config.network.pnic):
                pnics = host.config.network.pnic
                try:
                    valid_link_speeds = []
                    for pnic_obj in pnics: 
                        if hasattr(pnic_obj, 'linkSpeed') and \
                           pnic_obj.linkSpeed is not None and \
                           hasattr(pnic_obj.linkSpeed, 'speedMb') and \
                           isinstance(pnic_obj.linkSpeed.speedMb, int):
                            valid_link_speeds.append(pnic_obj.linkSpeed.speedMb)
                        elif hasattr(pnic_obj, 'linkSpeed') and pnic_obj.linkSpeed is not None and hasattr(pnic_obj.linkSpeed, 'speedMb'):
                            logger.warning(f"Host '{host.name}', pNIC '{pnic_obj.device}': linkSpeed.speedMb found but is not an integer (type: {type(pnic_obj.linkSpeed.speedMb)} value: {pnic_obj.linkSpeed.speedMb}). Skipping this pNIC for network capacity sum.")

                    if valid_link_speeds:
                        total_link_speed_mbps = sum(valid_link_speeds) 
                        network_capacity_val = total_link_speed_mbps / 8.0 
                        if network_capacity_val == 0: 
                            logger.warning(f"Host '{host.name}': Sum of valid pNIC link speeds is 0. Defaulting network capacity.")
                            network_capacity_val = 1250.0
                    else:
                        logger.warning(f"Host '{host.name}': No valid integer link speeds (speedMb) found for pNICs. Defaulting network capacity.")
                except Exception as e_pnic: # Catch errors during pNIC processing
                    logger.warning(f"Host '{host.name}': Error calculating network capacity from pNICs: {e_pnic}. Defaulting network capacity.")
                    # network_capacity_val remains 1250.0 (default set at start of network calc block)
            else:
                logger.warning(f"Host '{host.name}': Could not retrieve pNIC information. Defaulting network capacity.")
            host_metrics["network_capacity"] = network_capacity_val

        except Exception as e:
            logger.error(f"[ResourceMonitor.get_host_metrics] Error fetching capacity for host '{getattr(host, 'name', str(host))}': {e}. Capacities will be defaulted.")
            host_metrics["cpu_capacity"] = 0
            host_metrics["memory_capacity"] = 0
            host_metrics["disk_io_capacity"] = 1 # Used 1 to prevent potential division by zero
            host_metrics["network_capacity"] = 1 # Used 1 to prevent potential division by zero
            
        return host_metrics
