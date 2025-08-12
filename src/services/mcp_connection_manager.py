#!/usr/bin/env python3
"""
MCP Connection Manager

Provides a clean interface for managing MCP connections with lazy initialization,
connection pooling, and tool discovery caching. Integrates with the Query Controller
to provide tools when the MCP checkbox is enabled.
"""

import asyncio
import threading
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from .mcp_client_service import MCPClientService
from .models.mcp_models import MCPTool

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
        @staticmethod
        def log_debug(msg): print(f"[BinAssist] DEBUG: {msg}")
    log = MockLog()


@dataclass
class ConnectionState:
    """Track connection state and cached tools"""
    connected: bool = False
    last_connection_attempt: float = 0.0
    tools: List[Dict[str, Any]] = None
    tools_cached_at: float = 0.0
    error_message: Optional[str] = None
    
    def __post_init__(self):
        if self.tools is None:
            self.tools = []


class MCPConnectionManager:
    """
    Manages MCP connections with lazy initialization and caching.
    
    Provides a clean interface for the Query Controller to:
    - Check if MCP tools are available
    - Get tool definitions for LLM requests
    - Ensure connections are established when needed
    """
    
    def __init__(self):
        self._mcp_service = MCPClientService()
        self._state = ConnectionState()
        self._lock = threading.RLock()
        self._tools_cache_ttl = 60.0  # 60 seconds
        self._connection_retry_delay = 30.0  # 30 seconds
        
    
    def is_available(self) -> bool:
        """
        Check if MCP connections are available.
        
        Returns:
            True if at least one MCP server is connected
        """
        with self._lock:
            return self._state.connected
    
    def get_available_tools_for_llm(self) -> List[Dict[str, Any]]:
        """
        Get MCP tools formatted for LLM consumption.
        
        Returns:
            List of tool definitions in OpenAI tool calling format.
            Returns empty list if not connected or no tools available.
        """
        with self._lock:
            # Check if we have cached tools that are still fresh
            current_time = time.time()
            cache_age = current_time - self._state.tools_cached_at
            
            if self._state.connected and cache_age < self._tools_cache_ttl:
                return self._state.tools.copy()
            
            # Cache is stale or we're not connected
            if not self._state.connected:
                return []
            
            # Refresh tools cache
            try:
                # Get raw MCP tools and convert to LLM format
                raw_tools = self._mcp_service.get_available_tools()
                tools = self._convert_tools_for_llm(raw_tools)
                self._state.tools = tools
                self._state.tools_cached_at = current_time
                log.log_info(f"Refreshed MCP tools cache: {len(tools)} tools available")
                return tools.copy()
                
            except Exception as e:
                log.log_error(f"Failed to refresh MCP tools cache: {e}")
                self._state.error_message = str(e)
                return []
    
    def ensure_connections(self) -> bool:
        """
        Ensure MCP connections are established.
        
        This method performs lazy initialization - connections are only
        established when actually needed (when MCP checkbox is enabled).
        
        Returns:
            True if connections are established successfully
        """
        with self._lock:
            current_time = time.time()
            
            # If already connected, return success
            if self._state.connected:
                return True
            
            # Check if we should retry connection (rate limiting)
            time_since_last_attempt = current_time - self._state.last_connection_attempt
            if time_since_last_attempt < self._connection_retry_delay:
                return False
            
            # Attempt to establish connections
            self._state.last_connection_attempt = current_time
            
            try:
                log.log_info("Initializing MCP connections...")
                
                # Load configuration and initialize connections
                if not self._mcp_service.load_configuration():
                    self._state.error_message = "Failed to load MCP configuration"
                    log.log_warn("No MCP configuration found")
                    return False
                
                # Initialize connections in background
                success = self._mcp_service.initialize_connections()
                
                if success:
                    self._state.connected = True
                    self._state.error_message = None
                    self._state.tools = []  # Reset cache
                    self._state.tools_cached_at = 0.0
                    log.log_info("MCP connections established successfully")
                    return True
                else:
                    self._state.error_message = "Failed to initialize MCP connections"
                    log.log_warn("MCP connection initialization failed")
                    return False
                    
            except Exception as e:
                self._state.error_message = str(e)
                log.log_error(f"Exception during MCP connection: {e}")
                return False
    
    def get_connection_status(self) -> Dict[str, Any]:
        """
        Get detailed connection status for UI display.
        
        Returns:
            Dictionary with connection status information
        """
        with self._lock:
            status = {
                "connected": self._state.connected,
                "tools_count": len(self._state.tools),
                "last_attempt": self._state.last_connection_attempt,
                "cache_age": time.time() - self._state.tools_cached_at if self._state.tools_cached_at > 0 else 0,
                "error": self._state.error_message
            }
            
            if self._state.connected:
                # Get detailed server status
                server_statuses = self._mcp_service.get_all_connection_statuses()
                status["servers"] = server_statuses
            
            return status
    
    def _convert_tools_for_llm(self, mcp_tools: List) -> List[Dict[str, Any]]:
        """Convert MCP tools to OpenAI tool calling format"""
        llm_tools = []
        
        for tool in mcp_tools:
            try:
                # Base tool definition
                tool_def = {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or f"Tool from {tool.server_name}",
                    }
                }
                
                # Add schema if available
                if tool.schema and isinstance(tool.schema, dict):
                    # Convert MCP schema to OpenAI function calling schema
                    if "properties" in tool.schema:
                        tool_def["function"]["parameters"] = {
                            "type": "object",
                            "properties": tool.schema["properties"],
                            "required": tool.schema.get("required", [])
                        }
                    else:
                        # If no proper schema, create minimal one
                        tool_def["function"]["parameters"] = {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                else:
                    # Default schema for tools without defined parameters
                    tool_def["function"]["parameters"] = {
                        "type": "object", 
                        "properties": {},
                        "required": []
                    }
                
                llm_tools.append(tool_def)
                
            except Exception as e:
                log.log_error(f"Error converting tool {getattr(tool, 'name', 'unknown')} to LLM format: {e}")
                continue
        
        return llm_tools
    
    def disconnect(self):
        """
        Disconnect from all MCP servers and clean up resources.
        """
        with self._lock:
            if self._state.connected:
                try:
                    log.log_info("Disconnecting from MCP servers...")
                    self._mcp_service.shutdown()
                    self._state.connected = False
                    self._state.tools = []
                    self._state.tools_cached_at = 0.0
                    self._state.error_message = None
                    log.log_info("MCP connections closed")
                except Exception as e:
                    log.log_error(f"Error during MCP disconnect: {e}")
    
    def force_reconnect(self):
        """
        Force a reconnection attempt, bypassing retry delays.
        """
        with self._lock:
            log.log_info("Forcing MCP reconnection...")
            self._state.connected = False
            self._state.last_connection_attempt = 0.0
            self._state.tools = []
            self._state.tools_cached_at = 0.0
            self._state.error_message = None
            
            # Shutdown existing connections
            try:
                self._mcp_service.shutdown()
            except Exception as e:
                pass  # Ignore shutdown errors
            
            # Attempt new connection
            return self.ensure_connections()
    
    def get_tool_count(self) -> int:
        """
        Get the number of available MCP tools.
        
        Returns:
            Number of tools available, 0 if not connected
        """
        with self._lock:
            return len(self._state.tools) if self._state.connected else 0
    
    def __del__(self):
        """Cleanup on destruction"""
        try:
            self.disconnect()
        except:
            pass  # Ignore cleanup errors