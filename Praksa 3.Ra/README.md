# OPR26-27

Avtor: Marko Toman

## Windows Network Settings

Run `python network_settings.py` on Windows. The program shows the network
adapters from Network Connections in compact selectable cards, with each
adapter's IPv4 address and connection status. Changing settings requires
running Python as Administrator; applying a change can interrupt the network
connection.

Keyboard controls: use Tab to move between controls, Left/Right to move between
adapter and preset cards, and Home/End to choose the first or last card. Press
Enter to apply settings. Use Alt+R to refresh adapters.

For command-line use, open a terminal as Administrator. Use `--cli` for the
interactive terminal interface, or pass an adapter name followed by `DHCP` or
an IP address and optional network settings:

```text
python network_settings.py "Wi-Fi" DHCP
python network_settings.py "Wi-Fi" 192.168.1.55
python network_settings.py "Wi-Fi" 192.168.1.55 255.255.255.0 192.168.1.1
python network_settings.py "Wi-Fi" 192.168.1.55 255.255.255.0 192.168.1.1 1.1.1.1 8.8.8.8
```

For static settings, omitted mask, gateway, and DNS values are preserved from
the adapter's current configuration. If the adapter has no current mask, the
default is `255.255.255.0`; a missing gateway or DNS configuration is left
empty. Use `-` in the gateway or DNS position to clear it.

IP assignment and DNS assignment can be configured independently. Manual IP
settings have separate address, subnet mask, and gateway fields; manual DNS
settings have separate preferred and alternative server fields. Presets are
stored in `network_presets.json`; selecting one fills the manual fields.