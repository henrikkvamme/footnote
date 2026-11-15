FROM caddy:2-alpine
EXPOSE 3000
CMD ["caddy", "reverse-proxy", "--from", ":3000", "--to", "https://lens.srv2159196.hstgr.cloud", "--change-host-header"]
