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

IP assignment and DNS assignment can be configured independently. Manual IP
settings have separate address, subnet mask, and gateway fields; manual DNS
settings have separate preferred and alternative server fields. Presets are
stored in `network_presets.json`; selecting one fills the manual fields.