# Managed by proxy-bootstrap
[Unit]
Description=sing-box service
Documentation=https://sing-box.sagernet.org/
After=network-online.target nss-lookup.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=__SING_BOX_BIN__ run -c __CONFIG_FILE__
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5s
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
