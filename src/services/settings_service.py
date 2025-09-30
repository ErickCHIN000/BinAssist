#!/usr/bin/env python3

import sqlite3
import threading
import json
import os
from typing import Any, Dict, List, Optional, Union
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


class SettingsService:
    """
    Thread-safe settings service for BinAssist plugin.
    Provides CRUD API for plugin configuration stored in SQLite database.
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
        """Initialize the settings service"""
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
        self._populate_defaults()
    
    def _init_database(self):
        """Initialize SQLite database in Binary Ninja user directory"""
        try:
            user_dir = user_directory()
            binassist_dir = os.path.join(user_dir, 'binassist')
            
            # Create BinAssist directory if it doesn't exist
            os.makedirs(binassist_dir, exist_ok=True)
            
            self._db_path = os.path.join(binassist_dir, 'settings.db')
            
            # Test connection
            with self._db_lock:
                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.close()
                
        except Exception as e:
            raise RuntimeError(f"Failed to initialize settings database: {e}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-safe database connection"""
        return sqlite3.connect(self._db_path, check_same_thread=False)
    
    def _create_tables(self):
        """Create database schema"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Settings table for key-value pairs
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        type TEXT NOT NULL DEFAULT 'string',
                        category TEXT DEFAULT 'general',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # LLM Providers table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS llm_providers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        model TEXT NOT NULL,
                        url TEXT NOT NULL,
                        max_tokens INTEGER DEFAULT 4096,
                        api_key TEXT,
                        disable_tls BOOLEAN DEFAULT 0,
                        provider_type TEXT DEFAULT 'openai',
                        is_active BOOLEAN DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # MCP Providers table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS mcp_providers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        url TEXT NOT NULL,
                        enabled BOOLEAN DEFAULT 1,
                        transport TEXT DEFAULT 'HTTP',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create indexes for better performance
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_settings_category ON settings(category)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_llm_active ON llm_providers(is_active)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_mcp_enabled ON mcp_providers(enabled)')
                
                conn.commit()
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to create database schema: {e}")
            finally:
                conn.close()
    
    def _run_migrations(self):
        """Run database migrations"""
        try:
            from .migrations import migrate_add_provider_type
            migrate_add_provider_type(self._db_path)
        except Exception as e:
            # Migration failures shouldn't prevent plugin loading
            try:
                log.log_warn(f"Migration failed: {e}")
            except:
                log.log_warn(f"[BinAssist] Warning: Migration failed: {e}")
    
    def _populate_defaults(self):
        """Populate database with default settings"""
        defaults = {
            # System settings
            'system_prompt': {
                'value': 'You are an AI assistant specialized in binary analysis and reverse engineering. Help users understand code, identify patterns, and provide actionable insights for their reverse engineering tasks.',
                'type': 'string',
                'category': 'system'
            },
            
            # Database paths
            'analysis_db_path': {
                'value': os.path.join(user_directory(), 'binassist', 'analysis.db'),
                'type': 'string',
                'category': 'database'
            },
            'rlhf_db_path': {
                'value': os.path.join(user_directory(), 'binassist', 'rlhf.db'),
                'type': 'string',
                'category': 'database'
            },
            'rag_index_path': {
                'value': os.path.join(user_directory(), 'binassist', 'rag_index'),
                'type': 'string',
                'category': 'database'
            },
            
            # UI settings
            'active_llm_provider': {
                'value': '',
                'type': 'string',
                'category': 'ui'
            }
        }
        
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                for key, config in defaults.items():
                    # Only insert if key doesn't exist
                    cursor.execute('SELECT 1 FROM settings WHERE key = ?', (key,))
                    if not cursor.fetchone():
                        cursor.execute('''
                            INSERT INTO settings (key, value, type, category)
                            VALUES (?, ?, ?, ?)
                        ''', (key, config['value'], config['type'], config['category']))
                
                conn.commit()
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to populate default settings: {e}")
            finally:
                conn.close()
    
    # CRUD Operations for Settings
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting value by key"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('SELECT value, type FROM settings WHERE key = ?', (key,))
                result = cursor.fetchone()
                
                if result:
                    value, value_type = result
                    return self._deserialize_value(value, value_type)
                return default
                
            except Exception as e:
                raise RuntimeError(f"Failed to get setting '{key}': {e}")
            finally:
                conn.close()
    
    def set_setting(self, key: str, value: Any, category: str = 'general') -> bool:
        """Set a setting value"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                value_str, value_type = self._serialize_value(value)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO settings (key, value, type, category, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (key, value_str, value_type, category))
                
                conn.commit()
                return True
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to set setting '{key}': {e}")
            finally:
                conn.close()
    
    def delete_setting(self, key: str) -> bool:
        """Delete a setting by key"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM settings WHERE key = ?', (key,))
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to delete setting '{key}': {e}")
            finally:
                conn.close()
    
    def get_settings_by_category(self, category: str) -> Dict[str, Any]:
        """Get all settings in a category"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('SELECT key, value, type FROM settings WHERE category = ?', (category,))
                results = cursor.fetchall()
                
                settings = {}
                for key, value, value_type in results:
                    settings[key] = self._deserialize_value(value, value_type)
                
                return settings
                
            except Exception as e:
                raise RuntimeError(f"Failed to get settings for category '{category}': {e}")
            finally:
                conn.close()
    
    # LLM Provider Operations
    
    def add_llm_provider(self, name: str, model: str, url: str, max_tokens: int = 4096, 
                        api_key: str = '', disable_tls: bool = False, provider_type: str = 'openai') -> int:
        """Add a new LLM provider"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO llm_providers (name, model, url, max_tokens, api_key, disable_tls, provider_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (name, model, url, max_tokens, api_key, disable_tls, provider_type))
                
                provider_id = cursor.lastrowid
                conn.commit()
                return provider_id
                
            except sqlite3.IntegrityError:
                raise ValueError(f"Provider '{name}' already exists")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to add LLM provider: {e}")
            finally:
                conn.close()
    
    def get_llm_providers(self) -> List[Dict[str, Any]]:
        """Get all LLM providers"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, name, model, url, max_tokens, api_key, disable_tls, provider_type, is_active
                    FROM llm_providers ORDER BY name
                ''')
                
                providers = []
                for row in cursor.fetchall():
                    providers.append({
                        'id': row[0],
                        'name': row[1],
                        'model': row[2],
                        'url': row[3],
                        'max_tokens': row[4],
                        'api_key': row[5],
                        'disable_tls': bool(row[6]),
                        'provider_type': row[7],
                        'is_active': bool(row[8])
                    })
                
                return providers
                
            except Exception as e:
                raise RuntimeError(f"Failed to get LLM providers: {e}")
            finally:
                conn.close()
    
    def update_llm_provider(self, provider_id: int, **kwargs) -> bool:
        """Update an LLM provider"""
        valid_fields = {'name', 'model', 'url', 'max_tokens', 'api_key', 'disable_tls', 'provider_type', 'is_active'}
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}
        
        if not update_fields:
            return False
        
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                set_clause = ', '.join([f"{field} = ?" for field in update_fields.keys()])
                values = list(update_fields.values()) + [provider_id]
                
                cursor.execute(f'''
                    UPDATE llm_providers SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', values)
                
                updated = cursor.rowcount > 0
                conn.commit()
                return updated
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to update LLM provider: {e}")
            finally:
                conn.close()
    
    def delete_llm_provider(self, provider_id: int) -> bool:
        """Delete an LLM provider"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM llm_providers WHERE id = ?', (provider_id,))
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to delete LLM provider: {e}")
            finally:
                conn.close()
    
    def set_active_llm_provider(self, provider_name: str) -> bool:
        """Set the active LLM provider"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Deactivate all providers
                cursor.execute('UPDATE llm_providers SET is_active = 0')
                
                # Activate the specified provider
                cursor.execute('UPDATE llm_providers SET is_active = 1 WHERE name = ?', (provider_name,))
                
                # Update the setting in the same transaction
                value_str, value_type = self._serialize_value(provider_name)
                cursor.execute('''
                    INSERT OR REPLACE INTO settings (key, value, type, category, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', ('active_llm_provider', value_str, value_type, 'ui'))
                
                conn.commit()
                return True
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to set active LLM provider: {e}")
            finally:
                conn.close()
    
    def get_active_llm_provider(self) -> Optional[Dict[str, Any]]:
        """Get the currently active LLM provider"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # First check if there's an active provider in the database
                cursor.execute('''
                    SELECT id, name, model, url, max_tokens, api_key, disable_tls, provider_type
                    FROM llm_providers WHERE is_active = 1 LIMIT 1
                ''')
                
                row = cursor.fetchone()
                if row:
                    log.log_debug(f"Found active provider in database: {row[1]}")
                    return {
                        'id': row[0],
                        'name': row[1],
                        'model': row[2],
                        'url': row[3],
                        'max_tokens': row[4],
                        'api_key': row[5],
                        'disable_tls': bool(row[6]),
                        'provider_type': row[7]
                    }
                
                log.log_debug("No active provider in database, checking saved setting")
                
                # If no active provider found, check if we have a saved setting and restore it
                cursor.execute('''
                    SELECT value FROM settings WHERE key = 'active_llm_provider' LIMIT 1
                ''')
                setting_row = cursor.fetchone()
                
                if setting_row:
                    saved_provider_name = setting_row[0]
                    log.log_debug(f"Found saved active provider setting: {saved_provider_name}")
                    
                    # Check if this provider still exists and set it as active
                    cursor.execute('''
                        SELECT id, name, model, url, max_tokens, api_key, disable_tls, provider_type
                        FROM llm_providers WHERE name = ? LIMIT 1
                    ''', (saved_provider_name,))
                    
                    provider_row = cursor.fetchone()
                    if provider_row:
                        log.log_debug(f"Restoring active provider: {saved_provider_name}")
                        # Restore the active flag
                        cursor.execute('UPDATE llm_providers SET is_active = 1 WHERE name = ?', (saved_provider_name,))
                        conn.commit()
                        
                        return {
                            'id': provider_row[0],
                            'name': provider_row[1],
                            'model': provider_row[2],
                            'url': provider_row[3],
                            'max_tokens': provider_row[4],
                            'api_key': provider_row[5],
                            'disable_tls': bool(provider_row[6]),
                            'provider_type': provider_row[7]
                        }
                    else:
                        log.log_warn(f"Saved provider '{saved_provider_name}' no longer exists in database")
                else:
                    log.log_debug("No saved active provider setting found")
                
                return None
                
            except Exception as e:
                raise RuntimeError(f"Failed to get active LLM provider: {e}")
            finally:
                conn.close()
    
    # MCP Provider Operations (similar pattern)
    
    def add_mcp_provider(self, name: str, url: str, enabled: bool = True, transport: str = 'HTTP') -> int:
        """Add a new MCP provider"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO mcp_providers (name, url, enabled, transport)
                    VALUES (?, ?, ?, ?)
                ''', (name, url, enabled, transport))
                
                provider_id = cursor.lastrowid
                conn.commit()
                return provider_id
                
            except sqlite3.IntegrityError:
                raise ValueError(f"MCP Provider '{name}' already exists")
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to add MCP provider: {e}")
            finally:
                conn.close()
    
    def get_mcp_providers(self) -> List[Dict[str, Any]]:
        """Get all MCP providers"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, name, url, enabled, transport
                    FROM mcp_providers ORDER BY name
                ''')
                
                providers = []
                for row in cursor.fetchall():
                    providers.append({
                        'id': row[0],
                        'name': row[1],
                        'url': row[2],
                        'enabled': bool(row[3]),
                        'transport': row[4]
                    })
                
                return providers
                
            except Exception as e:
                raise RuntimeError(f"Failed to get MCP providers: {e}")
            finally:
                conn.close()
    
    def update_mcp_provider(self, provider_id: int, **kwargs) -> bool:
        """Update an MCP provider"""
        valid_fields = {'name', 'url', 'enabled', 'transport'}
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}
        
        if not update_fields:
            return False
        
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                set_clause = ', '.join([f"{field} = ?" for field in update_fields.keys()])
                values = list(update_fields.values()) + [provider_id]
                
                cursor.execute(f'''
                    UPDATE mcp_providers SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', values)
                
                updated = cursor.rowcount > 0
                conn.commit()
                return updated
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to update MCP provider: {e}")
            finally:
                conn.close()
    
    def delete_mcp_provider(self, provider_id: int) -> bool:
        """Delete an MCP provider"""
        with self._db_lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM mcp_providers WHERE id = ?', (provider_id,))
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
                
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Failed to delete MCP provider: {e}")
            finally:
                conn.close()
    
    # Utility methods
    
    def _serialize_value(self, value: Any) -> tuple[str, str]:
        """Serialize a value for database storage"""
        if isinstance(value, bool):
            return str(int(value)), 'boolean'
        elif isinstance(value, int):
            return str(value), 'integer'
        elif isinstance(value, float):
            return str(value), 'float'
        elif isinstance(value, (dict, list)):
            return json.dumps(value), 'json'
        else:
            return str(value), 'string'
    
    def _deserialize_value(self, value: str, value_type: str) -> Any:
        """Deserialize a value from database storage"""
        try:
            if value_type == 'boolean':
                return bool(int(value))
            elif value_type == 'integer':
                return int(value)
            elif value_type == 'float':
                return float(value)
            elif value_type == 'json':
                return json.loads(value)
            else:
                return value
        except (ValueError, json.JSONDecodeError):
            return value  # Return as string if deserialization fails
    
    # System Prompt Management
    
    def get_system_prompt(self) -> str:
        """Get current system prompt (delegates to AnalysisDB)"""
        try:
            from .analysis_db_service import analysis_db_service
            active_prompt = analysis_db_service.get_active_system_prompt()
            if active_prompt:
                return active_prompt
            
            # Fallback to settings table
            return self.get_setting('system_prompt', 
                'You are an AI assistant specialized in binary analysis and reverse engineering. Help users understand code, identify patterns, and provide actionable insights for their reverse engineering tasks.')
        except Exception as e:
            log.log_error(f"Failed to get system prompt: {e}")
            return 'You are an AI assistant specialized in binary analysis and reverse engineering.'
    
    def save_system_prompt(self, prompt: str, version: str = None) -> bool:
        """Save system prompt (delegates to AnalysisDB)"""
        try:
            from .analysis_db_service import analysis_db_service
            import datetime
            
            if not version:
                version = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Save to AnalysisDB
            prompt_id = analysis_db_service.save_system_prompt(prompt, version)
            
            # Set as active
            analysis_db_service.set_active_system_prompt(version)
            
            # Also update settings for backward compatibility
            self.set_setting('system_prompt', prompt, 'system')
            
            log.log_info(f"Saved system prompt version {version}")
            return True
            
        except Exception as e:
            log.log_error(f"Failed to save system prompt: {e}")
            return False
    
    def close(self):
        """Close database connections (for cleanup)"""
        # SQLite connections are closed after each operation
        # This method is here for interface completeness
        pass


# Global instance for easy access throughout the application
settings_service = SettingsService()
