import argparse
import ctypes
import ipaddress
import json
import pprint
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


PRESETS_FILE = Path(__file__).with_name("network_presets.json")


def run_powershell(script):
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def get_network_adapters():
    script = r"""
    $ErrorActionPreference = 'Stop'

    # Pull directly from .NET network stack (bypasses CIM/WMI and CDXML completely)
    $interfaces = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()

    $result = foreach ($nic in $interfaces) {
        $props = $nic.GetIPProperties()
        
        # Filter down to operational or IP-enabled ones if desired, or grab all
        $ipv4Addresses = @()
        foreach ($addr in $props.UnicastAddresses) {
            if ($addr.Address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork) {
                $ipv4Addresses += [pscustomobject]@{
                    IPAddress    = $addr.Address.ToString()
                    PrefixLength = 0 # .NET doesn't expose prefix length directly here without registry/mask math, safe to omit or calculate if needed
                }
            }
        }

        [pscustomobject]@{
            Name           = $nic.Name
            Description    = $nic.Description
            Status         = [string]$nic.OperationalStatus
            InterfaceIndex = $nic.GetIPProperties().GetIPv4Properties().Index
            IPv4Dhcp       = 'Unknown' # .NET doesn't expose a clean boolean for DHCP here easily without CIM, but status/IPs load instantly
            IPv4           = $ipv4Addresses
            Gateway        = @($props.GatewayAddresses | ForEach-Object { $_.Address.ToString() })
            DNS            = @($props.DnsAddresses | Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } | ForEach-Object { $_.ToString() })
        }
    }

    ConvertTo-Json -InputObject $result -Depth 5 -Compress

"""
    output = run_powershell(script)
    if not output:
        return []
    adapters = json.loads(output)
    return adapters if isinstance(adapters, list) else [adapters]


def adapter_settings(adapter):
    addresses = adapter.get("IPv4") or []
    if isinstance(addresses, dict):
        addresses = [addresses]
    return {
        "IP": [
            str(address["IPAddress"])
            for address in addresses
            if address.get("IPAddress") is not None
        ],
        "MASK": [
            str(ipaddress.IPv4Network(f"0.0.0.0/{address['PrefixLength']}").netmask)
            for address in addresses
            if address.get("PrefixLength") is not None
        ],
        "GATEWAY": [
            str(value) for value in (adapter.get("Gateway") or []) if value is not None
        ],
        "DNS": [str(value) for value in (adapter.get("DNS") or []) if value is not None],
    }


def ip_assignment_mode(adapter):
    dhcp_status = str(adapter.get("IPv4Dhcp") or "").casefold()
    if dhcp_status == "enabled":
        return "DHCP"
    if dhcp_status == "disabled":
        return "Manual"
    return "Unknown"


def settings_comparison(before, after):
    return {
        name: {"previous": before[name], "current": after[name]}
        for name in ("IP", "MASK", "GATEWAY", "DNS")
    }


def request_admin_mode(root):
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return False
    except (AttributeError, OSError):
        return False

    if not messagebox.askyesno(
        "Run as administrator",
        "Do you want to restart this program as administrator?",
        parent=root,
    ):
        return False

    shell_execute = ctypes.windll.shell32.ShellExecuteW
    shell_execute.argtypes = (
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    )
    shell_execute.restype = ctypes.c_void_p
    result = shell_execute(
        None,
        "runas",
        sys.executable,
        subprocess.list2cmdline(sys.argv),
        None,
        1,
    )
    if result is None or result <= 32:
        if result == 5:
            return False
        raise OSError(f"Could not start administrator mode (ShellExecuteW returned {result}).")
    return True


def load_presets():
    with PRESETS_FILE.open(encoding="utf-8") as preset_file:
        presets = json.load(preset_file)
    if not isinstance(presets, list) or not presets:
        raise ValueError("The presets file must contain a non-empty list.")
    for index, preset in enumerate(presets, start=1):
        if not isinstance(preset, dict):
            raise ValueError(f"Preset {index} must be an object.")
        name = preset.get("name")
        preset["name"] = name.strip() if isinstance(name, str) else ""
    return presets


def validate_ip(value, field_name):
    try:
        return str(ipaddress.IPv4Address(value.strip()))
    except ipaddress.AddressValueError as error:
        raise ValueError(f"{field_name} must be a valid IPv4 address.") from error


def subnet_prefix(mask):
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask.strip()}").prefixlen
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as error:
        raise ValueError("Subnet mask must be a valid IPv4 subnet mask.") from error


def preset_settings(preset, host):
    if preset.get("dhcp") or str(preset.get("mode", "")).casefold() == "dhcp":
        return None, None, None, preset.get("dns", [])
    try:
        host_number = int(host)
    except (TypeError, ValueError) as error:
        raise ValueError("IP last number must be between 2 and 254.") from error
    if not 2 <= host_number <= 254:
        raise ValueError("IP last number must be between 2 and 254.")
    address = ipaddress.IPv4Address(preset["ip"])
    ip = str(ipaddress.IPv4Address((int(address) & 0xFFFFFF00) | host_number))
    return ip, preset["mask"], preset.get("gateway", ""), preset.get("dns", [])


