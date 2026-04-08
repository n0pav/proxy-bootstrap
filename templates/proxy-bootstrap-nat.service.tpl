# Managed by proxy-bootstrap
[Unit]
Description=Apply proxy-bootstrap NAT rule
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c '/usr/sbin/iptables -w -t nat -C POSTROUTING -o __PRIMARY_INTERFACE__ -m comment --comment proxy-bootstrap-nat -j MASQUERADE 2>/dev/null || /usr/sbin/iptables -w -t nat -A POSTROUTING -o __PRIMARY_INTERFACE__ -m comment --comment proxy-bootstrap-nat -j MASQUERADE'
ExecStop=/bin/sh -c '/usr/sbin/iptables -w -t nat -D POSTROUTING -o __PRIMARY_INTERFACE__ -m comment --comment proxy-bootstrap-nat -j MASQUERADE 2>/dev/null || true'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
