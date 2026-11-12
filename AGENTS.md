# TURXZ AI Agent Governance Framework

This document defines the formal architectural blueprint, operational boundaries, permission taxonomy, and security policies governing all autonomous and semi-autonomous AI agents within the **TURXZ** (Turing Smart Screen Display Driver & Automation Engine) ecosystem.

---

## 1. System Architecture Overview

The TURXZ agent ecosystem employs a decoupled, event-driven multi-agent architecture designed to operate reliably on secondary USB/CDC displays (specifically Turing Smart Screen 3.5", 5.0", and 7.0" hardware).

```
                      +---------------------------------------+
                      |         Human Developer / CLI         |
                      +---------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                            TURXZ Multi-Agent Layer                                |
|                                                                                   |
|   +--------------------------+               +--------------------------------+   |
|   |    turzx-core-agent      |<=============>|     turzx-scheduler-agent      |   |
|   |  - Display pipeline      |  Local IPC    |  - Cron & Interval tasks       |   |
|   |  - Framebuffer & RGB565  |  Tokens       |  - Health & Quota monitoring   |   |
|   |  - CLI Dispatcher        |               |  - Execution lifecycle         |   |
|   +--------------------------+               +--------------------------------+   |
|                 |                                             |                   |
|                 v                                             v                   |
|   +--------------------------+               +--------------------------------+   |
|   |   turzx-hardware-agent   |               |   turzx-integration-agent      |   |
|   |  - USB Bulk (0x1A86:5722)|               |  - Session Bridge              |   |
|   |  - CDC Serial (/dev/cu*) |               |  - Agent Quotas                |   |
|   |  - Failover & Recovery   |               |  - Redis & External Agents     |   |
|   +--------------------------+               +--------------------------------+   |
|                                                                                   |
|                        +---------------------------+                              |
|                        |     turzx-test-agent      |                              |
|                        |  - Mock USB & Serial      |                              |
|                        |  - CI/CD Test Harness     |                              |
|                        +---------------------------+                              |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                               Physical Hardware                                   |
|                CH340/CH341 USB-to-Serial & AX206/LCM Display Buffer               |
+-----------------------------------------------------------------------------------+
```

---

## 2. Agent Catalog & Taxonomy

The ecosystem consists of five specialized agents registered in `.agents/ai/turzx/`:

| Agent ID | Name | Role & Responsibility | Entrypoint |
|---|---|---|---|
| `turzx-core-agent` | Core Display Agent | Manages RGB565 framebuffer encoding, screen orientation, brightness, and high-level rendering pipelines. | `turzx.cli:main` |
| `turzx-scheduler-agent` | Task Scheduler Agent | Coordinates background jobs (interval, cron, startup, one-off), health monitoring, and graceful restarts. | `turxz_scheduled_jobs.cli:main` |
| `turzx-hardware-agent` | Hardware Interface Agent | Controls raw USB bulk I/O (`0x1A86:0x5722`) and CDC serial communication with hardware safety guards. | `turzx.driver:TURZXDriver` |
| `turzx-integration-agent` | Integration & Bridge Agent | Synchronizes multi-agent state with external LLM sessions, agent quota limits, and system status widgets. | `agent_quota_manager:AgentQuotaManager` |
| `turzx-test-agent` | Test & Verification Agent | Provides deterministic hardware mocks, protocol fuzzing, regression tests, and verification in CI/CD. | `scripts/test.sh` |

---

## 3. The Five Golden Rules of Agent Governance

Every agent executing in the TURXZ runtime must strictly enforce and comply with the following five non-negotiable rules:

### Rule 1: Zero Assumption Compliance
- **No Blind IO**: Agents must never assume hardware is plugged in, powered on, or in a ready state without active verification.
- **Hardware Probing**: Low-level operations must explicitly probe USB vendor/product IDs (`0x1A86:0x5722`) or enumerate CDC serial device paths (`/dev/cu.usbmodem*` or `/dev/ttyUSB*`).
- **State Validation**: If device descriptors cannot be queried or handshake packets fail, the agent must not proceed with raw buffer writes.

### Rule 2: Fail-Closed Enforcement
- **Abort on Anomaly**: Any malformed payload, invalid checksum, unverified token, or unexpected response code immediately triggers an execution abort.
- **Safety First**: The agent will never attempt undefined fallback recovery that could flood USB endpoints, corrupt display memory, or lock serial ports.
- **Audit Logging**: Failures are recorded with structured metadata using `structlog` (or local standard logger) and logged at `ERROR` level before terminating the operation.

### Rule 3: Semantic Signature Verification
- **Schema Contracts**: Every inter-agent message, job specification, and CLI command payload must adhere to the defined JSON schemas in `.agents/ai/`.
- **Protocol Signatures**: Packets destined for the hardware layer must contain the valid command prelude and checksum matching the documented protocol (`rev_a`, `rev_b`, `rev_c`).
- **Contract Mismatch Prevention**: Agents encountering mismatched capability signatures must reject the command without execution.

### Rule 4: Resource Boundary Enforcement
- **Frame Rate Caps**: Display render loops must enforce a hard upper limit of 30 FPS (`max_frame_rate_fps: 30`) to protect USB bus bandwidth and display controller thermal limits.
- **Memory Footprint**: Image buffers must not exceed the physical panel dimensions (e.g., 320x480x2 = 307,200 bytes for 3.5" or 800x480x2 = 768,000 bytes for 7.0").
- **Concurrency & Timeouts**: The task scheduler caps simultaneous execution at 10 concurrent jobs with a mandatory default timeout of 300 seconds per task.

### Rule 5: Lightweight Local Process Token Authentication
To secure communications between independent processes (e.g., CLI commands, background scheduler daemon, and display rendering bridges) running locally on macOS without the overhead of heavy asymmetric cryptography, TURXZ implements a **Lightweight Local Process Token**:
- **Mechanism**: A shared session token (`TURXZ_PROCESS_TOKEN`) is generated upon daemon initialization using cryptographically secure pseudorandom bytes (`secrets.token_hex(32)`).
- **Storage**: Stored in a restricted POSIX file (`$TMPDIR/turxz-$UID/process.token` or `~/.turzx/token`) with file mode `0600` (read/write only by owner process).
- **Verification**: Any local agent invoking control commands (display clearing, job rescheduling, quota reset) presents this token via header or intra-process call.
- **Replay Protection**: The token is regenerated on service startup and validated in constant-time (`hmac.compare_digest`). Unauthenticated calls are rejected with `PermissionDeniedError` (`fail-closed`).

---

## 4. Permission Taxonomy

Agents are assigned granular permissions under a principle of least privilege:

| Permission String | Description | Permitted Agents |
|---|---|---|
| `display:write` | Write pixel buffers and render commands to display | `turzx-core-agent` |
| `display:notify` | Request display notification overlay | `turzx-integration-agent` |
| `image:transform` | Resize, crop, and convert image formats to RGB565 | `turzx-core-agent` |
| `cli:execute` | Parse and execute command line arguments | `turzx-core-agent` |
| `scheduler:manage`| Add, remove, pause, and trigger scheduled jobs | `turzx-scheduler-agent` |
| `job:execute` | Run job worker coroutines and subprocesses | `turzx-scheduler-agent` |
| `telemetry:read` | Read scheduler health and job execution metrics | `turzx-scheduler-agent`, `turzx-integration-agent` |
| `telemetry:write`| Write metrics and event telemetry | `turzx-scheduler-agent` |
| `state:persist` | Save and restore job state from disk | `turzx-scheduler-agent` |
| `usb:raw_io` | Direct USB bulk transfer to endpoints `0x03` / `0x82` | `turzx-hardware-agent` |
| `serial:rw` | Read/write access to CH340 serial port | `turzx-hardware-agent` |
| `device:reset` | Send hardware re-enumeration and reset signals | `turzx-hardware-agent` |
| `quota:manage` | Read and mutate external AI agent quotas | `turzx-integration-agent` |
| `session:bridge` | Bridge external agent events to local state | `turzx-integration-agent` |
| `redis:rw` | Communicate with local Redis cache | `turzx-integration-agent` |
| `test:execute` | Run automated test harnesses and benchmarks | `turzx-test-agent` |
| `device:mock` | Instantiate mock USB/serial hardware fixtures | `turzx-test-agent` |
| `report:generate` | Produce test and compliance reports | `turzx-test-agent` |

---

## 5. Incident Response & Recovery Runbooks

### Incident 1: USB Endpoint Freeze / Pipe Stalled
1. **Detection**: `turzx-hardware-agent` receives `usb.core.USBError: [Errno 32] Pipe error` or repeated timeout (> 3000ms).
2. **Action**:
   - Immediately abort active transfer (Fail-Closed).
   - Attempt endpoint clear-halt (`dev.clear_halt(endpoint)`).
   - If clear-halt fails, trigger device reset via `dev.reset()`.
   - Log incident with timestamp and packet counter.
   - Wait 1.5 seconds before re-probing descriptor.

### Incident 2: Corrupted Frame Buffer
1. **Detection**: `turzx-core-agent` detects length mismatch (`len(buffer) != width * height * 2`).
2. **Action**:
   - Drop frame immediately; do not send truncated payload over USB.
   - Re-encode frame from source PIL Image.
   - If issue persists, blank screen to black `#000000` to prevent visual artifacts.

### Incident 3: Authentication Failure / Stale Token
1. **Detection**: Mismatched `TURXZ_PROCESS_TOKEN` during IPC invocation.
2. **Action**:
   - Reject request with HTTP 403 / Exit Code 13.
   - Increment suspicious request counter.
   - If consecutive failures exceed 5 within 60 seconds, temporarily lock IPC interface for 30 seconds.

---

## 6. Continuous Compliance Verification

Compliance with this framework is mechanically enforced:
- **Git Hooks**: `.githooks/pre-commit` verifies all agent JSON schemas and executes lint gates before code can be staged.
- **Pre-Push Gate**: `.githooks/pre-push` runs the complete test matrix including hardware mocks.
- **CI/CD Pipeline**: `.github/workflows/ci.yml` runs automated schema validation and multi-version test matrices on every pull request.
