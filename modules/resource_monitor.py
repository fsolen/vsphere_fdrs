from pyVmomi import vim
import time
import logging

logger = logging.getLogger('fdrs')

class ResourceMonitor:
    """
    Monitor resources (CPU, Memory, Disk I/O, Network I/O) of VMs and Hosts
    """

    def __init__(self, service_instance, config=None):
        self.service_instance = service_instance
        self.performance_manager = service_instance.content.perfManager
        self.counter_map = self._build_counter_map()
        self.config = config

    def _build_counter_map(self):
        """
        Builds a map of performance counter names to IDs.
        """
        counter_map = {}
        perf_dict = {}
        perfList = self.performance_manager.perfCounter
        for counter in perfList:
            perf_dict[counter.groupInfo.key + "." + counter.nameInfo.key] = counter.key
        counter_map['cpu.usage'] = perf_dict.get('cpu.usage')
        counter_map['mem.usage'] = perf_dict.get('mem.usage')
        counter_map['disk.usage'] = perf_dict.get('disk.usage')
        counter_map['net.usage'] = perf_dict.get('net.usage')
        return counter_map

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
