#!/usr/bin/env python3

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                              QTableWidget, QTableWidgetItem, QComboBox, QTextEdit,
                              QLineEdit, QCheckBox, QHeaderView, QAbstractItemView,
                              QGroupBox, QScrollArea, QFileDialog)
from PySide6.QtCore import Signal, Qt


class SettingsTabView(QWidget):
    # Signals for controller communication
    llm_provider_add_requested = Signal()
    llm_provider_edit_requested = Signal(int)  # row index
    llm_provider_delete_requested = Signal(int)  # row index
    llm_provider_test_requested = Signal(int)  # row index
    llm_active_provider_changed = Signal(str)  # provider name
    
    mcp_provider_add_requested = Signal()
    mcp_provider_edit_requested = Signal(int)  # row index
    mcp_provider_delete_requested = Signal(int)  # row index
    mcp_provider_test_requested = Signal(int)  # row index
    
    system_prompt_changed = Signal(str)
    database_path_changed = Signal(str, str)  # path_type, path_value
    
    def __init__(self):
        super().__init__()
        self.setup_ui()
    
    def setup_ui(self):
        # Create scroll area for the settings content
        scroll_area = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_layout.setContentsMargins(5, 5, 5, 5)
        scroll_layout.setSpacing(10)
        
        # Create all sections
        self.create_llm_provider_section(scroll_layout)
        self.create_mcp_provider_section(scroll_layout)
        self.create_system_prompt_section(scroll_layout)
        self.create_database_paths_section(scroll_layout)
        
        # Add stretch to push everything to the top
        scroll_layout.addStretch()
        
        scroll_widget.setLayout(scroll_layout)
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)
    
    def create_llm_provider_section(self, parent_layout):
        group_box = QGroupBox("LLM Providers")
        layout = QVBoxLayout()
        
        # LLM Providers table
        self.llm_table = QTableWidget()
        self.llm_table.setColumnCount(7)
        self.llm_table.setHorizontalHeaderLabels([
            "Name", "Model", "Type", "URL", "Max Tokens", "Key", "Disable TLS"
        ])
        
        # Configure table
        self.llm_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.llm_table.horizontalHeader()
        for i in range(7):
            header.setSectionResizeMode(i, QHeaderView.Interactive)
        
        # Set initial column widths
        self.llm_table.setColumnWidth(0, 120)  # Name
        self.llm_table.setColumnWidth(1, 120)  # Model
        self.llm_table.setColumnWidth(2, 100)  # Type
        self.llm_table.setColumnWidth(3, 200)  # URL
        self.llm_table.setColumnWidth(4, 80)   # Max Tokens
        self.llm_table.setColumnWidth(5, 120)  # Key
        self.llm_table.setColumnWidth(6, 80)   # Disable TLS
        
        layout.addWidget(self.llm_table)
        
        # LLM Provider buttons
        llm_buttons_layout = QHBoxLayout()
        self.llm_add_button = QPushButton("Add")
        self.llm_edit_button = QPushButton("Edit")
        self.llm_delete_button = QPushButton("Delete")
        self.llm_test_button = QPushButton("Test")
        
        self.llm_add_button.clicked.connect(self.llm_provider_add_requested.emit)
        self.llm_edit_button.clicked.connect(self.on_llm_edit_clicked)
        self.llm_delete_button.clicked.connect(self.on_llm_delete_clicked)
        self.llm_test_button.clicked.connect(self.on_llm_test_clicked)
        
        llm_buttons_layout.addWidget(self.llm_add_button)
        llm_buttons_layout.addWidget(self.llm_edit_button)
        llm_buttons_layout.addWidget(self.llm_delete_button)
        llm_buttons_layout.addWidget(self.llm_test_button)
        llm_buttons_layout.addStretch()
        
        layout.addLayout(llm_buttons_layout)
        
        # Active Provider section
        active_layout = QHBoxLayout()
        active_layout.addWidget(QLabel("Active Provider:"))
        
        self.active_provider_combo = QComboBox()
        self.active_provider_combo.currentTextChanged.connect(self.llm_active_provider_changed.emit)
        active_layout.addWidget(self.active_provider_combo)
        
        active_layout.addStretch()
        layout.addLayout(active_layout)
        
        group_box.setLayout(layout)
        parent_layout.addWidget(group_box)
    
    def create_mcp_provider_section(self, parent_layout):
        group_box = QGroupBox("MCP Providers")
        layout = QVBoxLayout()
        
        # MCP Providers table
        self.mcp_table = QTableWidget()
        self.mcp_table.setColumnCount(4)
        self.mcp_table.setHorizontalHeaderLabels([
            "Name", "URL", "Enabled", "Transport"
        ])
        
        # Configure table
        self.mcp_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.mcp_table.horizontalHeader()
        for i in range(4):
            header.setSectionResizeMode(i, QHeaderView.Interactive)
        
        # Set initial column widths
        self.mcp_table.setColumnWidth(0, 120)  # Name
        self.mcp_table.setColumnWidth(1, 200)  # URL
        self.mcp_table.setColumnWidth(2, 80)   # Enabled
        self.mcp_table.setColumnWidth(3, 100)  # Transport
        
        layout.addWidget(self.mcp_table)
        
        # MCP Provider buttons
        mcp_buttons_layout = QHBoxLayout()
        self.mcp_add_button = QPushButton("Add")
        self.mcp_edit_button = QPushButton("Edit")
        self.mcp_delete_button = QPushButton("Delete")
        self.mcp_test_button = QPushButton("Test")
        
        self.mcp_add_button.clicked.connect(self.mcp_provider_add_requested.emit)
        self.mcp_edit_button.clicked.connect(self.on_mcp_edit_clicked)
        self.mcp_delete_button.clicked.connect(self.on_mcp_delete_clicked)
        self.mcp_test_button.clicked.connect(self.on_mcp_test_clicked)
        
        mcp_buttons_layout.addWidget(self.mcp_add_button)
        mcp_buttons_layout.addWidget(self.mcp_edit_button)
        mcp_buttons_layout.addWidget(self.mcp_delete_button)
        mcp_buttons_layout.addWidget(self.mcp_test_button)
        mcp_buttons_layout.addStretch()
        
        layout.addLayout(mcp_buttons_layout)
        
        group_box.setLayout(layout)
        parent_layout.addWidget(group_box)
    
    def create_system_prompt_section(self, parent_layout):
        group_box = QGroupBox("System Prompt")
        layout = QVBoxLayout()
        
        self.system_prompt_text = QTextEdit()
        self.system_prompt_text.setPlainText("You are an AI assistant specialized in binary analysis and reverse engineering...")
        self.system_prompt_text.setMaximumHeight(120)  # Limit height to keep it compact
        self.system_prompt_text.textChanged.connect(self.on_system_prompt_changed)
        
        layout.addWidget(self.system_prompt_text)
        
        group_box.setLayout(layout)
        parent_layout.addWidget(group_box)
    
    def create_database_paths_section(self, parent_layout):
        group_box = QGroupBox("Database Paths")
        layout = QVBoxLayout()
        
        # Analysis DB path
        analysis_layout = QHBoxLayout()
        analysis_layout.addWidget(QLabel("Analysis DB:"))
        self.analysis_db_path = QLineEdit("/path/to/analysis.db")
        self.analysis_db_button = QPushButton("Browse...")
        self.analysis_db_button.clicked.connect(lambda: self.browse_database_path("analysis_db"))
        
        analysis_layout.addWidget(self.analysis_db_path)
        analysis_layout.addWidget(self.analysis_db_button)
        layout.addLayout(analysis_layout)
        
        # RLHF DB path
        rlhf_layout = QHBoxLayout()
        rlhf_layout.addWidget(QLabel("RLHF DB:"))
        self.rlhf_db_path = QLineEdit("/path/to/rlhf.db")
        self.rlhf_db_button = QPushButton("Browse...")
        self.rlhf_db_button.clicked.connect(lambda: self.browse_database_path("rlhf_db"))
        
        rlhf_layout.addWidget(self.rlhf_db_path)
        rlhf_layout.addWidget(self.rlhf_db_button)
        layout.addLayout(rlhf_layout)
        
        # RAG Index path
        rag_layout = QHBoxLayout()
        rag_layout.addWidget(QLabel("RAG Index:"))
        self.rag_index_path = QLineEdit("/path/to/rag_index")
        self.rag_index_button = QPushButton("Browse...")
        self.rag_index_button.clicked.connect(lambda: self.browse_database_path("rag_index"))
        
        rag_layout.addWidget(self.rag_index_path)
        rag_layout.addWidget(self.rag_index_button)
        layout.addLayout(rag_layout)
        
        group_box.setLayout(layout)
        parent_layout.addWidget(group_box)
    
    def add_llm_provider(self, name, model, provider_type, url, max_tokens, key, disable_tls=False):
        """Add an LLM provider to the table"""
        row_count = self.llm_table.rowCount()
        self.llm_table.insertRow(row_count)
        
        self.llm_table.setItem(row_count, 0, QTableWidgetItem(name))
        self.llm_table.setItem(row_count, 1, QTableWidgetItem(model))
        self.llm_table.setItem(row_count, 2, QTableWidgetItem(provider_type))
        self.llm_table.setItem(row_count, 3, QTableWidgetItem(url))
        self.llm_table.setItem(row_count, 4, QTableWidgetItem(str(max_tokens)))
        
        # Redact API key for security (use dots like the modal dialog)
        if key and len(key) > 0:
            masked_key = "•" * min(len(key), 20)  # Limit to 20 dots for readability
        else:
            masked_key = ""
        self.llm_table.setItem(row_count, 5, QTableWidgetItem(masked_key))
        
        # Add checkbox for disable TLS
        tls_checkbox = QCheckBox()
        tls_checkbox.setChecked(disable_tls)
        self.llm_table.setCellWidget(row_count, 6, tls_checkbox)
        
        # Update active provider combo
        self.active_provider_combo.addItem(name)
    
    def add_mcp_provider(self, name, url, enabled=True, transport="HTTP"):
        """Add an MCP provider to the table"""
        row_count = self.mcp_table.rowCount()
        self.mcp_table.insertRow(row_count)
        
        self.mcp_table.setItem(row_count, 0, QTableWidgetItem(name))
        self.mcp_table.setItem(row_count, 1, QTableWidgetItem(url))
        
        # Add checkbox for enabled
        enabled_checkbox = QCheckBox()
        enabled_checkbox.setChecked(enabled)
        self.mcp_table.setCellWidget(row_count, 2, enabled_checkbox)
        
        self.mcp_table.setItem(row_count, 3, QTableWidgetItem(transport))
    
    def on_llm_edit_clicked(self):
        current_row = self.llm_table.currentRow()
        if current_row >= 0:
            self.llm_provider_edit_requested.emit(current_row)
    
    def on_llm_delete_clicked(self):
        current_row = self.llm_table.currentRow()
        if current_row >= 0:
            self.llm_provider_delete_requested.emit(current_row)
    
    def on_llm_test_clicked(self):
        current_row = self.llm_table.currentRow()
        if current_row >= 0:
            self.llm_provider_test_requested.emit(current_row)
    
    def on_mcp_edit_clicked(self):
        current_row = self.mcp_table.currentRow()
        if current_row >= 0:
            self.mcp_provider_edit_requested.emit(current_row)
    
    def on_mcp_delete_clicked(self):
        current_row = self.mcp_table.currentRow()
        if current_row >= 0:
            self.mcp_provider_delete_requested.emit(current_row)
    
    def on_mcp_test_clicked(self):
        current_row = self.mcp_table.currentRow()
        if current_row >= 0:
            self.mcp_provider_test_requested.emit(current_row)
    
    def on_system_prompt_changed(self):
        self.system_prompt_changed.emit(self.system_prompt_text.toPlainText())
    
    def browse_database_path(self, path_type):
        """Open file dialog to browse for database path"""
        if path_type == "rag_index":
            # For RAG index, use directory dialog
            path = QFileDialog.getExistingDirectory(self, "Select RAG Index Directory")
        else:
            # For database files, use file dialog
            path, _ = QFileDialog.getOpenFileName(self, f"Select {path_type.replace('_', ' ').title()}", "", "Database files (*.db);;All files (*)")
        
        if path:
            if path_type == "analysis_db":
                self.analysis_db_path.setText(path)
            elif path_type == "rlhf_db":
                self.rlhf_db_path.setText(path)
            elif path_type == "rag_index":
                self.rag_index_path.setText(path)
            
            self.database_path_changed.emit(path_type, path)
    
