#!/usr/bin/env python3

import sqlite3
import threading
import json
import os
import hashlib
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timedelta
import binaryninja as bn
from binaryninja import user_directory

# Setup BinAssist logger
try:
    import binaryninja
    log = binaryninja.log.Logger(0, "BinAssist")
except ImportError:
    # Fallback for testing outside Binary Ninja
    class MockLog:
        @staticmethod
        def log_info(msg): print(f"[BinAssist] INFO: {msg}")
        @staticmethod
        def log_error(msg): print(f"[BinAssist] ERROR: {msg}")
        @staticmethod
        def log_warn(msg): print(f"[BinAssist] WARN: {msg}")
    log = MockLog()


class AnalysisDBService:
    """
    Service for persisting binary analysis data, query responses, and chat histories.
    Organized by binary hash for multi-binary support with context caching.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern implementation"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the analysis database service"""
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self._db_lock = threading.RLock()  # Reentrant lock for database operations
        self._db_path = None
        self._connection = None
        
        # Initialize database
        self._init_database()
        self._create_tables()
        self._run_migrations()
    
    def _init_database(self):
        """Initialize SQLite database in Binary Ninja user directory"""
        try:
            user_dir = user_directory()
            binassist_dir = os.path.join(user_dir, 'binassist')
            
            # Create BinAssist directory if it doesn't exist
            os.makedirs(binassist_dir, exist_ok=True)
            
            self._db_path = os.path.join(binassist_dir, 'analysis.db')
            
            # Test connection
            with self._db_lock:
                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.close()
                
            log.log_info(f"AnalysisDB initialized at: {self._db_path}")
                
        except Exception as e:
            raise RuntimeError(f"Failed to initialize analysis database: {e}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-safe database connection"""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        # Enable foreign keys and WAL mode for better performance
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA journal_mode = WAL')
        return conn
    
    def _create_tables(self):
        """Create database schema"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Binary analysis responses
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS BNAnalysis (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        binary_hash TEXT NOT NULL,
                        function_start INTEGER NOT NULL,
                        query_type TEXT NOT NULL,
                        response TEXT NOT NULL,
                        metadata TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(binary_hash, function_start, query_type)
                    )
                ''')
                
                # Binary context cache for performance
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS BNContext (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        binary_hash TEXT NOT NULL,
                        function_start INTEGER NOT NULL,
                        context_data TEXT NOT NULL,
                        expires_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(binary_hash, function_start)
                    )
                ''')
                
                # Chat conversation histories
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS BNChatHistory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        binary_hash TEXT NOT NULL,
                        chat_id TEXT NOT NULL,
                        message_order INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(binary_hash, chat_id, message_order)
                    )
                ''')
                
                # Chat metadata (titles, settings, etc.)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS BNChatMetadata (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        binary_hash TEXT NOT NULL,
                        chat_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(binary_hash, chat_id)
                    )
                ''')
                
                # System prompts versioning
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS SystemPrompts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        prompt TEXT NOT NULL,
                        version TEXT NOT NULL,
                        is_active BOOLEAN DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create indexes for better performance
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_analysis_lookup ON BNAnalysis(binary_hash, function_start, query_type)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_analysis_hash ON BNAnalysis(binary_hash)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_context_lookup ON BNContext(binary_hash, function_start)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_context_expires ON BNContext(expires_at)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_lookup ON BNChatHistory(binary_hash, chat_id, message_order)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_hash ON BNChatHistory(binary_hash)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_metadata_lookup ON BNChatMetadata(binary_hash, chat_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_system_prompt_active ON SystemPrompts(is_active)')
                
                conn.commit()
                log.log_info("AnalysisDB schema created successfully")
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to create database schema: {e}")
            finally:
                conn.close()
    
    def _run_migrations(self):
        """Run database migrations"""
        try:
            from .db_migrations import DatabaseMigrations
            DatabaseMigrations.migrate_analysis_db(self._db_path)
        except Exception as e:
            log.log_warn(f"Migration failed: {e}")
    
    # Binary Hash Generation
    
    def get_binary_hash(self, binary_view: bn.BinaryView) -> str:
        """Generate consistent hash for binary identification"""
        if not binary_view:
            raise ValueError("Binary view is required")
        
        try:
            # Try to use any existing hash from Binary Ninja session data
            if hasattr(binary_view, 'file') and binary_view.file:
                session_data = getattr(binary_view.file, 'session_data', {})
                if isinstance(session_data, dict) and 'hash' in session_data:
                    return session_data['hash']
            
            # Generate hash from binary characteristics
            filename = getattr(binary_view.file, 'filename', 'unknown') if binary_view.file else 'unknown'
            entry_point = binary_view.entry_point or 0
            
            # Get file length using Binary Ninja's length property or end address
            try:
                # Try to get the length of the binary view
                if hasattr(binary_view, 'length'):
                    file_length = binary_view.length
                elif hasattr(binary_view, 'end'):
                    file_length = binary_view.end
                else:
                    # Fallback: get the highest address from segments
                    file_length = 0
                    for segment in binary_view.segments:
                        if segment.end > file_length:
                            file_length = segment.end
            except:
                file_length = 0
            
            # Create a unique identifier
            hash_input = f"{filename}:{entry_point}:{file_length}"
            
            # Add first few bytes if available for additional uniqueness
            try:
                first_bytes = binary_view.read(0, min(64, file_length) if file_length > 0 else 64)
                if first_bytes:
                    hash_input += f":{first_bytes.hex()}"
            except:
                pass
            
            binary_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
            log.log_info(f"Generated binary hash: {binary_hash} for {filename}")
            return binary_hash
            
        except Exception as e:
            log.log_error(f"Failed to generate binary hash: {e}")
            # Ultimate fallback
            import datetime
            return hashlib.sha256(f"fallback:{datetime.datetime.now().isoformat()}".encode()).hexdigest()[:16]
    
    # Function Analysis Operations
    
    def save_function_analysis(self, binary_hash: str, function_start: int, 
                             query_type: str, response: str, metadata: Dict[str, Any] = None) -> int:
        """Save function analysis response"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                metadata_json = json.dumps(metadata) if metadata else None
                
                cursor.execute('''
                    INSERT OR REPLACE INTO BNAnalysis 
                    (binary_hash, function_start, query_type, response, metadata, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (binary_hash, function_start, query_type, response, metadata_json))
                
                analysis_id = cursor.lastrowid
                conn.commit()
                
                log.log_info(f"Saved {query_type} analysis for {binary_hash}:{function_start:x}")
                return analysis_id
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to save function analysis: {e}")
            finally:
                conn.close()
    
    def get_function_analysis(self, binary_hash: str, function_start: int, 
                            query_type: str) -> Optional[Dict[str, Any]]:
        """Get function analysis response"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, response, metadata, created_at, updated_at
                    FROM BNAnalysis 
                    WHERE binary_hash = ? AND function_start = ? AND query_type = ?
                ''', (binary_hash, function_start, query_type))
                
                row = cursor.fetchone()
                if row:
                    metadata = json.loads(row[2]) if row[2] else {}
                    return {
                        'id': row[0],
                        'binary_hash': binary_hash,
                        'function_start': function_start,
                        'query_type': query_type,
                        'response': row[1],
                        'metadata': metadata,
                        'created_at': row[3],
                        'updated_at': row[4]
                    }
                return None
                
            except Exception as e:
                raise RuntimeError(f"Failed to get function analysis: {e}")
            finally:
                conn.close()
    
    def get_function_analyses(self, binary_hash: str, function_start: int) -> List[Dict[str, Any]]:
        """Get all analyses for a specific function"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, query_type, response, metadata, created_at, updated_at
                    FROM BNAnalysis 
                    WHERE binary_hash = ? AND function_start = ?
                    ORDER BY updated_at DESC
                ''', (binary_hash, function_start))
                
                analyses = []
                for row in cursor.fetchall():
                    metadata = json.loads(row[3]) if row[3] else {}
                    analyses.append({
                        'id': row[0],
                        'binary_hash': binary_hash,
                        'function_start': function_start,
                        'query_type': row[1],
                        'response': row[2],
                        'metadata': metadata,
                        'created_at': row[4],
                        'updated_at': row[5]
                    })
                
                return analyses
                
            except Exception as e:
                raise RuntimeError(f"Failed to get function analyses: {e}")
            finally:
                conn.close()
    
    def delete_function_analysis(self, binary_hash: str, function_start: int, 
                               query_type: str) -> bool:
        """Delete a specific function analysis"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    DELETE FROM BNAnalysis 
                    WHERE binary_hash = ? AND function_start = ? AND query_type = ?
                ''', (binary_hash, function_start, query_type))
                
                deleted = cursor.rowcount > 0
                conn.commit()
                
                if deleted:
                    log.log_info(f"Deleted {query_type} analysis for {binary_hash}:{function_start:x}")
                
                return deleted
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to delete function analysis: {e}")
            finally:
                conn.close()
    
    # Context Caching Operations
    
    def save_context_cache(self, binary_hash: str, function_start: int, 
                          context_data: Dict[str, Any], ttl_hours: int = 24) -> int:
        """Save context data to cache with expiration"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                context_json = json.dumps(context_data)
                expires_at = datetime.now() + timedelta(hours=ttl_hours)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO BNContext 
                    (binary_hash, function_start, context_data, expires_at)
                    VALUES (?, ?, ?, ?)
                ''', (binary_hash, function_start, context_json, expires_at.isoformat()))
                
                context_id = cursor.lastrowid
                conn.commit()
                return context_id
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to save context cache: {e}")
            finally:
                conn.close()
    
    def get_context_cache(self, binary_hash: str, function_start: int) -> Optional[Dict[str, Any]]:
        """Get cached context data if not expired"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT context_data, expires_at
                    FROM BNContext 
                    WHERE binary_hash = ? AND function_start = ? 
                    AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                ''', (binary_hash, function_start))
                
                row = cursor.fetchone()
                if row:
                    return json.loads(row[0])
                return None
                
            except Exception as e:
                raise RuntimeError(f"Failed to get context cache: {e}")
            finally:
                conn.close()
    
    def cleanup_expired_context(self) -> int:
        """Remove expired context cache entries"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    DELETE FROM BNContext 
                    WHERE expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP
                ''')
                
                deleted_count = cursor.rowcount
                conn.commit()
                
                if deleted_count > 0:
                    log.log_info(f"Cleaned up {deleted_count} expired context cache entries")
                
                return deleted_count
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to cleanup expired context: {e}")
            finally:
                conn.close()
    
    # Chat History Operations
    
    def save_chat_message(self, binary_hash: str, chat_id: str, role: str, 
                         content: str, metadata: Dict[str, Any] = None) -> int:
        """Save chat message to history"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Get next message order for this chat
                cursor.execute('''
                    SELECT COALESCE(MAX(message_order), -1) + 1
                    FROM BNChatHistory 
                    WHERE binary_hash = ? AND chat_id = ?
                ''', (binary_hash, chat_id))
                
                message_order = cursor.fetchone()[0]
                metadata_json = json.dumps(metadata) if metadata else None
                
                cursor.execute('''
                    INSERT INTO BNChatHistory 
                    (binary_hash, chat_id, message_order, role, content, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (binary_hash, chat_id, message_order, role, content, metadata_json))
                
                message_id = cursor.lastrowid
                conn.commit()
                return message_id
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to save chat message: {e}")
            finally:
                conn.close()
    
    def get_chat_history(self, binary_hash: str, chat_id: str) -> List[Dict[str, Any]]:
        """Get chat message history in order"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, message_order, role, content, metadata, created_at
                    FROM BNChatHistory 
                    WHERE binary_hash = ? AND chat_id = ?
                    ORDER BY message_order ASC
                ''', (binary_hash, chat_id))
                
                messages = []
                for row in cursor.fetchall():
                    metadata = json.loads(row[4]) if row[4] else {}
                    messages.append({
                        'id': row[0],
                        'binary_hash': binary_hash,
                        'chat_id': chat_id,
                        'message_order': row[1],
                        'role': row[2],
                        'content': row[3],
                        'metadata': metadata,
                        'created_at': row[5]
                    })
                
                return messages
                
            except Exception as e:
                raise RuntimeError(f"Failed to get chat history: {e}")
            finally:
                conn.close()
    
    def get_all_chats(self, binary_hash: str) -> List[Dict[str, Any]]:
        """Get all chat summaries for a binary"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Try new native messages table first
                try:
                    cursor.execute('''
                        SELECT chat_id, 
                               COUNT(*) as message_count,
                               MIN(created_at) as first_message,
                               MAX(created_at) as last_message
                        FROM BNChatMessages 
                        WHERE binary_hash = ?
                        GROUP BY chat_id
                        ORDER BY last_message DESC
                    ''', (binary_hash,))
                    
                    chats = []
                    for row in cursor.fetchall():
                        chats.append({
                            'binary_hash': binary_hash,
                            'chat_id': row[0],
                            'message_count': row[1],
                            'first_message': row[2],
                            'last_message': row[3]
                        })
                    
                    return chats
                    
                except sqlite3.OperationalError as e:
                    if "no such table: BNChatMessages" in str(e):
                        # Fall back to old table for backward compatibility
                        cursor.execute('''
                            SELECT chat_id, 
                                   COUNT(*) as message_count,
                                   MIN(created_at) as first_message,
                                   MAX(created_at) as last_message
                            FROM BNChatHistory 
                            WHERE binary_hash = ?
                            GROUP BY chat_id
                            ORDER BY last_message DESC
                        ''', (binary_hash,))
                        
                        chats = []
                        for row in cursor.fetchall():
                            chats.append({
                                'binary_hash': binary_hash,
                                'chat_id': row[0],
                                'message_count': row[1],
                                'first_message': row[2],
                                'last_message': row[3]
                            })
                        
                        return chats
                    else:
                        raise e
                
            except Exception as e:
                raise RuntimeError(f"Failed to get all chats: {e}")
            finally:
                conn.close()
    
    def delete_chat(self, binary_hash: str, chat_id: str) -> bool:
        """Delete entire chat history"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    DELETE FROM BNChatHistory 
                    WHERE binary_hash = ? AND chat_id = ?
                ''', (binary_hash, chat_id))
                
                deleted = cursor.rowcount > 0
                conn.commit()
                
                if deleted:
                    log.log_info(f"Deleted chat {chat_id} for binary {binary_hash}")
                
                return deleted
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to delete chat: {e}")
            finally:
                conn.close()
    
    # Native Message Storage Operations (Provider-Specific Format)
    
    def save_native_message(self, binary_hash: str, chat_id: str, 
                           native_message: Dict[str, Any], provider_type: str,
                           parent_message_id: Optional[int] = None,
                           conversation_thread_id: Optional[str] = None) -> int:
        """Save message in provider's native format"""
        from .message_format_service import get_message_format_service
        from .models.provider_types import ProviderType
        
        try:
            provider_enum = ProviderType(provider_type)
        except ValueError:
            raise ValueError(f"Unsupported provider type: {provider_type}")
        
        # Extract display information from native message
        format_service = get_message_format_service()
        role, content_text, message_type = format_service.extract_display_info(
            native_message, provider_enum
        )
        
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Get next message order for this chat
                cursor.execute('''
                    SELECT COALESCE(MAX(message_order), -1) + 1
                    FROM BNChatMessages 
                    WHERE binary_hash = ? AND chat_id = ?
                ''', (binary_hash, chat_id))
                message_order = cursor.fetchone()[0]
                
                # Insert native message
                cursor.execute('''
                    INSERT INTO BNChatMessages (
                        binary_hash, chat_id, message_order, provider_type,
                        native_message_data, role, content_text, message_type,
                        parent_message_id, conversation_thread_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    binary_hash, chat_id, message_order, provider_type,
                    json.dumps(native_message), role, content_text, message_type,
                    parent_message_id, conversation_thread_id
                ))
                
                message_id = cursor.lastrowid
                conn.commit()
                
                log.log_debug(f"Saved native message {message_id} for {provider_type}: {message_type} message")
                return message_id
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to save native message: {e}")
            finally:
                conn.close()
    
    def get_native_messages(self, binary_hash: str, chat_id: str, 
                          provider_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get native messages for a chat, optionally filtered by provider type"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                if provider_type:
                    cursor.execute('''
                        SELECT id, message_order, provider_type, native_message_data,
                               role, content_text, message_type, parent_message_id,
                               conversation_thread_id, created_at
                        FROM BNChatMessages 
                        WHERE binary_hash = ? AND chat_id = ? AND provider_type = ?
                        ORDER BY message_order ASC
                    ''', (binary_hash, chat_id, provider_type))
                else:
                    cursor.execute('''
                        SELECT id, message_order, provider_type, native_message_data,
                               role, content_text, message_type, parent_message_id,
                               conversation_thread_id, created_at
                        FROM BNChatMessages 
                        WHERE binary_hash = ? AND chat_id = ?
                        ORDER BY message_order ASC
                    ''', (binary_hash, chat_id))
                
                messages = []
                for row in cursor.fetchall():
                    native_data = json.loads(row[3])
                    messages.append({
                        'id': row[0],
                        'message_order': row[1],
                        'provider_type': row[2], 
                        'native_message_data': native_data,
                        'role': row[4],
                        'content_text': row[5],
                        'message_type': row[6],
                        'parent_message_id': row[7],
                        'conversation_thread_id': row[8],
                        'created_at': row[9]
                    })
                
                return messages
                
            except Exception as e:
                raise RuntimeError(f"Failed to get native messages: {e}")
            finally:
                conn.close()
    
    def get_native_messages_for_provider(self, binary_hash: str, chat_id: str, 
                                       provider_type: str) -> List[Dict[str, Any]]:
        """Get messages in exact native format for specific provider (for LLM API calls)"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT native_message_data
                    FROM BNChatMessages 
                    WHERE binary_hash = ? AND chat_id = ? AND provider_type = ?
                    ORDER BY message_order ASC
                ''', (binary_hash, chat_id, provider_type))
                
                native_messages = []
                for row in cursor.fetchall():
                    native_data = json.loads(row[0])
                    native_messages.append(native_data)
                
                return native_messages
                
            except Exception as e:
                raise RuntimeError(f"Failed to get provider-specific messages: {e}")
            finally:
                conn.close()
    
    def get_display_messages(self, binary_hash: str, chat_id: str) -> List[Dict[str, Any]]:
        """Get messages optimized for UI display (properly formatted from native storage)"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, message_order, role, content_text, message_type,
                           provider_type, created_at, native_message_data
                    FROM BNChatMessages 
                    WHERE binary_hash = ? AND chat_id = ?
                    ORDER BY message_order ASC
                ''', (binary_hash, chat_id))
                
                raw_messages = []
                for row in cursor.fetchall():
                    role = row[2]
                    message_type = row[4]
                    provider_type = row[5]
                    native_data = json.loads(row[7]) if row[7] else {}
                    
                    # Skip system messages from display
                    if role == 'system':
                        continue
                    
                    raw_messages.append({
                        'id': row[0],
                        'message_order': row[1],
                        'role': role,
                        'content_text': row[3],
                        'message_type': message_type,
                        'provider_type': provider_type,
                        'created_at': row[6],
                        'native_data': native_data
                    })
                
                # Group and deduplicate messages
                grouped_messages = self._group_and_deduplicate_messages(raw_messages)
                
                # Format for display
                display_messages = []
                for msg in grouped_messages:
                    formatted_content = self._format_message_for_display(
                        msg['role'], msg['content_text'], msg['message_type'], 
                        msg['provider_type'], msg['native_data']
                    )
                    
                    if formatted_content:
                        display_messages.append({
                            'id': msg['id'],
                            'message_order': msg['message_order'],
                            'role': msg['role'],
                            'content': formatted_content,
                            'message_type': msg['message_type'],
                            'provider_type': msg['provider_type'],
                            'created_at': msg['created_at']
                        })
                
                return display_messages
                
            except Exception as e:
                raise RuntimeError(f"Failed to get display messages: {e}")
            finally:
                conn.close()
    
    def _group_and_deduplicate_messages(self, raw_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Group and deduplicate messages to prevent duplicate display"""
        if not raw_messages:
            return []
        
        deduplicated_messages = []
        i = 0
        
        while i < len(raw_messages):
            msg = raw_messages[i]
            role = msg['role']
            
            # For user messages, add them directly (no deduplication needed)
            if role == 'user':
                deduplicated_messages.append(msg)
                i += 1
            
            # For assistant messages, look ahead for duplicates
            elif role == 'assistant':
                # Collect all consecutive assistant messages
                assistant_messages = [msg]
                j = i + 1
                
                while j < len(raw_messages) and raw_messages[j]['role'] == 'assistant':
                    assistant_messages.append(raw_messages[j])
                    j += 1
                
                # Choose the best assistant message from duplicates
                if len(assistant_messages) > 1:
                    best_msg = assistant_messages[0]
                    for other_msg in assistant_messages[1:]:
                        best_msg = self._choose_better_assistant_message(best_msg, other_msg)
                    deduplicated_messages.append(best_msg)
                else:
                    deduplicated_messages.append(msg)
                
                i = j  # Skip all processed assistant messages
            
            # For other message types, add them directly
            else:
                deduplicated_messages.append(msg)
                i += 1
        
        return deduplicated_messages
    
    def _choose_better_assistant_message(self, msg1: Dict[str, Any], msg2: Dict[str, Any]) -> Dict[str, Any]:
        """Choose the better assistant message when deduplicating"""
        # Prefer messages with actual content over tool-only messages
        content1 = msg1.get('content_text', '').strip()
        content2 = msg2.get('content_text', '').strip()
        
        # Prefer message with more substantive content
        if len(content2) > len(content1):
            return msg2
        elif len(content1) > len(content2):
            return msg1
        
        # If similar content length, prefer the later message (more recent)
        if msg2.get('message_order', 0) > msg1.get('message_order', 0):
            return msg2
        
        return msg1
    
    def _format_message_for_display(self, role: str, content_text: str, message_type: str, 
                                   provider_type: str, native_data: dict) -> str:
        """Format a message for proper display in the chat UI"""
        try:
            # Handle user messages - clean up enhanced query text
            if role == 'user':
                return self._clean_user_message_for_display(content_text)
            
            # Handle assistant messages - format based on content type
            elif role == 'assistant':
                return self._format_assistant_message_for_display(content_text, native_data, provider_type)
            
            # Handle tool messages - format as execution summaries
            elif role == 'tool':
                return self._format_tool_message_for_display(content_text, native_data)
            
            # Default: return content as-is
            return content_text
            
        except Exception as e:
            log.log_warn(f"Error formatting message for display: {e}")
            return content_text
    
    def _clean_user_message_for_display(self, content_text: str) -> str:
        """Clean user message by removing MCP/RAG context additions"""
        # Remove MCP context section
        if "Available MCP Tools" in content_text:
            # Split on the MCP tools section and take only the part before it
            parts = content_text.split("Available MCP Tools")
            return parts[0].strip()
        
        # Remove RAG context section  
        if "**RAG Context**" in content_text:
            parts = content_text.split("**RAG Context**")
            return parts[0].strip()
        
        # Remove simple MCP enhancement
        if "Use the available tool calls as needed." in content_text:
            content_text = content_text.replace("\n\nUse the available tool calls as needed.", "")
        
        return content_text.strip()
    
    def _format_assistant_message_for_display(self, content_text: str, native_data: dict, provider_type: str) -> str:
        """Format assistant message for display, handling tool calls properly"""
        # Check if this message has tool calls in the native data
        has_tool_calls = False
        tool_calls_details = []  # Store detailed tool call info
        
        if provider_type == 'anthropic' and 'content' in native_data:
            # Anthropic format: content is a list of blocks
            for block in native_data['content']:
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    has_tool_calls = True
                    tool_name = block.get('name', 'unknown')
                    tool_input = block.get('input', {})
                    if tool_name != 'unknown':
                        tool_calls_details.append({'name': tool_name, 'args': tool_input})
        
        elif provider_type == 'openai' and 'tool_calls' in native_data:
            # OpenAI format: tool_calls is a list
            if native_data['tool_calls']:
                has_tool_calls = True
                for tc in native_data['tool_calls']:
                    if isinstance(tc, dict) and 'function' in tc:
                        tool_name = tc['function'].get('name', 'unknown')
                        tool_args = tc['function'].get('arguments', {})
                        
                        # Parse arguments if they're a JSON string
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except json.JSONDecodeError:
                                tool_args = {'raw_args': tool_args}
                        
                        if tool_name != 'unknown':
                            tool_calls_details.append({'name': tool_name, 'args': tool_args})
        
        elif provider_type == 'ollama':
            # Ollama format: can have tool_calls similar to OpenAI format
            if 'tool_calls' in native_data and native_data['tool_calls']:
                has_tool_calls = True
                for tc in native_data['tool_calls']:
                    if isinstance(tc, dict):
                        # Ollama can have different formats
                        tool_name = 'unknown'
                        tool_args = {}
                        
                        if 'function' in tc:
                            # OpenAI-style format
                            tool_name = tc['function'].get('name', 'unknown')
                            tool_args = tc['function'].get('arguments', {})
                        elif 'name' in tc:
                            # Direct format
                            tool_name = tc.get('name', 'unknown')
                            tool_args = tc.get('arguments', tc.get('parameters', {}))
                        
                        # Parse arguments if they're a JSON string
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except json.JSONDecodeError:
                                tool_args = {'raw_args': tool_args}
                        
                        if tool_name != 'unknown':
                            tool_calls_details.append({'name': tool_name, 'args': tool_args})
            
            # Also check if tool calls are embedded in message content (older Ollama format)
            elif 'message' in native_data and isinstance(native_data['message'], dict):
                message_data = native_data['message']
                if 'tool_calls' in message_data and message_data['tool_calls']:
                    has_tool_calls = True
                    for tc in message_data['tool_calls']:
                        if isinstance(tc, dict):
                            tool_name = 'unknown'
                            tool_args = {}
                            
                            if 'function' in tc:
                                tool_name = tc['function'].get('name', 'unknown')
                                tool_args = tc['function'].get('arguments', {})
                            elif 'name' in tc:
                                tool_name = tc.get('name', 'unknown')
                                tool_args = tc.get('arguments', tc.get('parameters', {}))
                            
                            if isinstance(tool_args, str):
                                try:
                                    tool_args = json.loads(tool_args)
                                except json.JSONDecodeError:
                                    tool_args = {'raw_args': tool_args}
                            
                            if tool_name != 'unknown':
                                tool_calls_details.append({'name': tool_name, 'args': tool_args})
        
        # Clean the content text - remove any existing tool indicators
        clean_content = content_text.strip() if content_text else ""
        if clean_content.startswith('[Tools:') and ']' in clean_content:
            # Remove existing tool indicators from content
            bracket_end = clean_content.find(']')
            if bracket_end != -1:
                clean_content = clean_content[bracket_end + 1:].strip()
                # Remove leading newlines
                while clean_content.startswith('\n'):
                    clean_content = clean_content[1:]
        
        # Format the content with detailed tool information
        if has_tool_calls and tool_calls_details:
            tool_details_text = self._format_tool_calls_with_params(tool_calls_details)
            if clean_content:
                return f"{tool_details_text}\n\n{clean_content}"
            else:
                return tool_details_text
        
        return clean_content
    
    def _format_tool_calls_with_params(self, tool_calls_details: List[Dict[str, Any]]) -> str:
        """Format tool calls with their parameters for display"""
        if not tool_calls_details:
            return ""
        
        if len(tool_calls_details) == 1:
            # Single tool call - more compact format
            tool = tool_calls_details[0]
            name = tool['name']
            args = tool['args']
            
            if not args:
                return f"[Tool: {name}]"
            
            # Format args compactly
            args_str = self._format_args_compact(args)
            return f"[Tool: {name}({args_str})]"
        
        else:
            # Multiple tool calls - detailed format
            lines = ["[Tools called:]"]
            for i, tool in enumerate(tool_calls_details, 1):
                name = tool['name']
                args = tool['args']
                args_str = self._format_args_compact(args) if args else ""
                if args_str:
                    lines.append(f"  {i}. {name}({args_str})")
                else:
                    lines.append(f"  {i}. {name}")
            
            return "\n".join(lines)
    
    def _format_args_compact(self, args: Dict[str, Any]) -> str:
        """Format tool arguments in a compact, readable way"""
        if not args:
            return ""
        
        formatted_args = []
        for key, value in args.items():
            if isinstance(value, str):
                # Truncate long strings
                if len(value) > 30:
                    formatted_args.append(f"{key}=\"{value[:30]}...\"")
                else:
                    formatted_args.append(f"{key}=\"{value}\"")
            elif isinstance(value, (int, float, bool)):
                formatted_args.append(f"{key}={value}")
            elif isinstance(value, (list, dict)):
                # For complex types, show the type and length/size
                if isinstance(value, list):
                    formatted_args.append(f"{key}=[{len(value)} items]")
                else:
                    formatted_args.append(f"{key}={{{len(value)} keys}}")
            else:
                formatted_args.append(f"{key}={str(value)}")
        
        return ", ".join(formatted_args)
    
    def _format_tool_message_for_display(self, content_text: str, native_data: dict) -> str:
        """Format tool execution result for display"""
        if not content_text or not content_text.strip():
            return ""
        
        # Extract tool name and result from content if possible
        clean_content = content_text.strip()
        
        # Try to extract tool name from the native data or content
        tool_name = "Tool"
        if native_data and 'name' in native_data:
            tool_name = native_data['name']
        elif "Tool:" in clean_content:
            # Try to extract from content like "Tool: get_disassembly completed"
            lines = clean_content.split('\n')
            for line in lines:
                if line.strip().startswith("Tool:") or line.strip().startswith("**Tool:**"):
                    # Extract tool name
                    parts = line.split()
                    if len(parts) >= 2:
                        tool_name = parts[1].replace('`', '').replace('*', '')
                    break
        
        # Format the tool result for display
        if len(clean_content) > 200:
            # Truncate very long tool results
            preview = clean_content[:200] + "...\n\n[Tool result truncated for display]"
            return f"**{tool_name} Result:**\n```\n{preview}\n```"
        else:
            return f"**{tool_name} Result:**\n```\n{clean_content}\n```"
    
    def delete_native_chat(self, binary_hash: str, chat_id: str) -> bool:
        """Delete entire native chat history"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    DELETE FROM BNChatMessages 
                    WHERE binary_hash = ? AND chat_id = ?
                ''', (binary_hash, chat_id))
                
                deleted_count = cursor.rowcount
                conn.commit()
                
                if deleted_count > 0:
                    log.log_info(f"Deleted {deleted_count} native messages from chat {chat_id}")
                
                return deleted_count > 0
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to delete native chat: {e}")
            finally:
                conn.close()
    
    # Chat Metadata Operations
    
    def save_chat_metadata(self, binary_hash: str, chat_id: str, name: str) -> int:
        """Save or update chat metadata (name, etc.)"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO BNChatMetadata 
                    (binary_hash, chat_id, name, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ''', (binary_hash, chat_id, name))
                
                metadata_id = cursor.lastrowid
                conn.commit()
                
                log.log_info(f"Saved chat metadata for {binary_hash}:{chat_id} - '{name}'")
                return metadata_id
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to save chat metadata: {e}")
            finally:
                conn.close()
    
    def get_chat_metadata(self, binary_hash: str, chat_id: str) -> Optional[Dict[str, Any]]:
        """Get chat metadata by binary hash and chat ID"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, name, created_at, updated_at
                    FROM BNChatMetadata 
                    WHERE binary_hash = ? AND chat_id = ?
                ''', (binary_hash, chat_id))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0],
                        'binary_hash': binary_hash,
                        'chat_id': chat_id,
                        'name': row[1],
                        'created_at': row[2],
                        'updated_at': row[3]
                    }
                return None
                
            except Exception as e:
                raise RuntimeError(f"Failed to get chat metadata: {e}")
            finally:
                conn.close()
    
    def get_all_chat_metadata(self, binary_hash: str) -> List[Dict[str, Any]]:
        """Get all chat metadata for a binary"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT chat_id, name, created_at, updated_at
                    FROM BNChatMetadata 
                    WHERE binary_hash = ?
                    ORDER BY updated_at DESC
                ''', (binary_hash,))
                
                metadata_list = []
                for row in cursor.fetchall():
                    metadata_list.append({
                        'binary_hash': binary_hash,
                        'chat_id': row[0],
                        'name': row[1],
                        'created_at': row[2],
                        'updated_at': row[3]
                    })
                
                return metadata_list
                
            except Exception as e:
                raise RuntimeError(f"Failed to get all chat metadata: {e}")
            finally:
                conn.close()
    
    def delete_chat_metadata(self, binary_hash: str, chat_id: str) -> bool:
        """Delete chat metadata"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    DELETE FROM BNChatMetadata 
                    WHERE binary_hash = ? AND chat_id = ?
                ''', (binary_hash, chat_id))
                
                deleted = cursor.rowcount > 0
                conn.commit()
                
                if deleted:
                    log.log_info(f"Deleted chat metadata for {binary_hash}:{chat_id}")
                
                return deleted
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to delete chat metadata: {e}")
            finally:
                conn.close()
    
    # System Prompt Operations
    
    def save_system_prompt(self, prompt: str, version: str) -> int:
        """Save system prompt with version"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO SystemPrompts (prompt, version)
                    VALUES (?, ?)
                ''', (prompt, version))
                
                prompt_id = cursor.lastrowid
                conn.commit()
                
                log.log_info(f"Saved system prompt version {version}")
                return prompt_id
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to save system prompt: {e}")
            finally:
                conn.close()
    
    def get_active_system_prompt(self) -> Optional[str]:
        """Get currently active system prompt"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT prompt FROM SystemPrompts 
                    WHERE is_active = 1 
                    ORDER BY created_at DESC 
                    LIMIT 1
                ''', )
                
                row = cursor.fetchone()
                return row[0] if row else None
                
            except Exception as e:
                raise RuntimeError(f"Failed to get active system prompt: {e}")
            finally:
                conn.close()
    
    def set_active_system_prompt(self, version: str) -> bool:
        """Set active system prompt by version"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Deactivate all prompts
                cursor.execute('UPDATE SystemPrompts SET is_active = 0')
                
                # Activate specified version
                cursor.execute('''
                    UPDATE SystemPrompts SET is_active = 1 
                    WHERE version = ?
                ''', (version,))
                
                updated = cursor.rowcount > 0
                conn.commit()
                
                if updated:
                    log.log_info(f"Activated system prompt version {version}")
                
                return updated
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to set active system prompt: {e}")
            finally:
                conn.close()
    
    # Utility Methods
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Get database statistics"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                stats = {}
                
                # Analysis count
                cursor.execute('SELECT COUNT(*) FROM BNAnalysis')
                stats['total_analyses'] = cursor.fetchone()[0]
                
                # Context cache count
                cursor.execute('SELECT COUNT(*) FROM BNContext')
                stats['cached_contexts'] = cursor.fetchone()[0]
                
                # Chat messages count
                cursor.execute('SELECT COUNT(*) FROM BNChatHistory')
                stats['total_chat_messages'] = cursor.fetchone()[0]
                
                # Unique binaries
                cursor.execute('SELECT COUNT(DISTINCT binary_hash) FROM BNAnalysis')
                stats['unique_binaries'] = cursor.fetchone()[0]
                
                # System prompts count
                cursor.execute('SELECT COUNT(*) FROM SystemPrompts')
                stats['system_prompts'] = cursor.fetchone()[0]
                
                return stats
                
            except Exception as e:
                raise RuntimeError(f"Failed to get database stats: {e}")
            finally:
                conn.close()
    
    def cleanup_database(self) -> Dict[str, int]:
        """Clean up expired data and optimize database"""
        try:
            from .db_migrations import DatabaseCleanup
            
            # Clean up expired data
            cleanup_stats = DatabaseCleanup.cleanup_expired_data(self._db_path)
            
            # Also clean up using our internal method
            expired_contexts = self.cleanup_expired_context()
            cleanup_stats["expired_contexts"] += expired_contexts
            
            return cleanup_stats
            
        except Exception as e:
            log.log_error(f"Database cleanup failed: {e}")
            return {"expired_contexts": 0, "old_chat_messages": 0}
    
    def vacuum_database(self) -> bool:
        """Optimize database by running VACUUM"""
        try:
            from .db_migrations import DatabaseCleanup
            return DatabaseCleanup.vacuum_database(self._db_path)
        except Exception as e:
            log.log_error(f"Database vacuum failed: {e}")
            return False
    
    def get_database_info(self) -> Dict[str, Any]:
        """Get comprehensive database information"""
        try:
            from .db_migrations import DatabaseCleanup
            
            # Get size info
            info = DatabaseCleanup.get_database_size_info(self._db_path)
            
            # Add our stats
            info.update(self.get_database_stats())
            
            return info
            
        except Exception as e:
            log.log_error(f"Failed to get database info: {e}")
            return {}
    
    def close(self):
        """Close database connections (for cleanup)"""
        # SQLite connections are closed after each operation
        # This method is here for interface completeness
        pass


# Global instance for easy access throughout the application
analysis_db_service = AnalysisDBService()