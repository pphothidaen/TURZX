# TURXZ AI Agent Architecture & Governance Catalog

Welcome to the **TURXZ AI Agent Scaffolding** directory. This directory contains machine-readable agent definitions, security boundaries, capability declarations, and governance policies for all autonomous and semi-autonomous agents operating within the TURXZ secondary display ecosystem.

## 📁 Directory Structure

```
.agents/ai/
├── README.md                              # This specification catalog
└── turzx/
    ├── turzx_agent.json                   # Core Driver & CLI Agent
    ├── turzx_scheduler_agent.json         # Scheduler & Lifecycle Agent
    ├── turzx_hardware_agent.json          # USB Bulk & Serial Protocol Agent
    ├── turzx_integration_agent.json       # Session Bridge & Quota Manager Agent
    └── turzx_test_agent.json              # Mock Hardware & Test Validation Agent
```

---

## 🤖 Agent Matrix

| Agent ID | Role | Category | Primary Responsibility | Permissions |
|---|---|---|---|---|
| `turzx-core-agent` | Core Display Agent | `core` | High-level display rendering, image conversions (RGB565), and CLI execution | `display:write`, `image:transform`, `cli:execute` |
| `turzx-scheduler-agent` | Task Scheduler Agent | `scheduler` | Job registration, cron/interval triggers, health monitoring, and cleanup | `scheduler:manage`, `job:execute`, `telemetry:read` |
| `turzx-hardware-agent` | Hardware Interface Agent | `hardware` | USB bulk transfer (`0x1A86:0x5722`), CDC serial detection, baud rate negotiation | `usb:raw_io`, `serial:rw`, `device:reset` |
| `turzx-integration-agent` | Bridge & Quota Agent | `integration` | Session synchronization, Redis state bridge, agent quota thresholds | `session:bridge`, `quota:manage`, `redis:rw` |
| `turzx-test-agent` | Testing & Verification Agent | `testing` | Mock device harness, protocol regression testing, and CI verification | `test:execute`, `device:mock`, `report:generate` |

---

## 🔒 Governance & Security Enforcement

All TURXZ agents are bound by the governance rules detailed in [AGENTS.md](../../AGENTS.md):

1. **Zero Assumption Compliance**: No agent assumes hardware presence without proactive verification (probing USB descriptors `0x1A86:0x5722` or CDC device paths).
2. **Fail-Closed Enforcement**: Any unrecognized packet header, corrupted image buffer, or missing token immediately aborts execution and safely logs the incident without bricking device controllers.
3. **Semantic Signature Verification**: Inter-agent signals and remote control messages require schema and semantic signature matching.
4. **Resource Boundary Enforcement**: Memory usage, CPU time, transfer packet sizes (max 480x320x2 bytes per frame), and FPS rates (max 30 fps) are strictly bounded.
5. **Lightweight Local Process Token Authentication**: Intra-process and local macOS loopback communications validate a shared process token before mutating display state.

---

## 🛠️ Verification & Linting

All JSON definitions in this directory are automatically validated by:
- `.githooks/pre-commit` (local commit gate)
- `.github/workflows/ci.yml` (CI/CD pipeline)

To manually validate the definitions:
```bash
python3 -c "import json, glob; [json.load(open(f)) for f in glob.glob('.agents/ai/**/*.json', recursive=True)]; print('✅ All agent definitions valid!')"
```
