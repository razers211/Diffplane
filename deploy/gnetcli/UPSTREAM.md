# Draft pull request for annetutil/gnetcli

Not submitted upstream yet. The patch is
`known-hosts.patch` in this folder, against tag v1.3.17.

---

**Title:** server: verify SSH host keys against a known_hosts file

**Body:**

`gnetcli_server` connects to devices with `ssh.InsecureIgnoreHostKey()`: the SSH streamer
has `WithKnownHostsFiles`, but the server never uses it and no setting turns it on
(`ssh_config` mode reads ProxyJump, Port, User and IdentityFile, not
`StrictHostKeyChecking`/`UserKnownHostsFile`). Anyone who can intercept the management
network can then read device credentials and the configuration being pushed.

This adds an opt-in setting:

- `known_hosts_file` in the server config (`--known-hosts-file`, env `KNOWN_HOSTS_FILE`):
  an OpenSSH known_hosts file. When set, every SSH connection verifies the device's host key
  against it and fails with the `knownhosts` error otherwise. When unset, behaviour is
  unchanged.
- `server.WithKnownHostsFile(path)` for programs that embed the server.
- The file is read for each new connection, so it can be updated without a restart.
- A missing or unreadable file is an error, not a silent fallback to no verification.

Tests (`pkg/server/known_hosts_test.go`) run an in-process SSH server and check that a
different key and an unknown host are refused, the pinned key is accepted, and a missing
file is an error.

Not covered here, possible follow-ups:

- SSH tunnels (`proxy_jump`) still use the default callback.
- The client advertises its default host key algorithms, so known_hosts must hold every key
  type the device offers (or a later change could restrict `HostKeyAlgorithms` to the types
  present in the file, as OpenSSH does).
- Per-host files, or reading `UserKnownHostsFile` from ssh_config.

We run this patch in production-bound tooling (Huawei CloudEngine fabric automation through
annet) and would rather drop our copy than maintain it.
