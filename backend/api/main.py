"""
API module for DeepSeek Web Bridge Backend.
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional, Any
from datetime import datetime
import redis
import structlog
import asyncio
import threading
import time

# Initialize global services
try:
    redis_client = redis.from_url("redis://localhost:6379/0")
    redis_client.ping()  # Test connection
except Exception as e:
    print(f"Warning: Could not connect to Redis: {e}")
    redis_client = None

# Initialize TURXZ display service
try:
    from backend.services.turzx_display_service import TURZXDisplayService
    _turxz_display_service = TURZXDisplayService(redis_client)
    # Initialize hardware on startup
    if _turxz_display_service:
        init_result = _turxz_display_service.initialize_hardware()
        if init_result.get('hardware_ready'):
            print("✓ TURXZ display hardware initialized successfully")
        else:
            print(f"⚠ TURXZ display hardware init issue: {init_result.get('error', 'Unknown')}")
except Exception as e:
    print(f"Warning: Could not initialize TURXZ display service: {e}")
    _turxz_display_service = None

# Initialize agent quota manager
try:
    from backend.services.agent_quota_manager import AgentQuotaManager
    _agent_quota_manager = AgentQuotaManager("redis://localhost:6379/0")
except Exception as e:
    print(f"Warning: Could not initialize agent quota manager: {e}")
    _agent_quota_manager = None

# Configure structlog
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Periodic agent-quota dashboard render loop (see _start_display_render_loop)
_display_render_running = False
_display_render_task = None
_display_render_thread = None


def _start_display_render_loop():
    """Launch the periodic agent-quota dashboard render loop (runs at app startup)."""
    global _display_render_running, _display_render_task, _display_render_thread
    if _display_render_thread is not None:
        return

    def _render_loop():
        while _display_render_running:
            try:
                if _turxz_display_service and _turxz_display_service._hardware_initialized:
                    agent_ids = [f"agy{i}" for i in range(1, 6)] + [f"codex{i}" for i in range(1, 4)]
                    agent_data = {}
                    for agent_id in agent_ids:
                        if _agent_quota_manager is None:
                            continue
                        quota = _agent_quota_manager.get_quota_status(agent_id)
                        if quota:
                            agent_data[agent_id] = quota
                    if agent_data:
                        _turxz_display_service.render_quota_display(agent_data)
            except Exception as e:
                logger.error(f"[display-render] periodic render failed: {type(e).__name__}: {e}")
            time.sleep(5)

    _display_render_running = True
    _display_render_thread = threading.Thread(target=_render_loop, daemon=True, name="turzx-display-render")
    _display_render_thread.start()
    logger.info("[display-render] periodic dashboard render loop started")


def _stop_display_render_loop():
    global _display_render_running, _display_render_thread
    _display_render_running = False
    if _display_render_thread is not None:
        try:
            _display_render_thread.join(timeout=3)
        except Exception:
            pass
        _display_render_thread = None


def create_app():
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="DeepSeek Web Bridge Backend",
        description="Backend services for TURZX display integration with agent quota management",
        version="1.0.0",
    )

    # Start the periodic agent-quota dashboard render loop so the TURZX display
    # always shows the latest agent quota data (fixes the default/blank screen).
    _start_display_render_loop()

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        print(f"DEBUG: _turxz_display_service = {_turxz_display_service}")
        print(f"DEBUG: _turxz_display_service type = {type(_turxz_display_service)}")
        if _turxz_display_service:
            print(f"DEBUG: hardware_ready = {_turxz_display_service._hardware_initialized}")
        services_status = {
            "agent_quota_manager": "healthy" if _agent_quota_manager else "uninitialized",
            "turzx_display_service": "healthy" if _turxz_display_service else "uninitialized",
        }
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "services": services_status,
        }

    @app.post("/api/v1/quota/create")
    async def create_quota(request: Dict[str, Any]):
        """Create quota for an agent."""
        if not _agent_quota_manager:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        try:
            result = _agent_quota_manager.create_agent_quota(
                agent_id=request.get("agent_id", "test_agent"),
                quota_limit=request.get("quota_limit", 1000.0),
                warning_threshold=request.get("warning_threshold", 0.8),
                burst_allowed=request.get("burst_allowed", True),
            )
            
            return {
                "success": True,
                "agent_id": result.agent_id,
                "quota_limit": result.quota_limit,
                "warning_threshold": result.warning_threshold,
                "burst_allowed": result.burst_allowed,
                "current_usage": result.current_usage,
                "status": result.status.value,
            }
        except Exception as e:
            logger.error(f"Failed to create quota: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to create quota: {str(e)}")

    @app.post("/api/v1/quota/use")
    async def use_quota(request: Dict[str, Any]):
        """Use quota for an agent."""
        if not _agent_quota_manager:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        try:
            result = _agent_quota_manager.use_quota(
                agent_id=request.get("agent_id", "test_agent"),
                units=request.get("units", 1.0),
                allow_burst=request.get("allow_burst", True),
            )
            
            return result
        except Exception as e:
            logger.error(f"Failed to use quota: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to use quota: {str(e)}")

    @app.post("/api/v1/quota/validate")
    async def validate_quota(request: Dict[str, Any]):
        """Validate quota for operation."""
        if not _agent_quota_manager:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        try:
            result = _agent_quota_manager.validate_quota_for_operation(
                agent_id=request.get("agent_id", "test_agent"),
                required_units=request.get("units", 1.0),
                allow_burst=request.get("allow_burst", True),
            )
            
            return result
        except Exception as e:
            logger.error(f"Failed to validate quota: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to validate quota: {str(e)}")

    @app.post("/api/v1/turzx/display/sessions")
    async def create_display_session(request: Dict[str, Any]):
        """Create a new display session."""
        if not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        try:
            session_id = f"session_{int(datetime.now().timestamp())}_{request.get('agent_id', 'test_agent')}"
            
            # Convert initial_config dict to TURZXConfig dataclass
            initial_config = request.get("initial_config", {})
            from backend.services.turzx_display_service import TURZXConfig
            turzx_config = TURZXConfig(**initial_config) if initial_config else TURZXConfig()
            
            session = _turxz_display_service.create_display_session(
                session_id=session_id,
                agent_id=request.get("agent_id", "test_agent"),
                initial_config=turzx_config,
            )
            
            return session.to_dict()
        except Exception as e:
            logger.error(f"Failed to create display session: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to create display session: {str(e)}")

    @app.get("/api/v1/turzx/display/status")
    async def get_display_status():
        """Get display status."""
        if not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        try:
            status = _turxz_display_service.get_system_status()
            return status
        except Exception as e:
            logger.error(f"Failed to get display status: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to get display status: {str(e)}")

    @app.get("/api/v1/turzx/display/analytics")
    async def get_display_analytics(hours: int = 24):
        """Get display analytics."""
        if not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        try:
            analytics = _turxz_display_service.get_display_analytics(hours)
            return analytics
        except Exception as e:
            logger.error(f"Failed to get display analytics: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to get display analytics: {str(e)}")

    @app.put("/api/v1/turzx/display/sessions/{session_id}/status")
    async def update_session_status(session_id: str, request: Dict[str, Any]):
        """Update session status."""
        if not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        try:
            if not request.get("status"):
                raise HTTPException(status_code=400, detail="Status is required")
            
            return {
                "success": True,
                "session_id": session_id,
                "status": request.get("status"),
                "message": "Session status updated successfully (mock)",
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update session status: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to update session status: {str(e)}")

    @app.get("/api/v1/turzx/display/sessions")
    async def list_display_sessions():
        """List all display sessions."""
        if not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Service not initialized")
        
        try:
            sessions = _turxz_display_service.get_all_sessions()
            return {"sessions": sessions}
        except Exception as e:
            logger.error(f"Failed to list display sessions: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to list display sessions: {str(e)}")

    @app.post("/api/v1/turzx/display/image")
    async def display_custom_image(request: Dict[str, Any]):
        """Display an image on the TURZX hardware monitor."""
        if not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Display service not initialized")
        
        image_path = request.get("image_path")
        image_base64 = request.get("image_base64")
        
        if not image_path and not image_base64:
            raise HTTPException(status_code=400, detail="Either image_path or image_base64 must be provided")
            
        try:
            if image_path:
                result = _turxz_display_service.display_image_file(image_path)
            else:
                import base64
                import io
                from PIL import Image
                image_bytes = base64.b64decode(image_base64)
                pil_img = Image.open(io.BytesIO(image_bytes))
                result = _turxz_display_service.display_pil_image(pil_img)
                
            return {"success": result.get("success", False), "result": result}
        except Exception as e:
            logger.error("Failed to display image", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/turzx/display/clear")
    async def clear_hardware_display():
        """Clear the TURZX hardware monitor."""
        if not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Display service not initialized")
        success = _turxz_display_service.clear_display()
        return {"success": success}

    @app.post("/api/v1/turzx/display/brightness")
    async def set_hardware_brightness(request: Dict[str, Any]):
        """Set TURZX hardware monitor brightness (0-100)."""
        if not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Display service not initialized")
        level = request.get("level", 50)
        success = _turxz_display_service.set_display_brightness(int(level))
        return {"success": success, "level": level}

    @app.get("/api/v1/agent/dashboard")
    async def get_agent_dashboard():
        """Get agent dashboard data for all 8 agents (agy1-agy5, codex1-codex3) with quotas."""
        if not _agent_quota_manager or not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Services not initialized")

            try:
                agent_ids = [f"agy{i}" for i in range(1, 6)] + [f"codex{i}" for i in range(1, 4)]

                quota_statuses = {}
                all_sessions = _turxz_display_service.get_all_sessions()
                display_analytics = _turxz_display_service.get_display_analytics(hours=24)

                for agent_id in agent_ids:
                    quota_statuses[agent_id] = _agent_quota_manager.get_quota_status(agent_id)

                # Collect sessions per agent
                agent_sessions = {}
                for agent_id in agent_ids:
                    agent_sessions[agent_id] = [
                        s for s in all_sessions if s.get("agent_id") == agent_id
                    ]

                return {
                    "agents": agent_ids,
                    "quota_statuses": quota_statuses,
                    "agent_sessions": agent_sessions,
                    "display_analytics": display_analytics,
                }
            except Exception as e:
                logger.error(f"Failed to get agent dashboard: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to get agent dashboard: {str(e)}")

    @app.get("/ready")
    async def readiness_probe():
        """Readiness probe endpoint."""
        if not _agent_quota_manager or not _turxz_display_service:
            raise HTTPException(status_code=503, detail="Services not initialized")
        
        try:
            quota_health = _agent_quota_manager.health_check()
            if quota_health.get("status") != "healthy":
                raise HTTPException(status_code=503, detail="Quota manager not healthy")
            
            display_health = _turxz_display_service.health_check()
            if display_health.get("status") != "healthy":
                raise HTTPException(status_code=503, detail="Display service not healthy")
            
            return {"status": "ready"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Service not ready: {str(e)}")
            raise HTTPException(status_code=503, detail=f"Service not ready: {str(e)}")

    @app.get("/status")
    async def get_system_status():
        """Get system status."""
        status = {
            "timestamp": datetime.now().isoformat(),
            "version": "1.0.0",
            "services": {
                "agent_quota_manager": "healthy" if _agent_quota_manager else "uninitialized",
                "turzx_display_service": "healthy" if _turxz_display_service else "uninitialized",
            },
            "active_sessions": _turxz_display_service.get_all_sessions() if _turxz_display_service else [],
        }
        
        return status

    websocket_clients: set = set()

    @app.websocket("/api/v1/turzx/display/ws")
    async def websocket_display_update(websocket: WebSocket):
        """WebSocket endpoint for real-time display updates."""
        await websocket.accept()
        websocket_clients.add(websocket)
        try:
            while True:
                try:
                    data = await asyncio.wait_for(websocket.receive_json(), timeout=30)
                    await websocket.send_json(data)
                except asyncio.TimeoutError:
                    # Send a keep-alive ping or keep connection alive
                    await websocket.ping()
        except WebSocketDisconnect:
            websocket_clients.discard(websocket)
        except Exception:
            websocket_clients.discard(websocket)

    return app
