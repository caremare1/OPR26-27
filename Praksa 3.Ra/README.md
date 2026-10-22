# OPR26-27

Avtor: Marko Toman

## Windows Network Settings

Run `python network_settings.py` on Windows. The program shows the network
adapters from Network Connections in compact selectable cards, with each
adapter's IPv4 address and connection status. Changing settings requires
running Python as Administrator; applying a change can interrupt the network
connection.

Keyboard controls: use Tab to move between controls, Left/Right to move between
adapter cards, Home/End to choose the first or last adapter, and Enter or Space
to select a card. Use Alt+R to refresh adapters and Ctrl+Enter to apply settings.

Presets are stored in `network_presets.json` and can be edited separately. In
the Presets mode, change the IP last number before applying the selected preset.