# Security model

Ego bridge connects trusted machines. Browser scripts run as the signed-in Mac user
through ego-lite's embedded Node runtime and can access that user's readable files.
This is a controlled remote execution channel, not a browser sandbox.

## Connection and authentication

- The Mac initiates a TLS connection to the Linux service (default TCP 3325).
- A five-minute, single-use pairing code issues a long-lived 256-bit token.
- The Mac pins the service certificate fingerprint after pairing. Certificate changes
  require re-pairing; retries do not disable pinning.
- The Mac stores the connection in Keychain. If Keychain fails, the existing fallback
  is a 0600 local file. Linux keeps service credentials in a 0700 configuration directory.
- The default Linux listener is `0.0.0.0:3325`; `--bind` can restrict it. Deploy only
  between trusted accounts/networks. The local shim entry accepts loopback connections.
- The loopback entry trusts local processes, without per-process user authentication.
  Access to the Linux account/machine is therefore access to the Mac execution channel.

The service uses a fixed self-signed certificate so ordinary restarts preserve pairing.
Its generated certificate lifetime is 825 days; replacing an expired certificate requires
pairing again.

## Mac local control

The Mac does not open a TCP/UDP listener. App and CLI communicate with one core over
`~/Library/Application Support/ego-bridge/control.sock`: directory 0700, socket 0600.
A lock prevents a second core from replacing the active control endpoint.
`settings.json` stores the enable switch; credentials remain separate.

Agent-space observation is read-only and runs while the popup is open. It does not
claim spaces or capture page bodies, and its transient titles/names are not written
into logs or sent to the Linux service. See [activity scope](AGENT_ACTIVITY.md).

## Execution and files

- Commands use argument arrays and stdin, not shell interpolation.
- Request IDs prevent duplicate execution within the core's result-cache lifetime.
  A restarted core can return an unknown result; do not assume exactly-once execution
  across restarts and do not automatically replay an unknown request.
- Disconnecting/pausing aborts work; timeouts terminate child processes.
- Returned files are restricted to the session directory, with path checks, size limits,
  chunked transfer and SHA-256 validation. Other Mac directories are not scanned.
- Lifecycle logs omit script bodies, command output, cookies, page bodies and screenshots.

The official CLI may launch ego-lite when a browser command runs. A connected remote
agent can therefore start the browser even if its window was previously closed.

## Stop and revoke

Pause with the Mac switch or `ego-bridge disconnect`; stop the Linux service with
`systemctl --user stop ego-bridge`. Pausing preserves credentials.

Unpairing online notifies Linux to revoke the token and deletes local credentials.
When offline, the App only confirms local deletion: revoke the remote token separately,
or generate a new pairing to replace it. Do not interpret offline deletion as proven
remote revocation.

## Reporting a vulnerability

Use the repository's [private security reporting](https://github.com/gwen0x4c3/ego-bridge/security/advisories/new)
when available. Do not include live tokens, pairing codes, personal browser content or
screenshots in a public issue.
