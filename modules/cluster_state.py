from pyVmomi import vim
import logging

logger = logging.getLogger('fdrs')

class ClusterState:
    def __init__(self, service_instance, cluster_name=None):
        self.service_instance = service_instance
        self.cluster_name = cluster_name  # Optional: filter by specific cluster name
        self.vms = self._get_all_vms()
        self.hosts = self._get_all_hosts()

    def _get_all_vms(self):
        """Get all VMs in the datacenter, optionally filtered by cluster."""
        content = self.service_instance.RetrieveContent()
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        vms = container.view
        container.Destroy()
        
        # Filter out templates and powered off VMs
        active_vms = [vm for vm in vms if not vm.config.template and vm.runtime.powerState == 'poweredOn']
        
        # If cluster_name is specified, filter VMs to only those in the specified cluster
        if self.cluster_name:
            filtered_vms = []
            for vm in active_vms:
                try:
                    # Get the host this VM is running on, then check the host's cluster
                    if vm.runtime.host and vm.runtime.host.parent and hasattr(vm.runtime.host.parent, 'name'):
                        if vm.runtime.host.parent.name == self.cluster_name:
                            filtered_vms.append(vm)
                except Exception as e:
                    logger.debug(f"Could not determine cluster for VM {vm.name}: {e}")
            
            return filtered_vms
        
        return active_vms

    def _get_all_hosts(self):
        """Get all ESXi hosts in the datacenter."""
        content = self.service_instance.RetrieveContent()
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.HostSystem], True
        )
        hosts = container.view
        container.Destroy()
        
        # Filter out hosts that are not in connected state
        connected_hosts = [host for host in hosts if host.runtime.connectionState == 'connected']
        
        # If cluster_name is specified, filter by cluster
        if self.cluster_name:
            filtered_hosts = []
            for host in connected_hosts:
                try:
                    if host.parent and hasattr(host.parent, 'name') and host.parent.name == self.cluster_name:
                        filtered_hosts.append(host)
                except Exception as e:
                    logger.debug(f"Could not determine cluster for host {host.name}: {e}")
            
            if not filtered_hosts:
                logger.warning(f"[ClusterState] No hosts found in cluster '{self.cluster_name}'")
            else:
                logger.info(f"[ClusterState] Filtered to {len(filtered_hosts)} hosts in cluster '{self.cluster_name}'")
            
            return filtered_hosts
        
        return connected_hosts

    def get_cluster_state(self):
        """
        Get the current state of the cluster including all VMs and hosts with their metrics.
        Returns a dictionary with cluster state information.
        """
        if not hasattr(self, 'vms') or not hasattr(self, 'hosts'):
            self.vms = self._get_all_vms()
            self.hosts = self._get_all_hosts()
            
        cluster_state = {
            'vms': [],
            'hosts': [],
            'total_metrics': {
                'cpu_usage': 0,
                'memory_usage': 0,
                'disk_io_usage': 0,
                'network_io_usage': 0
            }
        }

        # Aggregate VM metrics
        for vm_obj in self.vms:
            vm_metrics_data = self.vm_metrics.get(vm_obj.name, {})
            vm_info = {
                'name': vm_obj.name,
                'host': self.get_host_of_vm(vm_obj),
                'cpu_usage': vm_metrics_data.get('cpu_usage_abs', 0),
                'memory_usage': vm_metrics_data.get('memory_usage_abs', 0),
                'disk_io_usage': vm_metrics_data.get('disk_io_usage_abs', 0),
                'network_io_usage': vm_metrics_data.get('network_io_usage_abs', 0)
            }
            cluster_state['vms'].append(vm_info)
            
            # Add to totals using the same keys
            for metric_key in ['cpu_usage', 'memory_usage', 'disk_io_usage', 'network_io_usage']:
                cluster_state['total_metrics'][metric_key] += vm_info[metric_key]

        # Aggregate host metrics
        for host_obj in self.hosts: # Renamed host to host_obj
            host_metrics_data = self.host_metrics.get(host_obj.name, {})
            host_info = {
                'name': host_obj.name,
                'cpu_usage': host_metrics_data.get('cpu_usage', 0),
                'memory_usage': host_metrics_data.get('memory_usage', 0),
                'disk_io_usage': host_metrics_data.get('disk_io_usage', 0),
                'network_io_usage': host_metrics_data.get('network_io_usage', 0),
                'cpu_capacity': host_metrics_data.get('cpu_capacity', 0),
                'memory_capacity': host_metrics_data.get('memory_capacity', 0),
                'disk_io_capacity': host_metrics_data.get('disk_io_capacity', 0),
                'network_capacity': host_metrics_data.get('network_capacity', 0)
            }
            cluster_state['hosts'].append(host_info)

        return cluster_state
        
    def get_host_of_vm(self, vm_object):
        """
        Given a VM object, return the name of the host it is running on.
        """
        try:
            # Use vm_object consistently
            if hasattr(vm_object, 'runtime') and hasattr(vm_object.runtime, 'host') and vm_object.runtime.host:
                return vm_object.runtime.host
            else:
                logger.warning(f"VM '{vm_object.name}' does not have a valid host reference.")
                return None
        except Exception as e:
            logger.error(f"Error getting host for VM '{getattr(vm_object, 'name', str(vm_object))}': {e}")
            return None

    def annotate_vms_with_metrics(self, resource_monitor):
        """
        Build a dictionary mapping VM names to their absolute resource consumption metrics.
        These metrics represent actual resource usage that will be used to calculate host loads.
        Uses ResourceMonitor for I/O metrics and vm.summary.quickStats for absolute CPU/Memory.
        """
        self.vm_metrics = {}
        logger.info("[ClusterState] Starting annotation of VMs with metrics...")
        for vm_obj in self.vms:
            vm_name_for_log = "UnknownVMObject" # Default
            try:
                vm_name_for_log = getattr(vm_obj, 'name', 'UnknownVMObject_NoNameAttr')
                logger.debug(f"[ClusterState.annotate_vms] Processing VM: {vm_name_for_log}, Type: {type(vm_obj)}")
                
                if not hasattr(vm_obj, '_moId') or vm_obj._moId is None:
                    logger.warning(f"[ClusterState.annotate_vms] VM {vm_name_for_log} has missing or None _moId. Skipping its metric annotation.")
                    continue
                
                # Original logic for a VM
                rm_vm_metrics = resource_monitor.get_vm_metrics(vm_obj)
                self.vm_metrics[vm_obj.name] = {
                    'cpu_usage_abs': vm_obj.summary.quickStats.overallCpuUsage or 0,
                    'memory_usage_abs': vm_obj.summary.quickStats.guestMemoryUsage or 0,
                    'disk_io_usage_abs': rm_vm_metrics.get('disk_io_usage', 0.0),
                    'network_io_usage_abs': rm_vm_metrics.get('network_io_usage', 0.0),
                    'vm_obj': vm_obj
                }
            except AttributeError as ae:
                logger.error(f"[ClusterState.annotate_vms] AttributeError while processing VM '{vm_name_for_log}' (Type: {type(vm_obj)}): {ae}")
                continue 
            except Exception as e:
                logger.error(f"[ClusterState.annotate_vms] Unexpected error while processing VM '{vm_name_for_log}' (Type: {type(vm_obj)}): {e}")
                continue

        logger.info("[ClusterState] Finished annotation of VMs with metrics.")

    def annotate_hosts_with_metrics(self, resource_monitor):
        """
        Calculate host metrics by summing the resource consumption of VMs running on each host.
        Also incorporates capacity information obtained directly or via ResourceMonitor for consistency.
        """
        self.host_metrics = {}
        logger.info("[ClusterState] Starting annotation of hosts with metrics...")
        for host_obj in self.hosts:
            host_name_for_log = "UnknownHostObject" # Default
            try:
                host_name_for_log = getattr(host_obj, 'name', 'UnknownHostObject_NoNameAttr')
                logger.debug(f"[ClusterState.annotate_hosts] Processing host: {host_name_for_log}, Type: {type(host_obj)}")

                if not hasattr(host_obj, '_moId') or host_obj._moId is None:
                    logger.warning(f"[ClusterState.annotate_hosts] Host {host_name_for_log} has missing or None _moId. Skipping its metric annotation.")
                    continue
                
                # Original logic for a host
                rm_host_metrics = resource_monitor.get_host_metrics(host_obj)
                current_host_metrics = {
                    'cpu_usage': 0, # Summed from VMs for now
                    'memory_usage': host_obj.summary.quickStats.overallMemoryUsage if host_obj.summary and host_obj.summary.quickStats else 0, # Directly use host's overall memory usage
                    'disk_io_usage': 0, # Summed from VMs
                    'network_io_usage': 0, # Summed from VMs
                    'cpu_capacity': rm_host_metrics.get('cpu_capacity', 0),
                    'memory_capacity': rm_host_metrics.get('memory_capacity', 0),
                    'disk_io_capacity': rm_host_metrics.get('disk_io_capacity', 1),
                    'network_capacity': rm_host_metrics.get('network_capacity', 1),
                    'vms': [],
                    'host_obj': host_obj,
                    'cluster_name': "N/A" # Default cluster name
                }
                
                # Get cluster name
                try:
                    if host_obj.parent and hasattr(host_obj.parent, 'name'):
                        current_host_metrics['cluster_name'] = host_obj.parent.name
                    else:
                        logger.debug(f"Host {host_name_for_log} parent or parent.name not found. Defaulting cluster_name.")
                except AttributeError as e:
                    logger.warning(f"AttributeError getting cluster name for host {host_name_for_log}: {e}. Defaulting cluster_name.")
                except Exception as e: # Catch any other unexpected errors
                    logger.error(f"Unexpected error getting cluster name for host {host_name_for_log}: {e}. Defaulting cluster_name.")

                for vm_on_host in self.get_vms_on_host(host_obj):
                    vm_metrics_data = self.vm_metrics.get(vm_on_host.name, {})
                    # ... (summation logic) ...
                    current_host_metrics['cpu_usage'] += vm_metrics_data.get('cpu_usage_abs', 0)
                    # The line for memory_usage summation is now removed.
                    current_host_metrics['disk_io_usage'] += vm_metrics_data.get('disk_io_usage_abs', 0)
                    current_host_metrics['network_io_usage'] += vm_metrics_data.get('network_io_usage_abs', 0)
                    current_host_metrics['vms'].append(vm_on_host.name)

                current_host_metrics['cpu_usage_pct'] = (current_host_metrics['cpu_usage'] / current_host_metrics['cpu_capacity'] * 100) \
                    if current_host_metrics['cpu_capacity'] > 0 else 0
                current_host_metrics['memory_usage_pct'] = (current_host_metrics['memory_usage'] / current_host_metrics['memory_capacity'] * 100) \
                    if current_host_metrics['memory_capacity'] > 0 else 0
                
                self.host_metrics[host_obj.name] = current_host_metrics
                
                logger.debug(f"Host {host_obj.name} annotated metrics:")
                logger.debug(f"  CPU: {current_host_metrics['cpu_usage_pct']:.1f}% ({current_host_metrics['cpu_usage']}/{current_host_metrics['cpu_capacity']} MHz)")
                logger.debug(f"  Memory: {current_host_metrics['memory_usage_pct']:.1f}% ({current_host_metrics['memory_usage']}/{current_host_metrics['memory_capacity']} MB)")
                logger.debug(f"  Disk I/O: {current_host_metrics['disk_io_usage']:.1f} MBps (Capacity: {current_host_metrics['disk_io_capacity']:.1f} MBps)")
                logger.debug(f"  Network I/O: {current_host_metrics['network_io_usage']:.1f} MBps (Capacity: {current_host_metrics['network_capacity']:.1f} MBps)")
                logger.debug(f"  VMs: {', '.join(current_host_metrics['vms'])}\n")

            except AttributeError as ae:
                logger.error(f"[ClusterState.annotate_hosts] AttributeError while processing host '{host_name_for_log}' (Type: {type(host_obj)}): {ae}")
                continue
            except Exception as e:
                logger.error(f"[ClusterState.annotate_hosts] Unexpected error while processing host '{host_name_for_log}' (Type: {type(host_obj)}): {e}")
                continue
        logger.info("[ClusterState] Finished annotation of hosts with metrics.")

    def get_vms_on_host(self, host_object):
        """
        Return list of VMs currently running on the specified host.
        """
        vms_on_host = []
        # Ensure host_object is valid and has _moId before proceeding
        if not hasattr(host_object, '_moId') or not host_object._moId:
            host_identifier = getattr(host_object, 'name', str(host_object))
            logger.warning(f"[ClusterState.get_vms_on_host] Provided host_object '{host_identifier}' is invalid or has no _moId. Cannot find VMs.")
            return vms_on_host

        for vm_obj in self.vms:
            host_of_vm = self.get_host_of_vm(vm_obj) # This returns a vim.HostSystem object or None
            
            # Ensure host_of_vm is valid and has _moId before comparison
            if host_of_vm and hasattr(host_of_vm, '_moId') and host_of_vm._moId:
                if host_of_vm._moId == host_object._moId:
                    vms_on_host.append(vm_obj)
            elif host_of_vm: # host_of_vm exists but lacks _moId or it's None
                vm_identifier = getattr(vm_obj, 'name', str(vm_obj))
                host_of_vm_identifier = getattr(host_of_vm, 'name', str(host_of_vm))
                logger.warning(f"[ClusterState.get_vms_on_host] Host '{host_of_vm_identifier}' for VM '{vm_identifier}' is invalid or has no _moId. Skipping for host comparison.")
                
        return vms_on_host
        
    def get_vm_by_name(self, vm_name):
        """
        Return the VM object with the given name, or None if not found.
        """
        for vm_obj in self.vms:
            if vm_obj.name == vm_name:
                return vm_obj
        return None

    def get_host_by_name(self, host_name):
        """
        Return the host object with the given name from self.hosts.
        Returns None if not found.
        """
        if not hasattr(self, 'hosts') or not self.hosts:
            logger.warning("[ClusterState.get_host_by_name] self.hosts is not initialized or is empty.")
            return None
            
        for host_obj in self.hosts:
            if hasattr(host_obj, 'name') and host_obj.name == host_name:
                return host_obj
        
        logger.warning(f"[ClusterState.get_host_by_name] Host '{host_name}' not found in self.hosts.")
        return None

    def log_cluster_stats(self):
        """Log detailed cluster statistics including resource distribution"""
        if not hasattr(self, 'host_metrics') or not hasattr(self, 'vm_metrics'):
            logger.warning("Metrics not yet collected. Run update_metrics() first.")
            return

        total_cpu_capacity = 0
        total_mem_capacity = 0
        total_cpu_usage = 0
        total_mem_usage = 0
        total_disk_io = 0
        total_net_io = 0
        

        logger.info("\n--- Host Summary ---")

        header = f"{'Cluster Name':<30} {'Hostname':<25} {'CPU %':<10} {'Mem %':<10} {'Storage I/O (MBps)':<20} {'Net Throughput (MBps)':<25} {'VM Count':<10}"
        logger.info(header)
        logger.info("-" * len(header))

        for host_name, metrics in self.host_metrics.items():
            cluster_name_to_log = metrics.get('cluster_name', 'N/A')
            logger.info(f"{cluster_name_to_log:<30} {host_name:<25} {metrics.get('cpu_usage_pct', 0):<10.1f} {metrics.get('memory_usage_pct', 0):<10.1f} {metrics.get('disk_io_usage', 0):<20.1f} {metrics.get('network_io_usage', 0):<25.1f} {len(metrics.get('vms', [])):<10}")
        
        # Host-level statistics (this section title will now follow the summary table)
        logger.info("\n--- Host Resource Distribution ---")
        for host_name, metrics in self.host_metrics.items():
            total_cpu_capacity += metrics['cpu_capacity']
            total_mem_capacity += metrics['memory_capacity']
            total_cpu_usage += metrics['cpu_usage']
            total_mem_usage += metrics['memory_usage']
            total_disk_io += metrics['disk_io_usage']
            total_net_io += metrics['network_io_usage']
            
            logger.info(f"Host: {host_name}")
            logger.info(f"├─ CPU: {metrics['cpu_usage_pct']:.1f}% ({metrics['cpu_usage']}/{metrics['cpu_capacity']} MHz)")
            logger.info(f"├─ Memory: {metrics['memory_usage_pct']:.1f}% ({metrics['memory_usage']}/{metrics['memory_capacity']} MB)")
            logger.info(f"├─ Disk I/O: {metrics['disk_io_usage']:.1f} MBps")
            logger.info(f"├─ Network I/O: {metrics['network_io_usage']:.1f} MBps")
            logger.info(f"└─ VMs: {len(metrics['vms'])} ({', '.join(metrics['vms'])})")

        # VM distribution analysis
        logger.info("\n--- VM Resource Consumption ---")
        for vm_name, metrics in self.vm_metrics.items():
            host_obj = self.get_host_of_vm(metrics['vm_obj'])
            host_display_name = host_obj.name if host_obj and hasattr(host_obj, 'name') else 'Unknown'
            logger.info(f"VM: {vm_name} (on {host_display_name})")
            logger.info(f"├─ CPU: {metrics.get('cpu_usage_abs', 0)} MHz")
            logger.info(f"├─ Memory: {metrics.get('memory_usage_abs', 0)} MB")
            logger.info(f"├─ Disk I/O: {metrics.get('disk_io_usage_abs', 0):.1f} MBps")
            logger.info(f"└─ Network I/O: {metrics.get('network_io_usage_abs', 0):.1f} MBps")

        # Overall cluster metrics
        cluster_cpu_usage = (total_cpu_usage / total_cpu_capacity * 100) if total_cpu_capacity > 0 else 0
        cluster_mem_usage = (total_mem_usage / total_mem_capacity * 100) if total_mem_capacity > 0 else 0
        
        logger.info("\n--- Cluster Total Resource Usage ---")
        logger.info(f"CPU: {cluster_cpu_usage:.1f}% ({total_cpu_usage}/{total_cpu_capacity} MHz)")
        logger.info(f"Memory: {cluster_mem_usage:.1f}% ({total_mem_usage}/{total_mem_capacity} MB)")
        logger.info(f"Total Disk I/O: {total_disk_io:.1f} MBps")
        logger.info(f"Total Network I/O: {total_net_io:.1f} MBps")
        logger.info(f"Total Hosts: {len(self.hosts)}")
        logger.info(f"Total VMs: {len(self.vms)}\n")

    def update_metrics(self, resource_monitor=None):
        """Update VM and Host metrics"""
        if resource_monitor is None:
            from .resource_monitor import ResourceMonitor # Keep local import for safety
            logger.warning("ResourceMonitor not provided to update_metrics, creating a new instance. This is not recommended for production.")
            resource_monitor = ResourceMonitor(self.service_instance) 

        self.annotate_vms_with_metrics(resource_monitor)
        self.annotate_hosts_with_metrics(resource_monitor)
        self.log_cluster_stats() 
