#!/bin/bash
# SpotBot WiFi Watchdog & Failover — priorise la connexion de secours 'bastet'
# Supporte NetworkManager (nmcli) et wpa_supplicant

PING_TARGET="8.8.8.8"      # DNS Public pour valider la connexion Internet réelle
PING_TIMEOUT=3
CHECK_INTERVAL=10
DEFAULT_SSID="TeALO"
DEFAULT_PSK="t3al0l3plusb3au"
LOG=/var/log/spotbot/wifi-failover.log

mkdir -p /var/log/spotbot

get_wifi_interfaces() {
    iw dev 2>/dev/null | awk '$1=="Interface"{print $2}' | sort
}

current_ssid() {
    local iface=$1
    if [ -z "$iface" ]; then
        iface="wlan0"
    fi
    local ssid=""
    if command -v iwgetid >/dev/null 2>&1; then
        ssid=$(iwgetid -r $iface 2>/dev/null)
    fi
    if [ -z "$ssid" ]; then
        ssid=$(wpa_cli -i $iface status 2>/dev/null | grep '^ssid=' | cut -d= -f2)
    fi
    echo "$ssid"
}

current_active_interface() {
    ip route get $PING_TARGET 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1
}

is_connected() {
    local iface=$1
    if [ -z "$iface" ]; then
        iface="wlan0"
    fi
    # Check if interface has an IP address
    if ip addr show dev $iface 2>/dev/null | grep -q 'inet '; then
        # Try pinging the local gateway for that interface first
        local gw=$(ip route show dev $iface | grep default | awk '{print $3}' | head -1)
        if [ -n "$gw" ]; then
            if ping -c 1 -W 2 $gw >/dev/null 2>&1; then
                return 0
            fi
        fi
        # Fallback to pinging 8.8.8.8 if gateway ping fails
        if ping -c 1 -W $PING_TIMEOUT $PING_TARGET >/dev/null 2>&1; then
            return 0
        fi
        # If both fail but we have an IP, we are still considered connected locally
        return 0
    fi
    return 1
}

is_system_connected() {
    # Check if we have a default route on any interface
    if ip route show | grep -q 'default '; then
        # Try pinging the default gateway
        local gw=$(ip route show | grep default | awk '{print $3}' | head -1)
        if [ -n "$gw" ]; then
            if ping -c 1 -W 2 $gw >/dev/null 2>&1; then
                return 0
            fi
        fi
        # Fallback to pinging 8.8.8.8
        if ping -c 1 -W $PING_TIMEOUT $PING_TARGET >/dev/null 2>&1; then
            return 0
        fi
        # If we have a default route but ping fails, we are still likely connected locally
        return 0
    fi
    return 1
}

connect_wifi() {
    local iface=$1
    local ssid=$2
    local psk=$3
    
    echo "$(date) [failover] Connecting $iface to $ssid..." >> $LOG
    ip link set $iface up 2>/dev/null
    
    # Configure wpa_supplicant.conf
    python3 -c "
import sys, os
ssid = '$ssid'
psk = '$psk'
conf_path = '/etc/wpa_supplicant/wpa_supplicant.conf'
content = ''
if os.path.exists(conf_path):
    with open(conf_path, 'r') as f:
        content = f.read()
blocks = content.split('network={')
new_blocks = [blocks[0]]
for b in blocks[1:]:
    brace_idx = b.find('}')
    if brace_idx != -1:
        block_content = b[:brace_idx]
        rest = b[brace_idx:]
        if f'ssid=\"{ssid}\"' in block_content or f'ssid=\'{ssid}\'' in block_content:
            new_blocks[0] += rest.lstrip('}').lstrip('\n')
            continue
    new_blocks.append('network={' + b)
new_content = ''.join(new_blocks).strip() + '\n\n'
if psk:
    new_network = f'network={{\n\tssid=\"{ssid}\"\n\tpsk=\"{psk}\"\n}}\n'
else:
    new_network = f'network={{\n\tssid=\"{ssid}\"\n\tkey_mgmt=NONE\n}}\n'
new_content += new_network
with open(conf_path, 'w') as f:
    f.write(new_content)
"
    wpa_cli -i $iface reconfigure > /dev/null 2>&1
}

echo "$(date) [failover] WiFi failover service started" >> $LOG

while true; do
    if is_system_connected; then
        sleep $CHECK_INTERVAL
        continue
    fi

    INTERFACES=$(get_wifi_interfaces)
    if [ -z "$INTERFACES" ]; then
        sleep $CHECK_INTERVAL
        continue
    fi
    
    # 1. Scanner pour voir si 'bastet' est disponible
    for iface in $INTERFACES; do
        if command -v nmcli &>/dev/null; then
            nmcli device wifi rescan ifname "$iface" > /dev/null 2>&1
        fi
    done
    sleep 2
    
    BASTET_VISIBLE=false
    # Scan using iwlist which works on unmanaged interfaces
    if iwlist wlan0 scan 2>/dev/null | grep -Eq 'ESSID:"bastet"'; then
        BASTET_VISIBLE=true
    fi
    
    CUR_SSID=$(current_ssid)
    ACTIVE_IFACE=$(current_active_interface)
    
    # 2. Si 'bastet' est visible et qu'on n'y est pas déjà connecté
    if [ "$BASTET_VISIBLE" = true ] && [ "$CUR_SSID" != "bastet" ]; then
        echo "$(date) [failover] Recovery SSID 'bastet' detected. Switching immediately..." >> $LOG
        for iface in $INTERFACES; do
            # Tenter en mode ouvert
            connect_wifi "$iface" "bastet" ""
            sleep 3
            if is_connected "$iface"; then
                echo "$(date) [failover] Connected to open SSID 'bastet' successfully" >> $LOG
                break
            fi
            
            # Tenter avec clé 'bastet'
            connect_wifi "$iface" "bastet" "bastet"
            sleep 3
            if is_connected "$iface"; then
                echo "$(date) [failover] Connected to secured SSID 'bastet' successfully" >> $LOG
                break
            fi
        done
        sleep $CHECK_INTERVAL
        continue
    fi
    
    # 3. Mode normal : si la connexion actuelle est perdue, rétablir vers le SSID par défaut
    if [ -n "$CUR_SSID" ]; then
        # Tout va bien, on est connecté à un réseau WiFi
        sleep $CHECK_INTERVAL
        continue
    fi
    
    echo "$(date) [failover] Connection lost or inactive, attempting recovery on default SSID..." >> $LOG
    for iface in $INTERFACES; do
        connect_wifi "$iface" "$DEFAULT_SSID" "$DEFAULT_PSK"
        sleep 4
        if is_connected "$iface"; then
            echo "$(date) [failover] Re-connected to default network $DEFAULT_SSID on $iface" >> $LOG
            break
        fi
    done
    
    sleep $CHECK_INTERVAL
done
