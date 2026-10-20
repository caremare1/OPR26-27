import argparse
import ctypes
import ipaddress
import json
import pprint
import subprocess
import sys
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


def get_connected_adapters():
    script = r"""
$ErrorActionPreference = 'Stop'
$adapters = @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | ForEach-Object {
    $adapter = $_
    $config = Get-NetIPConfiguration -InterfaceIndex $adapter.ifIndex -ErrorAction SilentlyContinue
    [pscustomobject]@{
        Name = $adapter.Name
        Description = $adapter.InterfaceDescription
        InterfaceIndex = [int]$adapter.ifIndex
        IPv4 = @($config.IPv4Address | ForEach-Object {
            [pscustomobject]@{ IPAddress = $_.IPAddress; PrefixLength = $_.PrefixLength }
        })
        Gateway = @($config.IPv4DefaultGateway | ForEach-Object { $_.NextHop })
        DNS = @($config.DnsServer.ServerAddresses | Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' })
    }
})
ConvertTo-Json -InputObject $adapters -Depth 5 -Compress
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
        "IP": [address.get("IPAddress", "") for address in addresses],
        "MASK": [
            str(ipaddress.IPv4Network(f"0.0.0.0/{address['PrefixLength']}").netmask)
            for address in addresses
            if address.get("PrefixLength") is not None
        ],
        "GATEWAY": adapter.get("Gateway") or [],
        "DNS": adapter.get("DNS") or [],
    }


def settings_comparison(before, after):
    return {
        name: {"previous": before[name], "current": after[name]}
        for name in ("IP", "MASK", "GATEWAY", "DNS")
    }


def load_presets():
    with PRESETS_FILE.open(encoding="utf-8") as preset_file:
        presets = json.load(preset_file)
    if not isinstance(presets, list) or not presets:
        raise ValueError("The presets file must contain a non-empty list.")
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
    try:
        host_number = int(host)
    except (TypeError, ValueError) as error:
        raise ValueError("IP last number must be between 2 and 254.") from error
    if not 2 <= host_number <= 254:
        raise ValueError("IP last number must be between 2 and 254.")
    address = ipaddress.IPv4Address(preset["ip"])
    ip = str(ipaddress.IPv4Address((int(address) & 0xFFFFFF00) | host_number))
    return ip, preset["mask"], preset.get("gateway", ""), preset.get("dns", [])


def apply_dhcp(interface_index):
    script = f"""
