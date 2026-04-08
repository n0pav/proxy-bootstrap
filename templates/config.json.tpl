{
  "log": {
    "level": "info",
    "timestamp": true
  },
  "inbounds": [
    {
      "type": "vless",
      "tag": "in-vless-reality",
      "listen": "::",
      "listen_port": __VLESS_PORT__,
      "users": [
        {
          "name": "vless-user",
          "uuid": "__VLESS_UUID__",
          "flow": "xtls-rprx-vision"
        }
      ],
      "tls": {
        "enabled": true,
        "server_name": "__REALITY_SERVER_NAME__",
        "reality": {
          "enabled": true,
          "handshake": {
            "server": "__REALITY_SERVER_NAME__",
            "server_port": __REALITY_HANDSHAKE_PORT__
          },
          "private_key": "__REALITY_PRIVATE_KEY__",
          "short_id": [
            "__REALITY_SHORT_ID__"
          ]
        }
      }
    },
    {
      "type": "hysteria2",
      "tag": "in-hysteria2",
      "listen": "::",
      "listen_port": __HYSTERIA_PORT__,
      "up_mbps": __HYSTERIA_UP_MBPS__,
      "down_mbps": __HYSTERIA_DOWN_MBPS__,
      "users": [
        {
          "name": "__HYSTERIA_USER__",
          "password": "__HYSTERIA_PASSWORD__"
        }
      ],
      "tls": {
        "enabled": true,
        "server_name": "__HYSTERIA_DOMAIN__",
        "certificate_path": "__CERT_FULLCHAIN__",
        "key_path": "__CERT_PRIVKEY__"
      }
    }
  ],
  "outbounds": [
    {
      "type": "direct",
      "tag": "direct"
    }
  ]
}
