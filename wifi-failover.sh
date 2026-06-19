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
    if command -v nmcli &>/dev/null; then
        nmcli -t -f ACTIVE,SSID dev wifi 2>/dev/null | grep '^yes:' | cut -d: -f2 | head -n 1
    else
        iwgetid -r 2>/dev/null
    fi
}

current_active_interface() {
    ip route get $PING_TARGET 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1
}

is_connected() {
    local iface=$1
    if [ -z "$iface" ]; then
        ping -c 1 -W $PING_TIMEOUT $PING_TARGET > /dev/null 2>&1
    else
        ping -I $iface -c 1 -W $PING_TIMEOUT $PING_TARGET > /dev/null 2>&1
    fi
}

connect_wifi() {
    local iface=$1
    local ssid=$2
    local psk=$3
    
    echo "$(date) [failover] Connecting $iface to $ssid..." >> $LOG
    ip link set $iface up 2>/dev/null
    
    if command -v nmcli &>/dev/null; then
        if [ -n "$psk" ]; then
            nmcli device wifi connect "$ssid" password "$psk" ifname "$iface" > /dev/null 2>&1
        else
            nmcli device wifi connect "$ssid" ifname "$iface" > /dev/null 2>&1
        fi
    else
        # Fallback wpa_supplicant
        wpa_cli -i $iface reconfigure > /dev/null 2>&1
    fi
}

echo "$(date) [failover] WiFi failover service started" >> $LOG

while true; do
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
    if command -v nmcli &>/dev/null; then
        if nmcli -t -f SSID dev wifi list 2>/dev/null | grep -Fqx "bastet"; then
            BASTET_VISIBLE=true
        fi
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
    if [ -n "$ACTIVE_IFACE" ] && is_connected "$ACTIVE_IFACE"; then
        # Tout va bien
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
