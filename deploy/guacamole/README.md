# Browser-based SSH and RDP for Leshy (Apache Guacamole)

This **optional** integration provides an actual browser terminal (SSH) and graphical remote desktop (RDP) without having to implement Leshy's own low-level SSH PTY or RDP screen transport.

Apache Guacamole **1.6.0** is an Apache-2.0-licensed, separate service. It is not bundled into Leshy binaries. Official manual:
- https://guacamole.apache.org/doc/gug/guacamole-docker.html
- https://guacamole.apache.org/doc/gug/postgresql-auth.html
- https://guacamole.apache.org/doc/gug/openid-auth.html

## Start on a trusted Docker host

Use a computer with Docker Compose and network access to your authorized SSH/RDP servers. From this directory:

1. Copy `.env.example` to `.env` and set a long, random `GUAC_DB_PASSWORD`. Neither `.env` nor `initdb.sql` should be committed.
2. Generate the official database schema **before first startup**:

   ```sh
   docker run --rm guacamole/guacamole:1.6.0 /opt/guacamole/bin/initdb.sh --postgresql > initdb.sql
   ```
3. Start the stack:

   ```sh
   docker compose up -d
   ```
4. Open **http://127.0.0.1:8088/guacamole/** on the same Docker host. Default Guacamole database user/password is `guacadmin` / `guacadmin`; **change this password immediately** before creating any connection. Never expose the default account publicly.
5. In Guacamole, create SSH and RDP connections for your own servers, configure allowed users and connection rights. Use Guacamole's own SSH host-key checks and RDP certificate verification.
6. In Leshy, open **SuperAgent → Удалённые компьютеры → Web SSH и RDP через Apache Guacamole**, save the Guacamole URL, then use the Web SSH/RDP button. The gateway performs **its own login** and shows your allowed connections.

This Compose deployment binds the Web service only to the server's **127.0.0.1:8088**, so it is not reachable from a different laptop or phone. For access from other devices, deploy an authenticated HTTPS reverse proxy (or an authenticated private VPN) and configure its browser-facing URL in Leshy. **Do not** expose the Guacamole web UI via unprotected plain HTTP, and **never** publish `guacd:4822` or the PostgreSQL port.

The network `backend` is internal and keeps PostgreSQL/guacd isolated. The Guacamole container also has the frontend network for access through the local forwarded web port.

## Security and access separation

- **Separate authentication:** A Leshy provider-linked Web login does **not** automatically grant Guacamole login. Guacamole has its own permissions. To use common identity, configure its official OpenID Connect extension against the same trusted OIDC provider, with explicit per-user connection permissions.
- **No transfer of secrets:** Leshy stores only the configured gateway URL per verified owner. Leshy does not copy passwords/SSH credentials, browser cookies or Guacamole session tokens into the gateway or into the GitHub repository.
- **No direct AI control:** The current module opens a *human-operated* real SSH terminal and RDP desktop in Guacamole. The model cannot read the Guacamole screen or type into it automatically. AI-driven screen control needs a separately approved, observed action channel and real end-to-end validation.
- **RDP identity:** A stored SSH host-key fingerprint does not validate an RDP TLS server certificate. Guacamole/native RDP connection must authenticate the server independently.
- **Deployment:** This Compose stack is for Docker/Linux hosts; it is **not** a native Windows installer for Guacamole, and Windows Docker Desktop setup may differ. Do not point the Windows Web browser at `http://127.0.0.1:8088` if the Docker host is a different machine: use its legitimate HTTPS endpoint.

This is a practical full interactive SSH/RDP transport while Leshy's existing approved SSH command executor remains available for verifiable AI plans. After actual integration tests on your computers, the future work is owner-authorized screen observations and safe multi-step UI actions, not another unauthenticated tunnel.