$ErrorActionPreference = 'Stop'
Set-NetIPInterface -InterfaceIndex {interface_index} -AddressFamily IPv4 -Dhcp Enabled
Set-DnsClientServerAddress -InterfaceIndex {interface_index} -ResetServerAddresses
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
    script = f"""
$ErrorActionPreference = 'Stop'
Set-NetIPInterface -InterfaceIndex {interface_index} -AddressFamily IPv4 -Dhcp Disabled
Get-NetIPAddress -InterfaceIndex {interface_index} -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
Get-NetRoute -InterfaceIndex {interface_index} -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
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
    dns_servers = [prompt_ip("DNS 1", optional=True), prompt_ip("DNS 2", optional=True)]
    return ip, mask, gateway, [server for server in dns_servers if server]


def run_terminal():
    if sys.platform != "win32":
        print("Windows required: run this terminal mode on the Windows computer.", file=sys.stderr)
        return

    try:
        adapters = get_connected_adapters()
        if not adapters:
            print("No connected network adapters were found.")
            return

        print("Connected network adapters:")
        for index, adapter in enumerate(adapters, start=1):
            print(f"{index}. {adapter['Name']} ({adapter['Description']})")
            pprint.pprint(adapter_settings(adapter), sort_dicts=False)
        adapter = adapters[prompt_number("Select adapter: ", len(adapters))]

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
                print(f"{index}. {preset['name']} ({preset['ip']})")
            preset = presets[prompt_number("Select preset: ", len(presets))]
            default_host = str(ipaddress.IPv4Address(preset["ip"])).split(".")[-1]
            while True:
                host = input(f"IP last number [{default_host}]: ").strip() or default_host
                try:
                    settings = preset_settings(preset, host)
                    break
                except (ValueError, KeyError) as error:
                    print(error)

        if settings is not None:
            ip = validate_ip(settings[0], "IP address")
            prefix = subnet_prefix(settings[1])
            gateway = validate_ip(settings[2], "Gateway") if settings[2] else ""
            dns_servers = [validate_ip(server, "DNS server") for server in settings[3]]

        confirmation = input(
            f"Apply {mode} settings to {adapter['Name']}? This may interrupt the connection [y/N]: "
        ).strip().lower()
        if confirmation != "y":
            print("Cancelled; no settings were changed.")
            return

        previous_settings = adapter_settings(adapter)
        if settings is None:
            apply_dhcp(adapter["InterfaceIndex"])
        else:
            apply_static(adapter["InterfaceIndex"], ip, prefix, gateway, dns_servers)

        updated_adapter = next(
            (
                item
                for item in get_connected_adapters()
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
        self.root.minsize(620, 520)
        self.adapters = []
        self.presets = load_presets()

        self.adapter_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="DHCP")
        self.preset_var = tk.StringVar()
        self.quick_host_var = tk.StringVar()
        self.current_vars = {
            name: tk.StringVar(value="Not available")
            for name in ("IP address", "Subnet mask", "Gateway", "DNS")
        }
        self.manual_vars = {
            name: tk.StringVar()
            for name in ("IP address", "Subnet mask", "Gateway", "DNS 1", "DNS 2")
        }

        self._build_ui()
        self.refresh_adapters()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="Connected network adapter").grid(row=0, column=0, sticky="w", pady=4)
        self.adapter_box = ttk.Combobox(main, textvariable=self.adapter_var, state="readonly")
        self.adapter_box.grid(row=0, column=1, sticky="ew", padx=(12, 8), pady=4)
        self.adapter_box.bind("<<ComboboxSelected>>", self._show_current_settings)
        ttk.Button(main, text="Refresh", command=self.refresh_adapters).grid(row=0, column=2, pady=4)

        current = ttk.LabelFrame(main, text="Current settings", padding=12)
        current.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(12, 16))
        current.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate(self.current_vars.items()):
            ttk.Label(current, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(current, textvariable=variable).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=3)

        modes = ttk.LabelFrame(main, text="New settings", padding=12)
        modes.grid(row=2, column=0, columnspan=3, sticky="ew")
        for column in range(3):
            modes.columnconfigure(column, weight=1)
        for column, mode in enumerate(("DHCP", "Manual", "Presets")):
            ttk.Radiobutton(
                modes,
                text=mode,
                value=mode,
                variable=self.mode_var,
                command=self._show_mode,
            ).grid(row=0, column=column, sticky="w", pady=(0, 10))

        self.manual_frame = ttk.Frame(modes)
        self.manual_frame.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.manual_frame.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate(self.manual_vars.items()):
            ttk.Label(self.manual_frame, text=f"{label}:").grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(self.manual_frame, textvariable=variable).grid(
                row=row, column=1, sticky="ew", padx=(12, 0), pady=3
            )

        self.preset_frame = ttk.Frame(modes)
        self.preset_frame.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.preset_frame.columnconfigure(1, weight=1)
        self.preset_box = ttk.Combobox(
            self.preset_frame,
            textvariable=self.preset_var,
            state="readonly",
            values=[preset["name"] for preset in self.presets],
        )
        self.preset_box.grid(row=0, column=0, columnspan=2, sticky="ew", pady=4)
        self.preset_box.bind("<<ComboboxSelected>>", self._select_preset)
        ttk.Label(self.preset_frame, text="IP last number:").grid(row=1, column=0, sticky="w", pady=4)
        self.quick_host = tk.Spinbox(
            self.preset_frame,
            from_=2,
            to=254,
            textvariable=self.quick_host_var,
            width=8,
            command=self._update_preset_summary,
        )
        self.quick_host.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=4)
        self.quick_host_var.trace_add("write", self._update_preset_summary)
        self.preset_summary = ttk.Label(self.preset_frame, text="")
        self.preset_summary.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.status_var = tk.StringVar()
        if not self._is_admin():
            self.status_var.set("Administrator rights are required to apply network changes.")
        ttk.Label(main, textvariable=self.status_var, wraplength=560).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(12, 4)
        )
        ttk.Button(main, text="Apply settings", command=self.apply_settings).grid(
            row=4, column=0, columnspan=3, sticky="e", pady=(8, 0)
        )
        self._show_mode()

    @staticmethod
    def _is_admin():
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    def refresh_adapters(self):
        previous_index = self._selected_adapter().get("InterfaceIndex")
        try:
            self.adapters = get_connected_adapters()
        except (OSError, RuntimeError, json.JSONDecodeError) as error:
            messagebox.showerror("Adapter refresh failed", str(error), parent=self.root)
            return False

        self.adapter_box["values"] = [
            f"{adapter['Name']} ({adapter['Description']})" for adapter in self.adapters
        ]
        selected = next(
            (index for index, adapter in enumerate(self.adapters) if adapter["InterfaceIndex"] == previous_index),
            0,
        )
        if self.adapters:
            self.adapter_box.current(selected)
            self._show_current_settings()
            self.status_var.set(
                "Administrator rights are required to apply network changes."
                if not self._is_admin()
                else f"Found {len(self.adapters)} connected adapter(s)."
            )
        else:
            self.adapter_var.set("")
            for variable in self.current_vars.values():
                variable.set("No connected adapter")
            self.status_var.set("No connected network adapters were found.")
        return True

    def _selected_adapter(self):
        selected = self.adapter_box.current()
        return self.adapters[selected] if 0 <= selected < len(self.adapters) else {}

    def _show_current_settings(self, _event=None):
        adapter = self._selected_adapter()
        if not adapter:
            return
        settings = adapter_settings(adapter)
        values = {
            "IP address": ", ".join(settings["IP"]) or "Not set",
            "Subnet mask": ", ".join(settings["MASK"]) or "Not set",
            "Gateway": ", ".join(settings["GATEWAY"]) or "Not set",
            "DNS": ", ".join(settings["DNS"]) or "Not set",
        }
        for name, value in values.items():
            self.current_vars[name].set(value)

    def _show_mode(self):
        self.manual_frame.grid_remove()
        self.preset_frame.grid_remove()
        if self.mode_var.get() == "Manual":
            self.manual_frame.grid()
        elif self.mode_var.get() == "Presets":
            self.preset_frame.grid()
            if self.presets and not self.preset_var.get():
                self.preset_box.current(0)
                self._select_preset()

    def _select_preset(self, _event=None):
        preset = self._selected_preset()
        if not preset:
            return
        address = ipaddress.IPv4Address(preset["ip"])
        self.quick_host_var.set(str(address).split(".")[-1])
        self._update_preset_summary()

    def _selected_preset(self):
        return next((preset for preset in self.presets if preset["name"] == self.preset_var.get()), None)

    def _update_preset_summary(self, *_args):
        preset = self._selected_preset()
        if not preset:
            return
        try:
            host = int(self.quick_host_var.get())
            address = ipaddress.IPv4Address(preset["ip"])
            ip = str(ipaddress.IPv4Address((int(address) & 0xFFFFFF00) | host))
        except (ValueError, ipaddress.AddressValueError):
            self.preset_summary.configure(text="Enter a host number from 2 to 254.")
            return
        self.preset_summary.configure(
            text=f"IP: {ip}    Mask: {preset['mask']}    Gateway: {preset.get('gateway', 'None')}"
        )

    def _settings_from_preset(self):
        preset = self._selected_preset()
        if not preset:
            raise ValueError("Choose a preset first.")
        return preset_settings(preset, self.quick_host_var.get())

    def apply_settings(self):
        adapter = self._selected_adapter()
        if not adapter:
            messagebox.showerror("No adapter selected", "Select a connected network adapter first.", parent=self.root)
            return
        if not self._is_admin():
            messagebox.showerror(
                "Administrator rights required",
                "Close the program and run it as administrator to change network settings.",
                parent=self.root,
            )
            return
        try:
            if self.mode_var.get() == "DHCP":
                settings = None
            elif self.mode_var.get() == "Manual":
                settings = (
                    self.manual_vars["IP address"].get(),
                    self.manual_vars["Subnet mask"].get(),
                    self.manual_vars["Gateway"].get(),
                    [self.manual_vars["DNS 1"].get(), self.manual_vars["DNS 2"].get()],
                )
            else:
                settings = self._settings_from_preset()

            if settings is not None:
                ip = validate_ip(settings[0], "IP address")
                prefix = subnet_prefix(settings[1])
                gateway = validate_ip(settings[2], "Gateway") if settings[2].strip() else ""
                dns_servers = [validate_ip(server, "DNS server") for server in settings[3] if server.strip()]
        except (ValueError, KeyError) as error:
            messagebox.showerror("Invalid settings", str(error), parent=self.root)
            return

        mode = self.mode_var.get()
        if not messagebox.askyesno(
            "Apply network settings",
            f"Apply {mode} settings to {adapter['Name']}?\n\nThe network connection may be interrupted.",
            parent=self.root,
        ):
            return

        previous_settings = adapter_settings(adapter)
        try:
            if settings is None:
                apply_dhcp(adapter["InterfaceIndex"])
            else:
                apply_static(adapter["InterfaceIndex"], ip, prefix, gateway, dns_servers)
        except (OSError, RuntimeError) as error:
            messagebox.showerror("Could not apply settings", str(error), parent=self.root)
            return

        if not self.refresh_adapters():
            print(f"Settings applied to {adapter['Name']}, but updated settings could not be read.")
            return

        updated_adapter = next(
            (
                item
                for item in self.adapters
                if item["InterfaceIndex"] == adapter["InterfaceIndex"]
            ),
            None,
        )
        if updated_adapter is None:
            print(f"Settings applied to {adapter['Name']}, but the adapter is no longer connected.")
            return

        current_settings = adapter_settings(updated_adapter)
        print(f"Network settings for {adapter['Name']}:", flush=True)
        pprint.pprint(
            settings_comparison(previous_settings, current_settings),
            sort_dicts=False,
        )


def main():
    parser = argparse.ArgumentParser(description="Change Windows network adapter settings.")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="use interactive terminal prompts instead of the graphical interface",
    )
    arguments = parser.parse_args()
    if arguments.cli:
        run_terminal()
        return

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
        NetworkSettingsApp(root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        messagebox.showerror("Startup failed", str(error), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()