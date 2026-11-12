"""
Health checking service for DeepSeek Web Bridge integration
Handles health monitoring across DeepSeek Bridge and TURZX display systems
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import structlog
from fastapi import HTTPException
import httpx
import redis
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

logger = structlog.get_logger(__name__)

class HealthChecker:
    """Comprehensive health checking for cross-system integration"""
    
    def __init__(self):
        self.redis_client = None
        self.database_pool = None
        self.deepseek_health_url = "http://localhost:8080/health"
        self.turzx_health_url = "http://localhost:8000/health"
        self.session_expiry_threshold = timedelta(minutes=5)
        
        # Initialize connections
        self._init_connections()
    
    def _init_connections(self):
        """Initialize external service connections"""
        try:
            # Redis connection for session management
            self.redis_client = redis.Redis(
                host='localhost',
                port=6379,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True
            )
            logger.info("Redis connection established")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
        
        try:
            # Database connection for TURZX services
            import os
            database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/turzx')
            self.database_pool = psycopg2.pool.SimpleConnectionPool(
                1, 5, database_url,
                cursor_factory=RealDictCursor
            )
            logger.info("Database connection pool created")
        except Exception as e:
            logger.error(f"Failed to create database pool: {e}")
    
    async def get_system_status(self) -> Dict[str, Any]:
        """Get overall system status across all components"""
        status = {
            "timestamp": datetime.utcnow().isoformat(),
            "overall_status": "healthy",
            "components": {},
            "integration_status": {},
            "performance_metrics": {}
        }
        
        try:
            # Check DeepSeek Bridge health
            deepseek_health = await self._check_deepseek_bridge()
            status["components"]["deepseek_bridge"] = deepseek_health
            
            # Check TURZX services health
            turxz_health = await self._check_turzx_services()
            status["components"]["turzx_services"] = turxz_health
            
            # Check Redis connectivity
            redis_health = await self._check_redis()
            status["components"]["redis"] = redis_health
            
            # Check database connectivity
            db_health = await self._check_database()
            status["components"]["database"] = db_health
            
            # Check integration health
            integration_health = await self._check_integration()
            status["integration_status"] = integration_health
            
            # Calculate performance metrics
            perf_metrics = await self._get_performance_metrics()
            status["performance_metrics"] = perf_metrics
            
            # Determine overall status
            component_statuses = [
                deepseek_health.get("status", "unhealthy"),
                turxz_health.get("status", "unhealthy"),
                redis_health.get("status", "unhealthy"),
                db_health.get("status", "unhealthy")
            ]
            
            if "unhealthy" in component_statuses:
                status["overall_status"] = "degraded"
            if "critical" in component_statuses:
                status["overall_status"] = "critical"
            
        except Exception as e:
            logger.error(f"Error checking system status: {e}")
            status["overall_status"] = "critical"
            status["error"] = str(e)
        
        return status
    
    async def _check_deepseek_bridge(self) -> Dict[str, Any]:
        """Check DeepSeek Bridge health"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.deepseek_health_url)
                response.raise_for_status()
                data = response.json()
                
            return {
                "status": "healthy",
                "response_time_ms": data.get("response_time", 0),
                "service": data.get("service", "deepseek-web-bridge"),
                "details": data,
                "last_check": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"DeepSeek Bridge health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "last_check": datetime.utcnow().isoformat()
            }
    
    async def _check_turzx_services(self) -> Dict[str, Any]:
        """Check TURZX services health"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.turzx_health_url)
                response.raise_for_status()
                data = response.json()
                
            # Additional TURZX-specific checks
            turxz_details = await self._get_turzx_details()
                
            return {
                "status": "healthy",
                "response_time_ms": data.get("response_time", 0),
                "service": data.get("service", "turzx-display"),
                "details": {
                    **data,
                    "turzx_details": turxz_details
                },
                "last_check": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"TURZX services health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "last_check": datetime.utcnow().isoformat()
            }
    
    async def _get_turzx_details(self) -> Dict[str, Any]:
        """Get TURZX-specific health details"""
        try:
            if not self.database_pool:
                return {"status": "unknown", "error": "Database pool not available"}
            
            conn = self.database_pool.getconn()
            try:
                with conn.cursor() as cursor:
                    # Check active sessions
                    cursor.execute("SELECT COUNT(*) as active_sessions FROM display_sessions WHERE status = 'active'")
                    sessions_result = cursor.fetchone()
                    
                    # Check quota usage
                    cursor.execute("SELECT AVG(usage_percent) as avg_quota_usage FROM agent_quota WHERE expiry_date > NOW()")
                    quota_result = cursor.fetchone()
                    
                    # Check recent errors
                    cursor.execute("SELECT COUNT(*) as error_count FROM system_logs WHERE level = 'ERROR' AND created_at > NOW() - INTERVAL '1 hour'")
                    error_result = cursor.fetchone()
                    
                    return {
                        "active_sessions": sessions_result[0] if sessions_result else 0,
                        "average_quota_usage": float(quota_result[0]) if quota_result and quota_result[0] else 0.0,
                        "recent_errors": error_result[0] if error_result else 0,
                        "database_status": "healthy"
                    }
            finally:
                self.database_pool.putconn(conn)
                
        except Exception as e:
            logger.error(f"Failed to get TURZX details: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _check_redis(self) -> Dict[str, Any]:
        """Check Redis connectivity and performance"""
        try:
            if not self.redis_client:
                return {"status": "unavailable", "error": "Redis client not configured"}
            
            # Test connection
            start_time = time.time()
            self.redis_client.ping()
            response_time = (time.time() - start_time) * 1000
            
            # Get Redis info
            info = self.redis_client.info()
            
            return {
                "status": "healthy",
                "response_time_ms": response_time,
                "used_memory_mb": info.get('used_memory', 0) / (1024 * 1024),
                "connected_clients": info.get('connected_clients', 0),
                "uptime_seconds": info.get('uptime_in_seconds', 0),
                "last_check": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "last_check": datetime.utcnow().isoformat()
            }
    
    async def _check_database(self) -> Dict[str, Any]:
        """Check database connectivity and performance"""
        try:
            if not self.database_pool:
                return {"status": "unavailable", "error": "Database pool not configured"}
            
            start_time = time.time()
            conn = self.database_pool.getconn()
            try:
                with conn.cursor() as cursor:
                    # Test basic query
                    cursor.execute("SELECT 1 as test")
                    result = cursor.fetchone()
                    
                    # Get database info
                    cursor.execute("SELECT version(), current_database(), current_user")
                    db_info = cursor.fetchone()
                    
                    response_time = (time.time() - start_time) * 1000
                    
            finally:
                self.database_pool.putconn(conn)
                
            return {
                "status": "healthy",
                "response_time_ms": response_time,
                "database_version": db_info[0] if db_info else None,
                "database_name": db_info[1] if db_info else None,
                "current_user": db_info[2] if db_info else None,
                "last_check": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "last_check": datetime.utcnow().isoformat()
            }
    
    async def _check_integration(self) -> Dict[str, Any]:
        """Check integration-specific health"""
        try:
            # Check session synchronization
            sync_status = await self._check_session_sync()
            
            # Check message queue health
            queue_status = await self._check_message_queue()
            
            # Check API endpoints availability
            api_status = await self._check_integration_apis()
            
            return {
                "status": "healthy",
                "session_sync": sync_status,
                "message_queue": queue_status,
                "api_endpoints": api_status,
                "last_check": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Integration health check failed: {e}")
            return {
                "status": "degraded",
                "error": str(e),
                "last_check": datetime.utcnow().isoformat()
            }
    
    async def _check_session_sync(self) -> Dict[str, Any]:
        """Check session synchronization between systems"""
        try:
            if not self.redis_client:
                return {"status": "unavailable", "error": "Redis not available"}
            
            # Count sessions in Redis
            redis_sessions = self.redis_client.scard("active_sessions") or 0
            
            # Count sessions in database
            if self.database_pool:
                conn = self.database_pool.getconn()
                try:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT COUNT(*) FROM display_sessions WHERE status = 'active'")
                        db_sessions = cursor.fetchone()[0]
                    
                    # Check for synchronization issues
                    sync_diff = abs(redis_sessions - db_sessions)
                    sync_health = "healthy" if sync_diff <= 5 else "degraded"
                    
                    return {
                        "status": sync_health,
                        "redis_sessions": redis_sessions,
                        "database_sessions": db_sessions,
                        "sync_diff": sync_diff,
                        "sync_health": sync_health
                    }
                finally:
                    self.database_pool.putconn(conn)
            else:
                return {"status": "unknown", "error": "Database pool not available"}
                
        except Exception as e:
            logger.error(f"Session sync check failed: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _check_message_queue(self) -> Dict[str, Any]:
        """Check message queue health"""
        try:
            if not self.redis_client:
                return {"status": "unavailable", "error": "Redis not available"}
            
            # Check Redis queue length
            queue_length = self.redis_client.llen("integration_queue") or 0
            
            # Check for stuck messages
            stuck_messages = self.redis_client.lrange("integration_queue", 0, -1)
            stuck_count = len([msg for msg in stuck_messages if self._is_message_stuck(msg)])
            
            queue_health = "healthy" if queue_length < 1000 and stuck_count == 0 else "degraded"
            
            return {
                "status": queue_health,
                "queue_length": queue_length,
                "stuck_messages": stuck_count,
                "queue_health": queue_health,
                "last_check": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Message queue check failed: {e}")
            return {"status": "error", "error": str(e)}
    
    def _is_message_stuck(self, message: bytes) -> bool:
        """Check if a message is stuck in the queue"""
        try:
            # Message format: {"timestamp": X, "data": {...}}
            import json
            msg_data = json.loads(message.decode('utf-8'))
            timestamp = msg_data.get('timestamp', 0)
            
            # Consider stuck if older than 1 hour
            return (time.time() * 1000) - timestamp > 3600000
        except:
            return True
    
    async def _check_integration_apis(self) -> Dict[str, Any]:
        """Check integration API endpoints"""
        api_endpoints = [
            "http://localhost:8000/api/v1/bridge/status",
            "http://localhost:8000/api/v1/bridge/detailed",
            "http://localhost:8080/api/v1/chat",
            "http://localhost:8080/api/v1/history"
        ]
        
        results = {}
        for endpoint in api_endpoints:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(endpoint)
                    results[endpoint] = {
                        "status": "accessible",
                        "status_code": response.status_code
                    }
            except Exception as e:
                results[endpoint] = {
                    "status": "inaccessible",
                    "error": str(e)
                }
        
        # Determine overall API health
        accessible_count = sum(1 for r in results.values() if r.get("status") == "accessible")
        api_health = "healthy" if accessible_count >= len(api_endpoints) * 0.75 else "degraded"
        
        return {
            "status": api_health,
            "endpoint_results": results,
            "accessible_count": accessible_count,
            "total_endpoints": len(api_endpoints),
            "last_check": datetime.utcnow().isoformat()
        }
    
    async def _get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for the integration"""
        try:
            if not self.redis_client:
                return {"status": "unavailable", "error": "Redis not available"}
            
            # Get request metrics from Redis
            request_count = self.redis_client.get("metrics:request_count") or 0
            error_count = self.redis_client.get("metrics:error_count") or 0
            
            # Calculate error rate
            total_requests = int(request_count)
            error_rate = (int(error_count) / total_requests * 100) if total_requests > 0 else 0
            
            # Get average response time
            avg_response_time = self.redis_client.get("metrics:avg_response_time") or 0
            
            # Get active sessions
            active_sessions = self.redis_client.scard("active_sessions") or 0
            
            return {
                "status": "collecting",
                "request_count": total_requests,
                "error_count": int(error_count),
                "error_rate_percent": error_rate,
                "average_response_time_ms": float(avg_response_time),
                "active_sessions": int(active_sessions),
                "last_updated": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get performance metrics: {e}")
            return {"status": "error", "error": str(e)}
    
    async def refresh_systems(self) -> Dict[str, Any]:
        """Trigger health check refresh for all systems"""
        try:
            # Reset metrics
            if self.redis_client:
                self.redis_client.delete("metrics:request_count", "metrics:error_count", "metrics:avg_response_time")
            
            # Force health checks
            await self.get_system_status()
            
            return {
                "status": "refreshed",
                "message": "Health checks refreshed successfully",
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to refresh systems: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }


# FastAPI router for health endpoints
from fastapi import FastAPI, APIRouter

router = APIRouter(prefix="/api/v1/bridge")

health_checker = HealthChecker()

@router.get("/status")
async def get_bridge_status():
    """Get overall DeepSeek Bridge integration status"""
    return await health_checker.get_system_status()

@router.get("/detailed")
async def get_detailed_status():
    """Get detailed component status with metrics"""
    return await health_checker.get_system_status()

@router.post("/refresh")
async def refresh_bridge():
    """Trigger health check refresh for all systems"""
    return await health_checker.refresh_systems()

@router.get("/health")
async def health_check():
    """Basic health check endpoint"""
    return {
        "status": "ok",
        "service": "deepseek-web-bridge-integration",
        "timestamp": datetime.utcnow().isoformat()
    }