def apply_dhcp(interface_index, dns_servers=None):
    if dns_servers:
        dns_list = ", ".join(f"'{server}'" for server in dns_servers)
        dns_command = (
            f"Set-DnsClientServerAddress -InterfaceIndex {interface_index} "
            f"-ServerAddresses @({dns_list})"
        )
    else:
        dns_command = (
            f"Set-DnsClientServerAddress -InterfaceIndex {interface_index} "
            "-ResetServerAddresses"
        )
    script = f"""
$ErrorActionPreference = 'Stop'
Set-NetIPInterface -InterfaceIndex {interface_index} -AddressFamily IPv4 -Dhcp Enabled
{dns_command}
Write-Output 'Network settings updated.'
"""
    run_powershell(script)


def apply_static(interface_index, ip, prefix, gateway, dns_servers):
    dns_list = ", ".join(f"'{server}'" for server in dns_servers)
    gateway_setting = f"    DefaultGateway = '{gateway}'\n" if gateway else ""
    dns_command = (
        f"Set-DnsClientServerAddress -InterfaceIndex {interface_index} -ServerAddresses @({dns_list})"
        if dns_servers
        else f"Set-DnsClientServerAddress -InterfaceIndex {interface_index} -ResetServerAddresses"
    )
    gateway_cleanup = (
        f"Get-NetRoute -InterfaceIndex {interface_index} -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.DestinationPrefix -eq '0.0.0.0/0' -or $_.NextHop -eq '{gateway}' }} | "
        "Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue"
        if gateway
        else (
            f"Get-NetRoute -InterfaceIndex {interface_index} -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
            "Where-Object { $_.DestinationPrefix -eq '0.0.0.0/0' } | "
            "Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue"
        )
    )
    script = f"""
$ErrorActionPreference = 'Stop'
Set-NetIPInterface -InterfaceIndex {interface_index} -AddressFamily IPv4 -Dhcp Disabled
Get-NetIPAddress -InterfaceIndex {interface_index} -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
{gateway_cleanup}
$settings = @{{
    InterfaceIndex = {interface_index}
    IPAddress = '{ip}'
    PrefixLength = {prefix}
{gateway_setting}}}
New-NetIPAddress @settings
{dns_command}
Write-Output 'Network settings updated.'
"""
    run_powershell(script)


def set_adapter_enabled(adapter_name, enabled):
    escaped_name = adapter_name.replace("'", "''")
    command = "Enable-NetAdapter" if enabled else "Disable-NetAdapter"
    script = f"""
$ErrorActionPreference = 'Stop'
{command} -Name '{escaped_name}' -Confirm:$false
"""
    run_powershell(script)


def prompt_number(prompt, maximum):
    while True:
        try:
            choice = int(input(prompt))
        except ValueError:
            print("Enter a number from the list.")
            continue
        if 1 <= choice <= maximum:
            return choice - 1
        print(f"Enter a number from 1 to {maximum}.")


def prompt_ip(label, optional=False):
    while True:
        value = input(f"{label}{' (leave blank to skip)' if optional else ''}: ").strip()
        if optional and not value:
            return ""
        try:
            return validate_ip(value, label)
        except ValueError as error:
            print(error)


def prompt_manual_settings():
    ip = prompt_ip("IP address")
    while True:
        mask = input("Subnet mask: ").strip()
        try:
            subnet_prefix(mask)
            break
        except ValueError as error:
            print(error)
    gateway = prompt_ip("Gateway", optional=True)
    dns_servers = [
        prompt_ip("Preferred DNS server", optional=True),
        prompt_ip("Alternative DNS server", optional=True),
    ]
    return ip, mask, gateway, [server for server in dns_servers if server]


def parse_direct_settings(arguments, adapter):
    if not arguments:
        raise ValueError("Provide DHCP or at least an IP address.")
    if arguments[0].casefold() == "dhcp":
        if len(arguments) != 1:
            raise ValueError("DHCP does not accept additional settings.")
        return None
    if len(arguments) > 5:
        raise ValueError("Provide an IP, optional mask and gateway, and up to two DNS servers.")

    current = adapter_settings(adapter)
    ip = validate_ip(arguments[0], "IP address")
    mask = (
        arguments[1]
        if len(arguments) > 1
        else (current["MASK"][0] if current["MASK"] else "255.255.255.0")
    )
    prefix = subnet_prefix(mask)
    gateway = (
        arguments[2]
        if len(arguments) > 2
        else (current["GATEWAY"][0] if current["GATEWAY"] else "")
    )
    gateway = validate_ip(gateway, "Gateway") if gateway and gateway != "-" else ""

    dns_servers = (
        arguments[3:]
        if len(arguments) > 3
        else current["DNS"]
    )
    dns_servers = [
        validate_ip(server, "DNS server")
        for server in dns_servers
        if server != "-"
    ]
    return ip, prefix, gateway, dns_servers


