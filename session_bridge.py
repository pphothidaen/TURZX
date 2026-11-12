"""
Session bridge service for DeepSeek Web Bridge - TURZX integration
Handles session synchronization and message bridging between systems
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import structlog
import redis
import psycopg2
from psycopg2.extras import RealDictCursor
from dataclasses import dataclass, asdict
from enum import Enum

logger = structlog.get_logger(__name__)

class MessageType(Enum):
    SESSION_CREATE = "session_create"
    SESSION_UPDATE = "session_update"
    MESSAGE = "message"
    SESSION_SYNC = "session_sync"
    HEARTBEAT = "heartbeat"

@dataclass
class SessionData:
    session_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    status: str = "active"
    created_at: datetime = None
    updated_at: datetime = None
    metadata: Dict[str, Any] = None
    messages: List[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.updated_at is None:
            self.updated_at = datetime.utcnow()
        if self.metadata is None:
            self.metadata = {}
        if self.messages is None:
            self.messages = []

class SessionBridge:
    """Manages session synchronization between DeepSeek Bridge and TURZX systems"""
    
    def __init__(self):
        self.redis_client = None
        self.database_pool = None
        self.session_queue = "session_events"
        self.message_queue = "integration_queue"
        self.session_ttl = 3600  # 1 hour
        
        # Initialize connections
        self._init_connections()
        
        # Background tasks
        self._cleanup_task = None
        self._sync_task = None
    
    def _init_connections(self):
        """Initialize Redis and database connections"""
        try:
            self.redis_client = redis.Redis(
                host='localhost',
                port=6379,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            logger.info("Redis connection established for session bridge")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
        
        try:
            import os
            database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/turzx')
            self.database_pool = psycopg2.pool.SimpleConnectionPool(
                1, 5, database_url,
                cursor_factory=RealDictCursor
            )
            logger.info("Database connection pool created for session bridge")
        except Exception as e:
            logger.error(f"Failed to create database pool: {e}")
    
    async def create_session(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new session and synchronize across systems"""
        try:
            session_id = session_data.get("session_id", f"sess_{int(time.time() * 1000)}")
            
            # Create session object
            session = SessionData(**session_data)
            session.session_id = session_id
            session.created_at = datetime.utcnow()
            session.updated_at = datetime.utcnow()
            
            # Store in Redis for fast access
            await self._store_session_redis(session)
            
            # Store in database for persistence
            await self._store_session_database(session)
            
            # Publish session creation event
            await self._publish_session_event("create", asdict(session))
            
            # Notify DeepSeek Bridge about new session
            await self._notify_deepseek_bridge(session)
            
            logger.info(f"Session created successfully: {session_id}")
            
            return {
                "status": "created",
                "session_id": session_id,
                "session": asdict(session),
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def update_session(self, session_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update existing session with new data"""
        try:
            # Get current session
            current_session = await self._get_session_redis(session_id)
            if not current_session:
                return {
                    "status": "error",
                    "error": f"Session {session_id} not found",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Apply updates
            for key, value in updates.items():
                if hasattr(current_session, key):
                    setattr(current_session, key, value)
            
            current_session.updated_at = datetime.utcnow()
            
            # Update both storage systems
            await self._update_session_redis(current_session)
            await self._update_session_database(current_session)
            
            # Publish update event
            await self._publish_session_event("update", {
                "session_id": session_id,
                "updates": updates
            })
            
            logger.info(f"Session updated: {session_id}")
            
            return {
                "status": "updated",
                "session_id": session_id,
                "updates": updates,
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to update session: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def add_message(self, session_id: str, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add message to session and synchronize"""
        try:
            # Get session
            session = await self._get_session_redis(session_id)
            if not session:
                return {
                    "status": "error",
                    "error": f"Session {session_id} not found",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Add message to session
            message = {
                "id": message_data.get("id", f"msg_{int(time.time() * 1000)}"),
                "role": message_data.get("role", "user"),
                "content": message_data.get("content", ""),
                "timestamp": message_data.get("timestamp", int(time.time() * 1000)),
                "metadata": message_data.get("metadata", {})
            }
            
            session.messages.append(message)
            session.updated_at = datetime.utcnow()
            
            # Update storage systems
            await self._update_session_redis(session)
            await self._update_session_database(session)
            
            # Publish message event
            await self._publish_session_event("message", {
                "session_id": session_id,
                "message": message
            })
            
            # Notify DeepSeek Bridge if needed
            await self._notify_deepseek_bridge_message(session, message)
            
            logger.info(f"Message added to session: {session_id}, message_id: {message['id']}")
            
            return {
                "status": "added",
                "session_id": session_id,
                "message": message,
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to add message: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def sync_session_data(self, session_id: str) -> Dict[str, Any]:
        """Force synchronization of session data between systems"""
        try:
            # Get data from both systems
            redis_data = await self._get_session_redis(session_id)
            db_data = await self._get_session_database(session_id)
            
            if not redis_data and not db_data:
                return {
                    "status": "error",
                    "error": f"Session {session_id} not found in either system",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Resolve conflicts and merge data
            merged_session = await self._merge_session_data(redis_data, db_data)
            
            # Update both systems with merged data
            await self._store_session_redis(merged_session)
            await self._store_session_database(merged_session)
            
            logger.info(f"Session synchronized: {session_id}")
            
            return {
                "status": "synced",
                "session_id": session_id,
                "redis_source": redis_data is not None,
                "db_source": db_data is not None,
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to sync session: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data from unified source"""
        try:
            # Try Redis first (fast access)
            session = await self._get_session_redis(session_id)
            if session:
                return sessionasdict(
            
            # Fall back to database
            session = await self._get_session_database(session_id)
            if session:
                # Cache in Redis for future access
                await self._store_session_redis(session)
                return sessionasdict(
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get session: {e}")
            return None
    
    async def get_all_sessions(self) -> List[Dict[str, Any]]:
        """Get all active sessions from both systems"""
        try:
            sessions = []
            
            # Get sessions from Redis
            if self.redis_client:
                redis_keys = self.redis_client.keys("session:*")
                for key in redis_keys:
                    session_data = self.redis_client.hgetall(key)
                    if session_data:
                        session_id = key.split(":")[1]
                        session = SessionData(**session_data)
                        session.session_id = session_id
                        sessions.append(sessionasdict()
            
            # Get sessions from database
            if self.database_pool:
                conn = self.database_pool.getconn()
                try:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT * FROM display_sessions WHERE status = 'active'")
                        db_sessions = cursor.fetchall()
                        
                        for db_session in db_sessions:
                            session_dict = dict(db_session)
                            session_id = session_dict.get("session_id")
                            
                            # Check if we already have this session from Redis
                            existing_session = next((s for s in sessions if s["session_id"] == session_id), None)
                            if not existing_session:
                                sessions.append(session_dict)
                finally:
                    self.database_pool.putconn(conn)
            
            return sessions
            
        except Exception as e:
            logger.error(f"Failed to get all sessions: {e}")
            return []
    
    async def cleanup_expired_sessions(self):
        """Clean up expired sessions from both systems"""
        try:
            # Clean up Redis sessions
            if self.redis_client:
                redis_keys = self.redis_client.keys("session:*")
                for key in redis_keys:
                    session_data = self.redis_client.hgetall(key)
                    if session_data:
                        session = SessionData(**session_data)
                        if datetime.utcnow() - session.updated_at > timedelta(hours=1):
                            self.redis_client.delete(key)
                            logger.info(f"Cleaned up expired Redis session: {key}")
            
            # Clean up database sessions
            if self.database_pool:
                conn = self.database_pool.getconn()
                try:
                    with conn.cursor() as cursor:
                        # Delete sessions older than 24 hours
                        cursor.execute(
                            "DELETE FROM display_sessions WHERE updated_at < NOW() - INTERVAL '24 hours'"
                        )
                    conn.commit()
                    logger.info("Cleaned up expired database sessions")
                finally:
                    self.database_pool.putconn(conn)
                    
        except Exception as e:
            logger.error(f"Failed to cleanup expired sessions: {e}")
    
    # Private methods
    
    async def _store_session_redis(self, session: SessionData):
        """Store session in Redis"""
        try:
            key = f"session:{session.session_id}"
            session_data = sessionasdict(
            
            # Convert datetime objects to ISO format strings
            session_data["created_at"] = session.created_at.isoformat()
            session_data["updated_at"] = session.updated_at.isoformat()
            
            self.redis_client.hmset(key, session_data)
            self.redis_client.expire(key, self.session_ttl)
            
        except Exception as e:
            logger.error(f"Failed to store session in Redis: {e}")
            raise
    
    async def _store_session_database(self, session: SessionData):
        """Store session in database"""
        try:
            if not self.database_pool:
                return
            
            conn = self.database_pool.getconn()
            try:
                with conn.cursor() as cursor:
                    # Prepare session data for database
                    db_data = sessionasdict(
                    
                    # Check if session already exists
                    cursor.execute(
                        "SELECT session_id FROM display_sessions WHERE session_id = %s",
                        (session.session_id,)
                    )
                    existing = cursor.fetchone()
                    
                    if existing:
                        # Update existing session
                        cursor.execute("""
                            UPDATE display_sessions 
                            SET updated_at = %s, metadata = %s, message_count = %s
                            WHERE session_id = %s
                        """, (
                            session.updated_at,
                            json.dumps(session.metadata),
                            len(session.messages),
                            session.session_id
                        ))
                    else:
                        # Insert new session
                        cursor.execute("""
                            INSERT INTO display_sessions 
                            (session_id, user_id, agent_id, status, created_at, updated_at, metadata, message_count)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            session.session_id,
                            session.user_id,
                            session.agent_id,
                            session.status,
                            session.created_at,
                            session.updated_at,
                            json.dumps(session.metadata),
                            len(session.messages)
                        ))
                
                conn.commit()
                
            finally:
                self.database_pool.putconn(conn)
                
        except Exception as e:
            logger.error(f"Failed to store session in database: {e}")
            raise
    
    async def _get_session_redis(self, session_id: str) -> Optional[SessionData]:
        """Get session from Redis"""
        try:
            if not self.redis_client:
                return None
            
            key = f"session:{session_id}"
            session_data = self.redis_client.hgetall(key)
            
            if not session_data:
                return None
            
            # Convert string values back to appropriate types
            for key, value in session_data.items():
                if key.endswith("_at"):
                    try:
                        session_data[key] = datetime.fromisoformat(value)
                    except:
                        pass
                elif key == "metadata":
                    try:
                        session_data[key] = json.loads(value)
                    except:
                        session_data[key] = {}
                elif key == "messages":
                    try:
                        session_data[key] = json.loads(value)
                    except:
                        session_data[key] = []
            
            return SessionData(**session_data)
            
        except Exception as e:
            logger.error(f"Failed to get session from Redis: {e}")
            return None
    
    async def _get_session_database(self, session_id: str) -> Optional[SessionData]:
        """Get session from database"""
        try:
            if not self.database_pool:
                return None
            
            conn = self.database_pool.getconn()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT * FROM display_sessions WHERE session_id = %s",
                        (session_id,)
                    )
                    db_session = cursor.fetchone()
                    
                    if not db_session:
                        return None
                    
                    # Convert to dict
                    session_dict = dict(db_session)
                    
                    # Parse JSON fields
                    if session_dict.get("metadata"):
                        session_dict["metadata"] = json.loads(session_dict["metadata"])
                    
                    if session_dict.get("messages"):
                        session_dict["messages"] = json.loads(session_dict["messages"])
                    
                    return SessionData(**session_dict)
                    
            finally:
                self.database_pool.putconn(conn)
                
        except Exception as e:
            logger.error(f"Failed to get session from database: {e}")
            return None
    
    async def _update_session_redis(self, session: SessionData):
        """Update session in Redis"""
        await self._store_session_redis(session)
    
    async def _update_session_database(self, session: SessionData):
        """Update session in database"""
        try:
            if not self.database_pool:
                return
            
            conn = self.database_pool.getconn()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE display_sessions 
                        SET updated_at = %s, metadata = %s, message_count = %s
                        WHERE session_id = %s
                    """, (
                        session.updated_at,
                        json.dumps(session.metadata),
                        len(session.messages),
                        session.session_id
                    ))
                
                conn.commit()
                
            finally:
                self.database_pool.putconn(conn)
                
        except Exception as e:
            logger.error(f"Failed to update session in database: {e}")
            raise
    
    async def _publish_session_event(self, event_type: str, data: Dict[str, Any]):
        """Publish session event to message queue"""
        try:
            if not self.redis_client:
                return
            
            event = {
                "type": event_type,
                "data": data,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "session_bridge"
            }
            
            message = json.dumps(event)
            self.redis_client.lpush(self.session_queue, message)
            
        except Exception as e:
            logger.error(f"Failed to publish session event: {e}")
    
    async def _notify_deepseek_bridge(self, session: SessionData):
        """Notify DeepSeek Bridge about new session"""
        try:
            # This would integrate with DeepSeek Bridge's session management
            # For now, we just log the notification
            logger.info(f"Notifying DeepSeek Bridge about new session: {session.session_id}")
            
            # In a real implementation, this would:
            # 1. Send HTTP request to DeepSeek Bridge API
            # 2. Create corresponding session on DeepSeek side
            # 3. Establish WebSocket connection for streaming
            
        except Exception as e:
            logger.error(f"Failed to notify DeepSeek Bridge: {e}")
    
    async def _notify_deepseek_bridge_message(self, session: SessionData, message: Dict[str, Any]):
        """Notify DeepSeek Bridge about new message"""
        try:
            # This would integrate with DeepSeek Bridge's message processing
            logger.info(f"Notifying DeepSeek Bridge about new message: {message['id']} in session: {session.session_id}")
            
            # In a real implementation, this would:
            # 1. Send message to DeepSeek Bridge's processing queue
            # 2. Wait for DeepSeek response
            # 3. Forward response back to TURZX
            
        except Exception as e:
            logger.error(f"Failed to notify DeepSeek Bridge about message: {e}")
    
    async def _merge_session_data(self, redis_data: Optional[SessionData], db_data: Optional[SessionData]) -> SessionData:
        """Merge session data from different sources"""
        if redis_data and db_data:
            # Merge by preferring Redis data but filling missing fields from DB
            merged = SessionData(
                session_id=redis_data.session_id or db_data.session_id,
                user_id=redis_data.user_id or db_data.user_id,
                agent_id=redis_data.agent_id or db_data.agent_id,
                status=redis_data.status or db_data.status,
                created_at=redis_data.created_at or db_data.created_at,
                updated_at=max(redis_data.updated_at, db_data.updated_at),
                metadata={**redis_data.metadata, **db_data.metadata},
                messages=redis_data.messages or db_data.messages
            )
        elif redis_data:
            merged = redis_data
        elif db_data:
            merged = db_data
        else:
            raise ValueError("Both session data sources are None")
        
        return merged


# FastAPI router for session bridge endpoints
from fastapi import FastAPI, APIRouter

router = APIRouter(prefix="/api/v1/sessions")

# Global session bridge instance
session_bridge = SessionBridge()

@router.post("/")
async def create_session(session_data: Dict[str, Any]):
    """Create a new session"""
    return await session_bridge.create_session(session_data)

@router.put("/{session_id}")
async def update_session(session_id: str, updates: Dict[str, Any]):
    """Update existing session"""
    return await session_bridge.update_session(session_id, updates)

@router.post("/{session_id}/messages")
async def add_message(session_id: str, message_data: Dict[str, Any]):
    """Add message to session"""
    return await session_bridge.add_message(session_id, message_data)

@router.get("/{session_id}")
async def get_session(session_id: str):
    """Get session data"""
    return await session_bridge.get_session(session_id)

@router.get("/")
async def get_all_sessions():
    """Get all active sessions"""
    return await session_bridge.get_all_sessions()

@router.post("/{session_id}/sync")
async def sync_session(session_id: str):
    """Force session synchronization"""
    return await session_bridge.sync_session_data(session_id)

# Background task setup
async def start_background_tasks():
    """Start background maintenance tasks"""
    session_bridge._cleanup_task = asyncio.create_task(session_bridge.cleanup_expired_sessions())
    session_bridge._sync_task = asyncio.create_task(session_bridge.periodic_sync())

async def stop_background_tasks():
    """Stop background maintenance tasks"""
    if session_bridge._cleanup_task:
        session_bridge._cleanup_task.cancel()
    if session_bridge._sync_task:
        session_bridge._sync_task.cancel()

# Periodic sync method
async def periodic_sync(self):
    """Periodically sync sessions between systems"""
    while True:
        try:
            await asyncio.sleep(300)  # Sync every 5 minutes
            logger.info("Running periodic session sync")
            await self._sync_all_sessions()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic sync: {e}")