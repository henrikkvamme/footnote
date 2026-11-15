# footnote-api

`https://api.footnotecheck.com` is a second address of the Footnote server that the iPhone app calls.
This directory holds only a Caddy reverse proxy to `https://lens.srv2159196.hstgr.cloud`
(the server in `hostinger-noise/ios/server/`). The proxy lets us add the domain without a new deploy
of the server, which would clear its state in memory.

Deploy: `infra/hostinger/host.sh footnote-api api.footnotecheck.com 3000`

To serve the domain from the server itself: add `api.footnotecheck.com` to
`hostinger-noise/ios/server/.hostinger-hosts`, deploy the server, and remove this proxy.