def run_direct_command(adapter_name, settings_arguments):
    if sys.platform != "win32":
        raise OSError("Windows is required to change network adapter settings.")
    if not bool(ctypes.windll.shell32.IsUserAnAdmin()):
        raise PermissionError("Run this command in a terminal opened as Administrator.")

    adapters = get_network_adapters()
    adapter = next(
        (item for item in adapters if item["Name"].casefold() == adapter_name.casefold()),
        None,
    )
    if adapter is None:
        available = ", ".join(item["Name"] for item in adapters) or "none"
        raise ValueError(
            f"Network adapter {adapter_name!r} was not found. Available adapters: {available}."
        )
    interface_index = adapter.get("InterfaceIndex")
    if interface_index is None:
        raise ValueError(f"{adapter['Name']} has no interface index and cannot be configured.")

    settings = parse_direct_settings(settings_arguments, adapter)
    if settings is None:
        apply_dhcp(interface_index)
        mode = "DHCP"
    else:
        apply_static(interface_index, *settings)
        mode = "static settings"
    print(f"Applied {mode} to network adapter {adapter['Name']!r}.")


def run_terminal():
    if sys.platform != "win32":
        print("Windows required: run this terminal mode on the Windows computer.", file=sys.stderr)
        return

    try:
        adapters = get_network_adapters()
        if not adapters:
            print("No network adapters were found.")
            return

        print("Network adapters:")
        for index, adapter in enumerate(adapters, start=1):
            print(
                f"{index}. {adapter['Name']} ({adapter['Description']}) "
                f"[{adapter['Status']}]"
            )
            pprint.pprint(adapter_settings(adapter), sort_dicts=False)
        adapter = adapters[prompt_number("Select adapter: ", len(adapters))]
        if adapter.get("InterfaceIndex") is None:
            print(f"{adapter['Name']} has no interface index and cannot be configured.")
            return

        print("\nSettings mode:\n1. DHCP\n2. Manual\n3. Preset")
        mode_index = prompt_number("Select mode: ", 3)
        settings = None
        mode = ("DHCP", "Manual", "Presets")[mode_index]

        if mode == "Manual":
            settings = prompt_manual_settings()
        elif mode == "Presets":
            presets = load_presets()
            print("Available presets:")
            for index, preset in enumerate(presets, start=1):
                label = preset.get("name") or (
                    "DHCP (Automatic)"
                    if preset.get("dhcp") or str(preset.get("mode", "")).casefold() == "dhcp"
                    else preset.get("ip", "Unknown")
                )
                print(f"{index}. ({label})")
            preset = presets[prompt_number("Select preset: ", len(presets))]
            if preset.get("dhcp") or str(preset.get("mode", "")).casefold() == "dhcp":
                settings = preset_settings(preset, None)
            else:
                default_host = str(ipaddress.IPv4Address(preset["ip"])).split(".")[-1]
                while True:
                    host = input(f"IP last number [{default_host}]: ").strip() or default_host
                    try:
                        settings = preset_settings(preset, host)
                        break
                    except (ValueError, KeyError) as error:
                        print(error)

        if settings is not None and settings[0] is not None:
            ip = validate_ip(settings[0], "IP address")
            prefix = subnet_prefix(settings[1])
            gateway = validate_ip(settings[2], "Gateway") if settings[2] else ""
            dns_servers = [validate_ip(server, "DNS server") for server in settings[3]]
        elif settings is not None:
            # DHCP preset with optional DNS
            ip, prefix, gateway = None, None, None
            dns_servers = [validate_ip(server, "DNS server") for server in settings[3]]

        confirmation = input(
            f"Apply {mode} settings to {adapter['Name']}? This may interrupt the connection [y/N]: "
        ).strip().lower()
        if confirmation != "y":
            print("Cancelled; no settings were changed.")
            return

        previous_settings = adapter_settings(adapter)
        if settings is None or settings[0] is None:
            apply_dhcp(adapter["InterfaceIndex"], dns_servers if settings is not None else None)
        else:
            apply_static(adapter["InterfaceIndex"], ip, prefix, gateway, dns_servers)

        updated_adapter = next(
            (
                item
                for item in get_network_adapters()
                if item["InterfaceIndex"] == adapter["InterfaceIndex"]
            ),
            None,
        )
        if updated_adapter is None:
            print("Settings were applied, but the adapter is no longer connected.")
            return

        print(f"\nNetwork settings for {adapter['Name']}:")
        pprint.pprint(
            settings_comparison(previous_settings, adapter_settings(updated_adapter)),
            sort_dicts=False,
        )
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"Could not update network settings: {error}", file=sys.stderr)


class NetworkSettingsApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Windows Network Settings")
        self.root.minsize(760, 480)
        self.adapters = []
        self.selected_adapter_index = None
        self.adapter_cards = []
        self.preset_cards = []
        self.background_results = queue.Queue()
        self.background_busy = False
        self.presets = load_presets()

        self.ip_mode_var = tk.StringVar(value="Automatic")
        self.dns_mode_var = tk.StringVar(value="Automatic")
        self.preset_var = tk.StringVar()
        self.current_vars = {
            name: tk.StringVar(value="Not available")
            for name in ("IP address", "Subnet mask", "Gateway", "DNS")
        }
        self.manual_vars = {
            name: tk.StringVar()
            for name in (
                "IP address",
                "Subnet mask",
                "Gateway",
                "Preferred DNS server",
                "Alternative DNS server",
            )
        }

        self._build_ui()
        self._bind_apply_enter(self.root)
        self.root.bind_all("<Alt-r>", self._refresh_shortcut)
        self.root.bind_all("<Alt-R>", self._refresh_shortcut)
        self.refresh_adapters()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=1)

        ttk.Label(main, text="Network adapters").grid(row=0, column=0, sticky="w", pady=4)
        self.refresh_button = ttk.Button(main, text="Refresh", command=self.refresh_adapters)
        self.refresh_button.grid(
            row=0, column=2, sticky="e", pady=4
        )

        adapter_list = ttk.LabelFrame(main, text="Select an adapter", padding=6)
        adapter_list.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.adapter_cards_frame = ttk.Frame(adapter_list)
        self.adapter_cards_frame.pack(fill="x")
        self.adapter_cards = []
        adapter_actions = ttk.Frame(adapter_list)
        adapter_actions.pack(fill="x", pady=(6, 0))
        self.enable_button = ttk.Button(
            adapter_actions,
            text="Enable selected",
            command=lambda: self._set_selected_adapter_enabled(True),
        )
        self.enable_button.pack(side="right", padx=(4, 0))
        self.disable_button = ttk.Button(
            adapter_actions,
            text="Disable selected",
            command=lambda: self._set_selected_adapter_enabled(False),
        )
        self.disable_button.pack(side="right")
        self.root.bind("<Configure>", self._resize_adapter_cards)

        current = ttk.LabelFrame(main, text="Current settings", padding=12)
        current.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 16))
        current.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate(self.current_vars.items()):
            ttk.Label(current, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(current, textvariable=variable).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=3)

        self.preset_frame = ttk.LabelFrame(main, text="Fill from preset", padding=8)
        self.preset_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.preset_frame.columnconfigure(0, weight=1)
        self.preset_cards_frame = ttk.Frame(self.preset_frame)
        self.preset_cards_frame.grid(row=0, column=0, sticky="ew", pady=4)
        self.preset_cards = []
        self._render_preset_cards()

        modes = ttk.LabelFrame(main, text="New settings", padding=12)
        modes.grid(row=4, column=0, columnspan=3, sticky="ew")
        modes.columnconfigure(0, weight=1)
        modes.columnconfigure(1, weight=1)

        ip_frame = ttk.LabelFrame(modes, text="IP address", padding=8)
        ip_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        ip_frame.columnconfigure(1, weight=1)
        for row, (text, value) in enumerate(
            (
                ("Obtain an IP address automatically", "Automatic"),
                ("Use the following IP address", "Manual"),
            )
        ):
            ttk.Radiobutton(
                ip_frame,
                text=text,
                value=value,
                variable=self.ip_mode_var,
                command=self._show_ip_mode,
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)

        self.ip_entries = []
        for row, label in enumerate(("IP address", "Subnet mask", "Gateway"), start=2):
            ttk.Label(ip_frame, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=3)
            entry = ttk.Entry(ip_frame, textvariable=self.manual_vars[label])
            entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=3)
            self.ip_entries.append(entry)

        dns_frame = ttk.LabelFrame(modes, text="DNS server addresses", padding=8)
        dns_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        dns_frame.columnconfigure(1, weight=1)
        for row, (text, value) in enumerate(
            (
                ("Obtain DNS server address automatically", "Automatic"),
                ("Use the following DNS server addresses", "Manual"),
            )
        ):
            ttk.Radiobutton(
                dns_frame,
                text=text,
                value=value,
                variable=self.dns_mode_var,
                command=self._show_dns_mode,
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)

        self.dns_entries = []
        for row, label in enumerate(
            ("Preferred DNS server", "Alternative DNS server"), start=2
        ):
            ttk.Label(dns_frame, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=3)
            entry = ttk.Entry(dns_frame, textvariable=self.manual_vars[label])
            entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=3)
            self.dns_entries.append(entry)

        self.status_var = tk.StringVar()
        if not self._is_admin():
            self.status_var.set("Administrator rights are required to apply network changes.")
        ttk.Label(main, textvariable=self.status_var, wraplength=560).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(12, 4)
        )
        self.apply_button = ttk.Button(main, text="Apply settings", command=self.apply_settings)
        self.apply_button.grid(
            row=6, column=0, columnspan=3, sticky="e", pady=(8, 0)
        )
        self._show_ip_mode()
        self._show_dns_mode()

    def _bind_apply_enter(self, widget):
        widget.bind("<Return>", self._apply_shortcut)
        widget.bind("<KP_Enter>", self._apply_shortcut)
        for child in widget.winfo_children():
            self._bind_apply_enter(child)

    @staticmethod
    def _is_admin():
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    def _start_background_task(self, operation, on_success, status, error_title):
        if self.background_busy:
            return False
        self.background_busy = True
        self.refresh_button.configure(state="disabled")
        self.enable_button.configure(state="disabled")
        self.disable_button.configure(state="disabled")
        self.apply_button.configure(state="disabled")
        self.status_var.set(status)

        def run():
            try:
                result = operation()
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                self.background_results.put((None, error, on_success, error_title))
            else:
                self.background_results.put((result, None, on_success, error_title))

        threading.Thread(target=run, daemon=True).start()
        self.root.after(50, self._poll_background_task)
        return True

    def _poll_background_task(self):
        try:
            result, error, on_success, error_title = self.background_results.get_nowait()
        except queue.Empty:
            self.root.after(50, self._poll_background_task)
            return

        self.background_busy = False
        self.refresh_button.configure(state="normal")
        self.enable_button.configure(state="normal")
        self.disable_button.configure(state="normal")
        self.apply_button.configure(state="normal")
        if error is not None:
            self.status_var.set(f"{error_title}: {error}")
            messagebox.showerror(error_title, str(error), parent=self.root)
            return
        on_success(result)

    def refresh_adapters(self):
        if self.background_busy:
            return False
        previous_index = self._selected_adapter().get("InterfaceIndex")
        return self._start_background_task(
            lambda: sorted(
                get_network_adapters(),
                key=lambda adapter: adapter.get("Status") != "Up",
            ),
            lambda adapters: self._show_adapters(adapters, previous_index),
            "Refreshing adapters...",
            "Adapter refresh failed",
        )

    def _show_adapters(self, adapters, previous_index=None):
        self.adapters = adapters
        self.selected_adapter_index = next(
            (index for index, adapter in enumerate(self.adapters) if adapter["InterfaceIndex"] == previous_index),
            0 if self.adapters else None,
        )
        self._render_adapter_cards()
        if self.adapters:
            self._show_current_settings()
            self.status_var.set(
                "Administrator rights are required to apply network changes."
                if not self._is_admin()
                else ""
            )
        else:
            for variable in self.current_vars.values():
                variable.set("No adapter")
            for variable in self.manual_vars.values():
                variable.set("")
            self.status_var.set("No network adapters were found.")

    def _render_adapter_cards(self):
        for card in self.adapter_cards_frame.winfo_children():
            card.destroy()
        self.adapter_cards = []

        available_width = self.root.winfo_width()
        if available_width <= 1:
            available_width = self.root.winfo_screenwidth()
        wraplength = max(80, (available_width - 48) // max(1, len(self.adapters)))

        for index, adapter in enumerate(self.adapters):
            settings = adapter_settings(adapter)
            ip_addresses = ", ".join(settings["IP"]) or "No IPv4 address"
            status = adapter.get("Status") or "Unknown"
            is_selected = index == self.selected_adapter_index
            if status == "Up":
                background, active_background = {
                    "DHCP": ("#66bb6a", "#4caf50"),
                    "Manual": ("#c8e6c9", "#a5d6a7"),
                }.get(ip_assignment_mode(adapter), ("#c8e6c9", "#a5d6a7"))
            elif status == "Disconnected":
                background, active_background = "#ffcdd2", "#ef9a9a"
            elif status == "Not Present":
                background, active_background = "#757575", "#616161"
            else:
                background, active_background = "#f2f2f2", "#e0e0e0"
            card = tk.Button(
                self.adapter_cards_frame,
                text=f"{adapter['Name']}\nIP: {ip_addresses}\nStatus: {status}",
                anchor="w",
                justify="left",
                wraplength=wraplength,
                padx=4,
                pady=2,
                relief="solid",
                borderwidth=2 if is_selected else 1,
                background=background,
                activebackground=active_background,
                takefocus=True,
                command=lambda selected=index: self._select_adapter(selected),
            )
            for key in ("<Left>", "<Right>", "<Up>", "<Down>", "<Home>", "<End>"):
                card.bind(
                    key,
                    lambda event, selected=index: self._handle_adapter_key(event, selected),
                )
            self._bind_apply_enter(card)
            card.bind(
                "<Return>",
                lambda _event, selected=index: self._activate_adapter_card(selected),
            )
            card.bind(
                "<KP_Enter>",
                lambda _event, selected=index: self._activate_adapter_card(selected),
            )
            card.bind("<space>", lambda _event: "break")
            card.pack(side="left", anchor="nw", padx=2, pady=2)
            self.adapter_cards.append(card)

        self.root.after_idle(self._resize_adapter_cards)

    def _resize_adapter_cards(self, _event=None):
        available_width = max(1, self.root.winfo_width() - 48)
        for cards in (
            getattr(self, "adapter_cards", []),
            getattr(self, "preset_cards", []),
        ):
            if not cards:
                continue
            wraplength = max(80, available_width // len(cards))
            for card in cards:
                card.configure(wraplength=wraplength)

    def _handle_adapter_key(self, event, index):
        if not self.adapters:
            return "break"
        if event.keysym in ("Left", "Up"):
            index = (index - 1) % len(self.adapters)
        elif event.keysym in ("Right", "Down"):
            index = (index + 1) % len(self.adapters)
        elif event.keysym == "Home":
            index = 0
        elif event.keysym == "End":
            index = len(self.adapters) - 1
        self._select_adapter(index)
        self.adapter_cards[index].focus_set()
        return "break"

    def _refresh_shortcut(self, _event=None):
        self.refresh_adapters()
        return "break"

    def _apply_shortcut(self, _event=None):
        self.apply_settings()
        return "break"

    def _select_adapter(self, index):
        self.selected_adapter_index = index
        self._render_adapter_cards()
        self._show_current_settings()

    def _activate_adapter_card(self, index):
        self._select_adapter(index)
        if self.adapter_cards:
            self.adapter_cards[index].focus_set()
        return "break"

    def _selected_adapter(self):
        index = self.selected_adapter_index
        return self.adapters[index] if index is not None and 0 <= index < len(self.adapters) else {}

    def _set_selected_adapter_enabled(self, enabled):
        if self.background_busy:
            return
        adapter = self._selected_adapter()
        if not adapter:
            messagebox.showerror(
                "No adapter selected",
                "Select a network adapter first.",
                parent=self.root,
            )
            return
        if not self._is_admin():
            messagebox.showerror(
                "Administrator rights required",
                "Close the program and run it as administrator to change adapter status.",
                parent=self.root,
            )
            return

        action = "enable" if enabled else "disable"
        if not messagebox.askyesno(
            f"{action.title()} network adapter",
            f"Do you want to {action} {adapter['Name']}?",
            parent=self.root,
        ):
            return

        adapter_name = adapter["Name"]

        def update_adapter():
            set_adapter_enabled(adapter_name, enabled)
            return sorted(
                get_network_adapters(),
                key=lambda item: item.get("Status") != "Up",
            )

        def show_updated_adapters(adapters):
            self._show_adapters(adapters)
            selected_index = next(
                (
                    index
                    for index, item in enumerate(self.adapters)
                    if item["Name"] == adapter_name
                ),
                None,
            )
            if selected_index is not None:
                self._select_adapter(selected_index)

        self._start_background_task(
            update_adapter,
            show_updated_adapters,
            "Enabling network adapter..." if enabled else "Disabling network adapter...",
            f"Could not {action} network adapter",
        )

    def _prefill_manual_settings(self, settings):
        values = {
            "IP address": settings["IP"][0] if settings["IP"] else "",
            "Subnet mask": settings["MASK"][0] if settings["MASK"] else "",
            "Gateway": settings["GATEWAY"][0] if settings["GATEWAY"] else "",
            "Preferred DNS server": settings["DNS"][0] if settings["DNS"] else "",
            "Alternative DNS server": settings["DNS"][1] if len(settings["DNS"]) > 1 else "",
        }
        for name, value in values.items():
            self.manual_vars[name].set(value)

    def _show_current_settings(self, _event=None):
        adapter = self._selected_adapter()
        if not adapter:
            self._prefill_manual_settings({"IP": [], "MASK": [], "GATEWAY": [], "DNS": []})
            return
        settings = adapter_settings(adapter)
        self._prefill_manual_settings(settings)
        ip_assignment = ip_assignment_mode(adapter)
        values = {
            "IP address": (
                f"{', '.join(settings['IP'])} ({ip_assignment})"
                if settings["IP"]
                else f"Not set ({ip_assignment})"
            ),
            "Subnet mask": ", ".join(settings["MASK"]) or "Not set",
            "Gateway": ", ".join(settings["GATEWAY"]) or "Not set",
            "DNS": ", ".join(settings["DNS"]) or "Not set",
        }
        for name, value in values.items():
            self.current_vars[name].set(value)

    def _show_ip_mode(self):
        state = "normal" if self.ip_mode_var.get() == "Manual" else "disabled"
        for entry in self.ip_entries:
            entry.configure(state=state)

    def _show_dns_mode(self):
        state = "normal" if self.dns_mode_var.get() == "Manual" else "disabled"
        for entry in self.dns_entries:
            entry.configure(state=state)

    def _select_preset(self, _event=None):
        preset = self._selected_preset()
        if not preset:
            return
        is_dhcp = preset.get("dhcp") or str(preset.get("mode", "")).casefold() == "dhcp"
        if is_dhcp:
            self._render_preset_cards()
            self.ip_mode_var.set("Automatic")
            dns_list = preset.get("dns", [])
            if dns_list:
                self.dns_mode_var.set("Manual")
                self.manual_vars["Preferred DNS server"].set(dns_list[0] if len(dns_list) > 0 else "")
                self.manual_vars["Alternative DNS server"].set(dns_list[1] if len(dns_list) > 1 else "")
            else:
                self.dns_mode_var.set("Automatic")
                self.manual_vars["Preferred DNS server"].set("")
                self.manual_vars["Alternative DNS server"].set("")
            self.manual_vars["IP address"].set("")
            self.manual_vars["Subnet mask"].set("")
            self.manual_vars["Gateway"].set("")
            self._show_ip_mode()
            self._show_dns_mode()
            return

        try:
            settings = preset_settings(preset, str(ipaddress.IPv4Address(preset["ip"])).split(".")[-1])
        except (KeyError, ValueError) as error:
            messagebox.showerror("Invalid preset", str(error), parent=self.root)
            return
        self._prefill_manual_settings(
            {
                "IP": [settings[0]],
                "MASK": [settings[1]],
                "GATEWAY": [settings[2]] if settings[2] else [],
                "DNS": settings[3],
            }
        )
        self._render_preset_cards()
        self.ip_mode_var.set("Manual")
        self.dns_mode_var.set("Manual")
        self._show_ip_mode()
        self._show_dns_mode()
        self.ip_entries[0].focus_set()
        self.ip_entries[0].selection_range(0, tk.END)

    def _selected_preset(self):
        try:
            index = int(self.preset_var.get())
        except (TypeError, ValueError):
            return None
        return self.presets[index] if 0 <= index < len(self.presets) else None

    def _render_preset_cards(self):
        for card in self.preset_cards_frame.winfo_children():
            card.destroy()
        self.preset_cards = []

        available_width = self.root.winfo_width()
        if available_width <= 1:
            available_width = self.root.winfo_screenwidth()
        wraplength = max(80, (available_width - 48) // max(1, len(self.presets)))

        for index, preset in enumerate(self.presets):
            selected = str(index) == self.preset_var.get()
            is_dhcp = preset.get("dhcp") or str(preset.get("mode", "")).casefold() == "dhcp"
            name = preset.get("name")
            header = f"{name}\n" if name else ""

            if is_dhcp:
                dns_servers = ", ".join(preset.get("dns", [])) or "Automatic"
                card_text = (
                    f"{header}IP: Automatic (DHCP)\n"
                    f"Gateway: Automatic\nDNS: {dns_servers}"
                )
            else:
                try:
                    ip = str(ipaddress.IPv4Address(preset["ip"]))
                except (KeyError, ipaddress.AddressValueError):
                    ip = str(preset.get("ip", "Unknown"))
                gateway = preset.get("gateway") or "None"
                dns_servers = ", ".join(preset.get("dns", [])) or "Automatic"
                card_text = (
                    f"{header}IP: {ip}\n"
                    f"Mask: {preset.get('mask', 'Unknown')}\n"
                    f"Gateway: {gateway}\nDNS: {dns_servers}"
                )

            card = tk.Button(
                self.preset_cards_frame,
                text=card_text,
                anchor="w",
                justify="left",
                wraplength=wraplength,
                padx=4,
                pady=2,
                relief="solid",
                borderwidth=2 if selected else 1,
                background="#c8e6c9" if selected else "#f2f2f2",
                activebackground="#a5d6a7" if selected else "#e0e0e0",
                takefocus=True,
                command=lambda selected_index=index: self._select_preset_card(selected_index),
            )
            for key in ("<Left>", "<Right>", "<Up>", "<Down>", "<Home>", "<End>"):
                card.bind(
                    key,
                    lambda event, selected_index=index: self._handle_preset_key(
                        event, selected_index
                    ),
                )
            self._bind_apply_enter(card)
            card.bind(
                "<Return>",
                lambda _event, selected_index=index: self._activate_preset_card(
                    selected_index
                ),
            )
            card.bind(
                "<KP_Enter>",
                lambda _event, selected_index=index: self._activate_preset_card(
                    selected_index
                ),
            )
            card.pack(side="left", anchor="nw", padx=2, pady=2)
            self.preset_cards.append(card)
        self.root.after_idle(self._resize_adapter_cards)

    def _select_preset_card(self, index):
        self.preset_var.set(str(index))
        self._select_preset()

    def _activate_preset_card(self, index):
        self._select_preset_card(index)
        return "break"

    def _handle_preset_key(self, event, index):
        if not self.presets:
            return "break"
        if event.keysym in ("Left", "Up"):
            index = (index - 1) % len(self.presets)
        elif event.keysym in ("Right", "Down"):
            index = (index + 1) % len(self.presets)
        elif event.keysym == "Home":
            index = 0
        elif event.keysym == "End":
            index = len(self.presets) - 1
        self._select_preset_card(index)
        self.preset_cards[index].focus_set()
        return "break"

    def apply_settings(self):
        if self.background_busy:
            return
        adapter = self._selected_adapter()
        if not adapter:
            messagebox.showerror("No adapter selected", "Select a network adapter first.", parent=self.root)
            return
        if adapter.get("InterfaceIndex") is None:
            messagebox.showinfo(
                "Adapter unavailable",
                f"{adapter['Name']} has no interface index and cannot be configured.",
                parent=self.root,
            )
            return
        if not self._is_admin():
            messagebox.showerror(
                "Administrator rights required",
                "Close the program and run it as administrator to change network settings.",
                parent=self.root,
            )
            return
        try:
            if self.ip_mode_var.get() == "Manual":
                ip = validate_ip(self.manual_vars["IP address"].get(), "IP address")
                prefix = subnet_prefix(self.manual_vars["Subnet mask"].get())
                gateway_value = self.manual_vars["Gateway"].get().strip()
                gateway = validate_ip(gateway_value, "Gateway") if gateway_value else ""

            dns_servers = []
            if self.dns_mode_var.get() == "Manual":
                preferred_dns = self.manual_vars["Preferred DNS server"].get().strip()
                if not preferred_dns:
                    raise ValueError("Preferred DNS server is required when DNS is manual.")
                dns_servers.append(validate_ip(preferred_dns, "Preferred DNS server"))
                alternative_dns = self.manual_vars["Alternative DNS server"].get().strip()
                if alternative_dns:
                    dns_servers.append(validate_ip(alternative_dns, "Alternative DNS server"))
        except (ValueError, KeyError) as error:
            messagebox.showerror("Invalid settings", str(error), parent=self.root)
            return

        ip_mode = self.ip_mode_var.get()
        dns_mode = self.dns_mode_var.get()
        if not messagebox.askyesno(
            "Apply network settings",
            f"Apply {ip_mode.lower()} IP and {dns_mode.lower()} DNS settings to {adapter['Name']}?"
            "\n\nThe network connection may be interrupted.",
            parent=self.root,
        ):
            return

        previous_settings = adapter_settings(adapter)
        interface_index = adapter["InterfaceIndex"]

        def apply_and_read_adapters():
            if ip_mode == "Automatic":
                apply_dhcp(interface_index, dns_servers if dns_mode == "Manual" else None)
            else:
                apply_static(
                    interface_index,
                    ip,
                    prefix,
                    gateway,
                    dns_servers if dns_mode == "Manual" else [],
                )
            return sorted(
                get_network_adapters(),
                key=lambda item: item.get("Status") != "Up",
            )

        def show_result(updated_adapters):
            self._show_adapters(updated_adapters, interface_index)
            updated_adapter = next(
                (
                    item
                    for item in self.adapters
                    if item["InterfaceIndex"] == interface_index
                ),
                None,
            )
            if updated_adapter is None:
                print(
                    f"Settings applied to {adapter['Name']}, but the adapter is no longer connected."
                )
                return

            current_settings = adapter_settings(updated_adapter)
            print(f"Network settings for {adapter['Name']}:", flush=True)
            pprint.pprint(
                settings_comparison(previous_settings, current_settings),
                sort_dicts=False,
            )

        self._start_background_task(
            apply_and_read_adapters,
            show_result,
            "Applying network settings...",
            "Could not apply settings",
        )


def main():
    parser = argparse.ArgumentParser(
        description="Change Windows network adapter settings.",
        epilog=(
            "Examples:\n"
            "  network_settings.py --cli\n"
            '  network_settings.py "Wi-Fi" DHCP\n'
            '  network_settings.py "Wi-Fi" 192.168.1.55\n'
            '  network_settings.py "Wi-Fi" 192.168.1.55 255.255.255.0 192.168.1.1\n'
            '  network_settings.py "Wi-Fi" 192.168.1.55 255.255.255.0 '
            "192.168.1.1 1.1.1.1 8.8.8.8"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="use interactive terminal prompts instead of the graphical interface",
    )
    parser.add_argument(
        "command",
        nargs="*",
        metavar="ARG",
        help="adapter name followed by DHCP or an IP and optional network settings",
    )
    arguments = parser.parse_args()
    if arguments.cli:
        if arguments.command:
            parser.error("--cli cannot be combined with a direct adapter command")
        run_terminal()
        return
    if arguments.command:
        if len(arguments.command) < 2:
            parser.error("provide an adapter name followed by DHCP or an IP address")
        try:
            run_direct_command(arguments.command[0], arguments.command[1:])
        except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
            print(f"Could not update network settings: {error}", file=sys.stderr)
            return 1
        return 0

    if sys.platform != "win32":
        print(
            "Windows required: this program manages Windows network adapters.",
            file=sys.stderr,
        )
        return

    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"Could not open the graphical interface: {error}", file=sys.stderr)
        return

    try:
        if request_admin_mode(root):
            root.destroy()
            return
    except OSError as error:
        messagebox.showerror("Administrator mode unavailable", str(error), parent=root)

    try:
        NetworkSettingsApp(root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        messagebox.showerror("Startup failed", str(error), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    raise SystemExit(main())