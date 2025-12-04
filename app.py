#!/usr/bin/env python3

import sys
import json
import sqlite3
import time
import threading

import os
from dotenv import load_dotenv

# Load environment variables from workspace root (.env located at project root)
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(WORKSPACE_ROOT, ".env"))

import paho.mqtt.client as mqtt
from PyQt5.QtCore import pyqtSignal, pyqtSlot, Qt, QPoint as _QPoint, QSize as _QSize, QRect as _QRect, QRectF as _QRectF, QMargins, QTimer
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QListWidget, QListWidgetItem, QWidget, QVBoxLayout, QLabel, 
    QHBoxLayout, QPushButton, QFrame, QGridLayout, QScrollArea, QSplitter
)
from PyQt5.QtChart import QChart, QChartView, QLineSeries, QValueAxis
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtGui import QPainter as _QPainter, QFont as _QFont, QPen as _QPen, QIcon, QColor, QBrush

# Monkey-patch QPoint to handle float arguments (fix for AnalogGaugeWidget compatibility)
class QPoint(_QPoint):
    def __new__(cls, *args):
        if len(args) == 2:
            # Convert float arguments to int
            return _QPoint(int(args[0]), int(args[1]))
        return _QPoint(*args)

# Monkey-patch QSize to handle float arguments (fix for AnalogGaugeWidget compatibility)
class QSize(_QSize):
    def __new__(cls, *args):
        if len(args) == 2:
            # Convert float arguments to int
            return _QSize(int(args[0]), int(args[1]))
        return _QSize(*args)

# Monkey-patch QRect to handle float arguments
class QRect(_QRect):
    def __init__(self, *args):
        if len(args) == 4:
            # QRect(x, y, width, height) - convert floats to ints
            super().__init__(int(args[0]), int(args[1]), int(args[2]), int(args[3]))
        elif len(args) == 2:
            # QRect(QPoint, QSize)
            super().__init__(args[0], args[1])
        else:
            super().__init__(*args)

# Monkey-patch QRectF to ensure compatibility
class QRectF(_QRectF):
    pass

# Monkey-patch QFont to handle float arguments
class QFont(_QFont):
    def __init__(self, *args, **kwargs):
        if len(args) >= 2 and isinstance(args[1], float):
            # QFont(family, pointSize, ...) - convert float pointSize to int
            args = list(args)
            args[1] = int(args[1])
        super().__init__(*args, **kwargs)

# Monkey-patch QPen to handle float arguments
class QPen(_QPen):
    def setWidth(self, width):
        """Override setWidth to handle float values"""
        return super().setWidth(int(width) if isinstance(width, float) else width)

# Monkey-patch QPainter methods to handle float arguments
class QPainter(_QPainter):
    def drawLine(self, *args):
        """Override drawLine to handle float coordinates"""
        if len(args) == 4:
            # drawLine(x1, y1, x2, y2) - convert floats to ints
            return super().drawLine(int(args[0]), int(args[1]), int(args[2]), int(args[3]))
        return super().drawLine(*args)
    
    def drawEllipse(self, *args):
        """Override drawEllipse to handle float coordinates"""
        if len(args) == 4:
            # drawEllipse(x, y, width, height) - convert floats to ints
            return super().drawEllipse(int(args[0]), int(args[1]), int(args[2]), int(args[3]))
        return super().drawEllipse(*args)
    
    def drawArc(self, *args):
        """Override drawArc to handle float coordinates"""
        if len(args) == 6:
            # drawArc(x, y, width, height, startAngle, spanAngle) - convert floats to ints
            return super().drawArc(int(args[0]), int(args[1]), int(args[2]), int(args[3]), int(args[4]), int(args[5]))
        return super().drawArc(*args)
    
    def drawPolygon(self, *args):
        """Override drawPolygon to handle various argument types"""
        return super().drawPolygon(*args)
    
    def drawText(self, *args):
        """Override drawText to handle float coordinates"""
        if len(args) >= 3 and isinstance(args[0], (int, float)) and isinstance(args[1], (int, float)):
            # drawText(x, y, text, ...) - convert floats to ints
            new_args = [int(args[0]), int(args[1])] + list(args[2:])
            return super().drawText(*new_args)
        return super().drawText(*args)

# Replace all patched classes in PyQt5 modules before importing AnalogGaugeWidget
import PyQt5.QtCore
import PyQt5.QtGui
PyQt5.QtCore.QPoint = QPoint
PyQt5.QtCore.QSize = QSize
PyQt5.QtCore.QRect = QRect
PyQt5.QtCore.QRectF = QRectF
PyQt5.QtGui.QPainter = QPainter
PyQt5.QtGui.QFont = QFont
PyQt5.QtGui.QPen = QPen

from Custom_Widgets.AnalogGaugeWidget import AnalogGaugeWidget
from services.rackControlService import Rack, RackControlService, DoorStatus, VentilationStatus, BuzzerStatus
from services.toolCallingService import ToolCallingService
from services.forecastService import ForecastService

class MainWindow(QMainWindow):
    message_received = pyqtSignal(dict)
    action_executed = pyqtSignal(str, str)  # rackId, action - signal for AI actions
    status_updated = pyqtSignal(str, str, str)  # rackId, action, reason - signal for status bar

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dashboard Rack Inteligente - EmbarcaTech")
        
        # Fullscreen mode
        self.showMaximized()
        
        # Current selected rack
        self.current_rack_id = None
        self.currentRack: Rack = None

        # Rack objects dictionary (rackId -> Rack instance)
        self.racks: dict[str, Rack] = {}
        
        # Rack control service (initialized after MQTT setup)
        self.rackControlService: RackControlService = None
        
        # Tool calling service for AI-driven control (initialized after MQTT setup)
        self.toolCallingService: ToolCallingService = None
        
        # Forecast service for time series prediction with SARIMA fallback
        self.forecastService: ForecastService = None

        # Rack states cache
        self.rack_states = {}

        self.base_topic = os.getenv("MQTT_BASE_TOPIC", "racks").rstrip("/")

        # Historical data configuration
        # Armazena dados para 7 dias de histórico (coleta a cada segundo)
        self.history_limit = int(os.getenv("FORECAST_CONTEXT_LENGTH", "168")) * 3600  # 168h * 3600s = 7 dias
        self.history_interval_seconds = 1
        # Horizonte de previsão: 24 horas
        self.forecast_horizon = int(os.getenv("FORECAST_HORIZON", "24"))
        self.temp_series = None
        self.temp_axis_x = None
        self.temp_axis_y = None
        self.temp_forecast_series = None
        self.hum_series = None
        self.hum_axis_x = None
        self.hum_axis_y = None
        self.hum_forecast_series = None

        # Setup UI
        self.setup_ui()

        # Connect signal to update UI from MQTT messages in the GUI thread
        self.message_received.connect(self.handle_message_update)

        # Banco SQLite
        self.conn = sqlite3.connect("data.db", check_same_thread=False)
        self.db_lock = threading.Lock()
        self.execute_db(
            """
            CREATE TABLE IF NOT EXISTS rack_data (
                id TEXT,
                latitude REAL,
                longitude REAL,
                temperature REAL,
                humidity REAL,
                door_status INTEGER,
                ventilation_status INTEGER,
                buzzer_status INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        , commit=True)
        # Carrega racks existentes do DB
        racks = self.execute_db("SELECT DISTINCT id FROM rack_data", fetchall=True)
        for (rid,) in racks:
            self.list_widget.addItem(f"Rack {str(rid)}")

        # MQTT
        self.setup_mqtt()
        
        # Initialize rack control service with MQTT client
        self.rackControlService = RackControlService(self.client, self.base_topic)

        # Conecta seleção de rack para atualizar widgets
        self.list_widget.currentItemChanged.connect(self.on_rack_selected)
        
        # Connect AI action signal for thread-safe UI updates
        self.action_executed.connect(self.handleActionExecuted)
        
        # Connect status bar signal for thread-safe UI updates
        self.status_updated.connect(self.handleStatusUpdate)
        
        # Seleciona primeiro rack para inicializar widgets
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

        # Timer para amostragem periódica de histórico
        self.history_timer = QTimer(self)
        self.history_timer.setInterval(self.history_interval_seconds * 1000)
        self.history_timer.timeout.connect(self.sample_current_state)
        self.history_timer.start()

        # Initialize Forecast Service for time series prediction
        self.initializeForecastService()
        
        # Initialize Tool Calling Service for AI-driven control
        self.initializeToolCallingService()

        # Timer para análise AI periódica (intervalo do .env)
        aiInterval = int(os.getenv("AI_ANALYSIS_INTERVAL", "10")) * 1000
        self.aiAnalysisTimer = QTimer(self)
        self.aiAnalysisTimer.setInterval(aiInterval)
        self.aiAnalysisTimer.timeout.connect(self.runAiAnalysis)
        self.aiAnalysisTimer.start()

        # Timer para verificar comandos pendentes expirados
        # Verifica a cada 1 segundo se há comandos que não receberam ACK
        self.commandTimeoutTimer = QTimer(self)
        self.commandTimeoutTimer.setInterval(1000)  # 1 segundo
        self.commandTimeoutTimer.timeout.connect(self.checkExpiredCommands)
        self.commandTimeoutTimer.start()

        # Dicionário para controle de piscagem de racks
        self.blinkingRacks: dict[str, QTimer] = {}

    def setup_ui(self):
        """Setup the user interface with modern UX design"""
        # Main central widget
        central = QWidget()
        self.setCentralWidget(central)
        
        # Main vertical layout (content + status bar)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(10, 10, 10, 10)
        outer_layout.setSpacing(10)
        
        # Content area (horizontal layout)
        content_widget = QWidget()
        main_layout = QHBoxLayout(content_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)
        
        # Left sidebar - Rack list
        left_panel = self.create_left_panel()
        main_layout.addWidget(left_panel, 1)
        
        # Right panel - Details and controls
        right_panel = self.create_right_panel()
        main_layout.addWidget(right_panel, 4)
        
        outer_layout.addWidget(content_widget, 1)
        
        # Status bar for AI actions
        status_bar = self.create_status_bar()
        outer_layout.addWidget(status_bar, 0)
        
        # Apply modern stylesheet
        self.apply_stylesheet()
    
    def create_left_panel(self):
        """Create left sidebar with rack list"""
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setStyleSheet("""
            QFrame {
                background-color: #2c3e50;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)
        
        # Title
        title = QLabel("📋 Racks Disponíveis")
        title.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
            }
        """)
        layout.addWidget(title)
        
        # Rack list
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #34495e;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 5px;
                font-size: 14px;
            }
            QListWidget::item {
                padding: 10px;
                border-radius: 5px;
                margin: 2px;
            }
            QListWidget::item:selected {
                background-color: #3498db;
            }
            QListWidget::item:hover {
                background-color: #4a6278;
            }
        """)
        layout.addWidget(self.list_widget)
        
        return panel
    
    def create_right_panel(self):
        """Create right panel with rack details and controls"""
        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                background-color: #bdc3c7;
                width: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background-color: #7f8c8d;
                border-radius: 6px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #5f6c6d;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        
        # Content widget inside scroll area
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 0, 10, 0)  # Right margin for scrollbar
        
        # Header with rack info
        header = self.create_header()
        layout.addWidget(header)
        
        # Environment monitoring section
        env_section = self.create_environment_section()
        layout.addWidget(env_section)
        
        # Control buttons section
        control_section = self.create_control_section()
        layout.addWidget(control_section)
        
        # Map section
        map_section = self.create_map_section()
        layout.addWidget(map_section)
        
        # Add stretch at the end
        layout.addStretch()
        
        scroll_area.setWidget(content_widget)
        return scroll_area
    
    def create_header(self):
        """Create header with rack ID and status"""
        header = QFrame()
        header.setFrameShape(QFrame.StyledPanel)
        header.setStyleSheet("""
            QFrame {
                background-color: #34495e;
                border-radius: 10px;
                padding: 15px;
            }
        """)
        
        layout = QHBoxLayout(header)
        
        # Rack ID
        self.id_label = QLabel("🖥️ Selecione um Rack")
        self.id_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 24px;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.id_label)
        
        layout.addStretch()
        
        # Door status indicator
        self.door_status_label = QLabel("🚪 Status: --")
        self.door_status_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 16px;
                padding: 10px 20px;
                background-color: #7f8c8d;
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.door_status_label)
        
        return header
    
    def create_environment_section(self):
        """Create environment monitoring section with gauges and charts"""
        section = QFrame()
        section.setFrameShape(QFrame.StyledPanel)
        section.setStyleSheet("""
            QFrame {
                background-color: #ecf0f1;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        
        layout = QVBoxLayout(section)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        title = QLabel("🌡️ Monitoramento Ambiental")
        title.setStyleSheet("""
            QLabel {
                color: #2c3e50;
                font-size: 16px;
                font-weight: bold;
                margin-bottom: 5px;
            }
        """)
        layout.addWidget(title)
        
        # Temperature block
        temp_block = self.create_metric_block(
            metric="temperature",
            title="Temperatura",
            unit="°C",
            gauge_theme=8,
            value_color="#e74c3c",
            series_color="#e74c3c"
        )
        layout.addWidget(temp_block)
        
        # Humidity block
        hum_block = self.create_metric_block(
            metric="humidity",
            title="Umidade",
            unit="%",
            gauge_theme=3,
            value_color="#3498db",
            series_color="#3498db"
        )
        layout.addWidget(hum_block)
        
        return section

    def create_metric_block(self, metric, title, unit, gauge_theme, value_color, series_color):
        """Create a metric block with gauge on the left and line chart on the right"""
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        layout = QHBoxLayout(frame)
        layout.setSpacing(15)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Left column (gauge)
        gauge_column = QVBoxLayout()
        gauge_column.setAlignment(Qt.AlignCenter)
        
        gauge = AnalogGaugeWidget()
        gauge.units = unit
        gauge.minValue = 0
        gauge.maxValue = 100
        gauge.setGaugeTheme(gauge_theme)
        gauge.setFixedSize(90, 90)
        gauge.setMouseTracking(False)
        gauge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        gauge_column.addWidget(gauge)
        
        value_label = QLabel(f"-- {unit}")
        value_label.setAlignment(Qt.AlignCenter)
        value_label.setStyleSheet(f"""
            QLabel {{
                color: {value_color};
                font-size: 14px;
                font-weight: bold;
                margin-top: 5px;
            }}
        """)
        gauge_column.addWidget(value_label)
        
        subtitle = QLabel(title)
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #7f8c8d; font-size: 12px;")
        gauge_column.addWidget(subtitle)
        
        layout.addLayout(gauge_column, 1)
        
        # Right column (chart)
        chart = QChart()
        chart.setAnimationOptions(QChart.NoAnimation)
        chart.legend().hide()
        chart.setBackgroundBrush(QBrush(Qt.transparent))
        chart.setPlotAreaBackgroundVisible(False)
        chart.setMargins(QMargins(10, 10, 10, 10))
        
        series = QLineSeries()
        series.setColor(QColor(series_color))
        chart.addSeries(series)

        forecast_series = QLineSeries()
        forecast_color = QColor(series_color)
        forecast_color.setAlpha(180)
        forecast_pen = QPen(forecast_color)
        forecast_pen.setStyle(Qt.DashLine)
        forecast_pen.setWidth(2)
        forecast_series.setPen(forecast_pen)
        chart.addSeries(forecast_series)
        
        axis_x = QValueAxis()
        axis_x.setLabelFormat("%d")
        total_span = max(1, self.history_limit + self.forecast_horizon)
        axis_x.setRange(0, total_span)
        axis_x.setTickCount(min(10, total_span + 1))
        axis_x.setTitleText("Tempo (s)")
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)
        forecast_series.attachAxis(axis_x)
        
        axis_y = QValueAxis()
        axis_y.setLabelFormat("%d")
        axis_y.setRange(0, 100)
        axis_y.setTickCount(6)
        axis_y.setTitleText(title)
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)
        forecast_series.attachAxis(axis_y)
        
        chart_view = QChartView(chart)
        chart_view.setRenderHint(QPainter.Antialiasing)
        chart_view.setMinimumHeight(160)
        chart_view.setStyleSheet("background: transparent;")
        layout.addWidget(chart_view, 2)
        
        # Store references based on metric
        if metric == "temperature":
            self.temp_gauge = gauge
            self.temp_value_label = value_label
            self.temp_series = series
            self.temp_forecast_series = forecast_series
            self.temp_axis_x = axis_x
            self.temp_axis_y = axis_y
        elif metric == "humidity":
            self.hum_gauge = gauge
            self.hum_value_label = value_label
            self.hum_series = series
            self.hum_forecast_series = forecast_series
            self.hum_axis_x = axis_x
            self.hum_axis_y = axis_y
        
        return frame

    def append_history_sample(self, state, history_key, value, timestamp):
        """Append telemetry sample to history respecting limit"""
        if value is None or timestamp is None:
            return
        history = state.setdefault(history_key, [])
        history.append(value)
        if len(history) > self.history_limit:
            history.pop(0)

    def append_history_with_previous(self, state, metric, timestamp):
        """Append current metric value to history or reuse last known sample for chart continuity"""
        history_key = f"{metric}_history"
        value = state.get(metric)
        if value is None:
            history = state.get(history_key) or []
            if history:
                value = history[-1]
        if value is None:
            return False
        self.append_history_sample(state, history_key, value, timestamp)
        self.update_metric_forecast(state, metric)
        return True

    def sample_current_state(self):
        """Periodically sample telemetry to drive history charts"""
        try:
            now = time.time()
            for rack_id, state in list(self.rack_states.items()):
                state = self.ensure_rack_state(rack_id)
                last_ts = state.get('last_sample_timestamp')
                if last_ts is not None and (now - last_ts) < self.history_interval_seconds:
                    continue

                temp_sampled = self.append_history_with_previous(state, 'temperature', now)
                hum_sampled = self.append_history_with_previous(state, 'humidity', now)

                if temp_sampled or hum_sampled:
                    state['last_sample_timestamp'] = now
                    if self.current_rack_id == rack_id:
                        self.update_ui_from_state(rack_id)
        except Exception as e:
            print(f"[UI/Error] ❌ Error sampling history: {e}")

    def update_metric_forecast(self, state, metric):
        """
        Compute forecast for the given metric using ForecastService.
        
        Uses hybrid architecture with support for:
        - Granite TTM as primary model
        - SARIMA as fallback with daily seasonality (24h)
        - Annual seasonality for climate prediction
        - Data aggregation: raw samples -> hourly averages
        - Humidity as exogenous variable for temperature prediction
        
        Predicts 24 hours ahead using 7 days of historical data.
        
        Args:
            state: Rack state dictionary
            metric: Metric name ('temperature' or 'humidity')
        """
        history_key = f"{metric}_history"
        forecast_key = f"{metric}_forecast"
        history = state.get(history_key) or []

        if not history:
            state[forecast_key] = []
            return

        # Minimum data requirement: at least 1 hour of samples (3600 points at 1s interval)
        minSamples = 3600  # 1 hour
        
        # Try using ForecastService (Granite TTM / SARIMA fallback)
        if self.forecastService is not None and len(history) >= minSamples:
            try:
                # Prepare data history for ForecastService
                # Use all available history (up to 7 days) for better seasonal analysis
                dataHistory = []
                currentTime = time.time()
                historySlice = history[-self.history_limit:]  # Up to 7 days
                
                for i, value in enumerate(historySlice):
                    timestamp = currentTime - (len(historySlice) - i - 1)
                    dataHistory.append({
                        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(timestamp)),
                        'value': value
                    })
                
                # Prepare exogenous data (humidity) for temperature prediction
                exogenousData = None
                if metric == 'temperature':
                    humidityHistory = state.get('humidity_history') or []
                    if len(humidityHistory) >= minSamples:
                        exogenousData = []
                        humiditySlice = humidityHistory[-self.history_limit:]
                        for i, value in enumerate(humiditySlice):
                            timestamp = currentTime - (len(humiditySlice) - i - 1)
                            exogenousData.append({
                                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(timestamp)),
                                'value': value
                            })
                
                # Get prediction from ForecastService (will aggregate to hourly internally)
                result = self.forecastService.predict(
                    dataHistory, 
                    aggregateData=True,
                    exogenousData=exogenousData
                )
                
                if result and 'predictions' in result:
                    predictions = result['predictions']
                    # Extract 24 hourly predictions
                    forecast = [p['value'] for p in predictions[:self.forecast_horizon]]
                    state[forecast_key] = forecast
                    
                    # Store metadata for debugging
                    state[f'{metric}_forecast_model'] = result.get('model', 'unknown')
                    state[f'{metric}_forecast_aggregated'] = result.get('aggregated', False)
                    state[f'{metric}_forecast_seasonal'] = result.get('annual_seasonality_applied', False)
                    state[f'{metric}_forecast_humidity_corrected'] = result.get('humidity_correction_applied', False)
                    
                    # Update MAE tracking if we have actual values to compare
                    if len(history) >= 2:
                        lastPredicted = history[-1]
                        lastActual = history[-2]
                        self.forecastService.updateMaeTracking(lastPredicted, lastActual)
                    
                    return
                    
            except Exception as e:
                print(f"[Forecast/Error] ⚠️ ForecastService error for {metric}: {e}")
        
        # Fallback to simple linear forecast (for display before enough data collected)
        last_value = history[-1]
        if len(history) >= 2:
            slope = history[-1] - history[-2]
        else:
            slope = 0.0

        forecast = []
        for step in range(1, self.forecast_horizon + 1):
            forecast.append(last_value + slope * step)

        state[forecast_key] = forecast

    def update_chart(self, series, forecast_series, axis_x, axis_y, history, forecast, default_min=0, default_max=100):
        """Update line chart with new historical data"""
        if series is None or axis_x is None or axis_y is None:
            return

        # Filter out None values
        values = [v for v in history if v is not None]

        series.clear()
        if forecast_series is not None:
            forecast_series.clear()

        if values:
            # Keep only the most recent samples within limit
            values = values[-self.history_limit:]
            for idx, value in enumerate(values):
                series.append(idx, value)

            forecast_values = forecast or []
            forecast_values = [v for v in forecast_values if v is not None]
            for offset, value in enumerate(forecast_values, start=len(values)):
                if forecast_series is not None:
                    forecast_series.append(offset, value)

            total_length = len(values) + len(forecast_values)
            if total_length <= 0:
                total_length = 1
            axis_x.setRange(0, max(total_length, self.forecast_horizon))
            axis_x.setTickCount(min(10, total_length + 1))

            combined_values = values + forecast_values
            min_val = min(combined_values)
            max_val = max(combined_values)
            if min_val == max_val:
                padding = max(5, min_val * 0.1)
                axis_y.setRange(max(default_min, min_val - padding), min(default_max, max_val + padding))
            else:
                padding = max(3, (max_val - min_val) * 0.1)
                axis_y.setRange(max(default_min, min_val - padding), min(default_max, max_val + padding))
        else:
            axis_x.setRange(0, self.history_limit)
            axis_x.setTickCount(6)
            axis_y.setRange(default_min, default_max)
    
    def create_control_section(self):
        """Create control buttons section"""
        section = QFrame()
        section.setFrameShape(QFrame.StyledPanel)
        section.setStyleSheet("""
            QFrame {
                background-color: #ecf0f1;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        
        layout = QVBoxLayout(section)
        layout.setSpacing(5)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Title
        title = QLabel("🎛️ Controles")
        title.setStyleSheet("""
            QLabel {
                color: #2c3e50;
                font-size: 16px;
                font-weight: bold;
                margin-bottom: 5px;
            }
        """)
        layout.addWidget(title)
        
        # Buttons grid
        buttons_layout = QGridLayout()
        buttons_layout.setSpacing(10)
        
        # Door toggle button
        self.btn_door_toggle = QPushButton("🚪 Porta")
        self.btn_door_toggle.clicked.connect(self.toggle_door)
        self.btn_door_toggle.setStyleSheet(self.get_button_style("#7f8c8d"))
        self.btn_door_toggle.setMinimumHeight(50)
        buttons_layout.addWidget(self.btn_door_toggle, 0, 0)
        
        # Ventilation toggle button
        self.btn_vent_toggle = QPushButton("💨 Ventilação")
        self.btn_vent_toggle.clicked.connect(self.toggle_ventilation)
        self.btn_vent_toggle.setStyleSheet(self.get_button_style("#7f8c8d"))
        self.btn_vent_toggle.setMinimumHeight(50)
        buttons_layout.addWidget(self.btn_vent_toggle, 0, 1)
        
        # Buzzer status indicator
        self.buzzer_status_label = QLabel("🔔 Buzzer: Desligado")
        self.buzzer_status_label.setAlignment(Qt.AlignCenter)
        self.buzzer_status_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
                background-color: #95a5a6;
                border-radius: 5px;
            }
        """)
        buttons_layout.addWidget(self.buzzer_status_label, 2, 0, 1, 2)
        
        layout.addLayout(buttons_layout)
        
        return section
    
    def create_map_section(self):
        """Create map section"""
        section = QFrame()
        section.setFrameShape(QFrame.StyledPanel)
        section.setStyleSheet("""
            QFrame {
                background-color: #ecf0f1;
                border-radius: 10px;
                padding: 20px;
            }
        """)
        
        layout = QVBoxLayout(section)
        
        # Title
        title = QLabel("📍 Localização")
        title.setStyleSheet("""
            QLabel {
                color: #2c3e50;
                font-size: 18px;
                font-weight: bold;
                margin-bottom: 10px;
            }
        """)
        layout.addWidget(title)
        
        # Map view usando Leaflet.js + OpenStreetMap (opensource)
        self.map_view = QWebEngineView()
        self.map_view.setMinimumHeight(600)
        
        # Configura WebChannel para comunicação JavaScript <-> Python
        self.map_channel = QWebChannel()
        self.map_channel.registerObject("pybridge", self)
        self.map_view.page().setWebChannel(self.map_channel)
        
        initial_map = self.generate_all_racks_map_html()
        self.map_view.setHtml(initial_map)
        layout.addWidget(self.map_view)
        
        return section
    
    @pyqtSlot(str)
    def selectRackFromMap(self, rack_id: str):
        """
        Callback chamado pelo JavaScript quando um rack é clicado no mapa.
        Seleciona o rack correspondente na lista lateral.
        
        Args:
            rack_id: ID do rack clicado
        """
        print(f"[UI/Map] 📍 Rack {rack_id} clicado no mapa")
        
        # Encontra o item na lista
        items = self.list_widget.findItems(f"Rack {rack_id}", Qt.MatchExactly)
        if items:
            self.list_widget.setCurrentItem(items[0])
    
    def generate_all_racks_map_html(self, selected_rack_id: str = None) -> str:
        """
        Gera o HTML para exibir o mapa com TODOS os racks simultaneamente.
        
        OpenStreetMap é um mapa opensource gratuito com licença ODbL.
        Leaflet.js é uma biblioteca JavaScript opensource (BSD-2-Clause).
        
        Args:
            selected_rack_id: ID do rack atualmente selecionado (para destacar)
            
        Returns:
            HTML completo com mapa Leaflet/OpenStreetMap e todos os racks
        """
        # Centro de Fortaleza-CE como padrão
        default_lat = -3.7319
        default_lon = -38.5267
        
        # Coleta todos os racks com coordenadas
        racks_data = []
        for rack_id, state in self.rack_states.items():
            lat = state.get('latitude')
            lon = state.get('longitude')
            if lat is not None and lon is not None:
                racks_data.append({
                    'id': rack_id,
                    'lat': lat,
                    'lon': lon,
                    'state': state
                })
        
        # Determina centro e zoom
        if selected_rack_id and selected_rack_id in self.rack_states:
            state = self.rack_states[selected_rack_id]
            center_lat = state.get('latitude', default_lat)
            center_lon = state.get('longitude', default_lon)
            zoom = 14
        elif racks_data:
            # Centraliza na média de todos os racks
            center_lat = sum(r['lat'] for r in racks_data) / len(racks_data)
            center_lon = sum(r['lon'] for r in racks_data) / len(racks_data)
            zoom = 12
        else:
            center_lat = default_lat
            center_lon = default_lon
            zoom = 12
        
        # Gera JavaScript para cada marcador
        markers_js = ""
        for rack in racks_data:
            rid = rack['id']
            rlat = rack['lat']
            rlon = rack['lon']
            rstate = rack['state']
            is_selected = rid == selected_rack_id
            
            # Monta conteúdo do popup
            popup_lines = [f"<b>🖥️ Rack {rid}</b>"]
            
            temp = rstate.get('temperature')
            if temp is not None:
                temp_icon = "🔥" if temp > 35 else "❄️" if temp < 18 else "🌡️"
                popup_lines.append(f"{temp_icon} Temp: {temp:.1f}°C")
            
            hum = rstate.get('humidity')
            if hum is not None:
                hum_icon = "💧" if hum > 70 else "🏜️" if hum < 30 else "💨"
                popup_lines.append(f"{hum_icon} Umidade: {hum:.1f}%")
            
            door = rstate.get('door_status')
            if door is not None:
                door_text = "ABERTA" if door == 1 else "FECHADA"
                door_icon = "🚪" if door == 1 else "🔒"
                popup_lines.append(f"{door_icon} Porta: {door_text}")
            
            vent = rstate.get('ventilation_status')
            if vent is not None:
                vent_text = "LIGADA" if vent == 1 else "DESLIGADA"
                vent_icon = "💨" if vent == 1 else "🛑"
                popup_lines.append(f"{vent_icon} Ventilação: {vent_text}")
            
            buzzer = rstate.get('buzzer_status')
            if buzzer is not None and buzzer > 0:
                buzzer_states = {1: '🔔 Porta Aberta', 2: '🚨 ARROMBAMENTO', 3: '🔥 SUPERAQUECIMENTO'}
                popup_lines.append(buzzer_states.get(buzzer, ''))
            
            popup_lines.append(f"<small>📍 {rlat:.6f}, {rlon:.6f}</small>")
            popup_content = "<br>".join(popup_lines)
            
            # Cor do marcador: azul escuro para selecionado, azul claro para outros
            bg_color = "#2c3e50" if is_selected else "#3498db"
            border = "3px solid #f39c12" if is_selected else "none"
            
            markers_js += f"""
                (function() {{
                    var rackIcon = L.divIcon({{
                        className: 'rack-marker',
                        html: '<div style="background-color: {bg_color}; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold; font-size: 11px; box-shadow: 0 2px 5px rgba(0,0,0,0.3); border: {border}; cursor: pointer;">🖥️ {rid}</div>',
                        iconSize: [80, 25],
                        iconAnchor: [40, 25],
                        popupAnchor: [0, -25]
                    }});
                    var marker = L.marker([{rlat}, {rlon}], {{icon: rackIcon}}).addTo(map);
                    marker.bindPopup("{popup_content}");
                    marker.on('click', function() {{
                        if (window.pybridge) {{
                            window.pybridge.selectRackFromMap("{rid}");
                        }}
                    }});
                    markers["{rid}"] = marker;
                    {"marker.openPopup();" if is_selected else ""}
                }})();
            """
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Mapa dos Racks</title>
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" 
                  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" 
                  crossorigin=""/>
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" 
                    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" 
                    crossorigin=""></script>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <style>
                html, body {{
                    margin: 0;
                    padding: 0;
                    height: 100%;
                    width: 100%;
                }}
                #map {{
                    height: 100%;
                    width: 100%;
                    border-radius: 10px;
                }}
                .leaflet-control-attribution {{
                    font-size: 10px;
                }}
            </style>
        </head>
        <body>
            <div id="map"></div>
            <script>
                // Inicializa o mapa centrado em Fortaleza-CE, Brasil
                var map = L.map('map').setView([{center_lat}, {center_lon}], {zoom});
                var markers = {{}};
                
                // Tiles do OpenStreetMap (ODbL License)
                L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                    maxZoom: 19,
                    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                }}).addTo(map);
                
                // Configura comunicação com Python via WebChannel
                new QWebChannel(qt.webChannelTransport, function(channel) {{
                    window.pybridge = channel.objects.pybridge;
                }});
                
                // Adiciona marcadores para todos os racks
                {markers_js}
                
                // Função para centralizar no rack (chamada pelo Python)
                window.centerOnRack = function(rackId) {{
                    if (markers[rackId]) {{
                        map.setView(markers[rackId].getLatLng(), 14);
                        markers[rackId].openPopup();
                    }}
                }};
            </script>
        </body>
        </html>
        """
        return html
    
    def update_map_view(self, rack_id: str = None, force: bool = False):
        """
        Atualiza o mapa com todos os racks, destacando o selecionado.
        
        O mapa só é atualizado se houver mudanças ou se force=True.
        
        Args:
            rack_id: ID do rack selecionado (para destacar e centralizar)
            force: Força atualização mesmo se não houve mudanças
        """
        if not hasattr(self, 'map_view') or self.map_view is None:
            return
        
        # Gera hash do estado atual para detectar mudanças
        current_hash = hash((rack_id, len(self.rack_states), str(sorted(self.rack_states.keys()))))
        last_hash = getattr(self, '_last_map_hash', None)
        
        if not force and last_hash == current_hash:
            # Nenhuma mudança, apenas centraliza no rack se necessário
            if rack_id:
                self.map_view.page().runJavaScript(f'if(window.centerOnRack) centerOnRack("{rack_id}");')
            return
        
        # Armazena hash atual
        self._last_map_hash = current_hash
        
        map_html = self.generate_all_racks_map_html(rack_id)
        self.map_view.setHtml(map_html)
        print(f"[UI/Map] 🗺️ Mapa atualizado com {len(self.rack_states)} racks")
    
    def create_status_bar(self):
        """Create status bar for AI actions display"""
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.StyledPanel)
        status_frame.setFixedHeight(60)
        status_frame.setStyleSheet("""
            QFrame {
                background-color: #2c3e50;
                border-radius: 8px;
                padding: 5px;
            }
        """)
        
        layout = QHBoxLayout(status_frame)
        layout.setContentsMargins(15, 5, 15, 5)
        layout.setSpacing(15)
        
        # AI indicator
        ai_indicator = QLabel("🤖 IA")
        ai_indicator.setStyleSheet("""
            QLabel {
                color: #3498db;
                font-size: 14px;
                font-weight: bold;
            }
        """)
        layout.addWidget(ai_indicator)
        
        # Status icon (animated)
        self.status_icon = QLabel("⏸️")
        self.status_icon.setStyleSheet("""
            QLabel {
                color: #95a5a6;
                font-size: 16px;
            }
        """)
        layout.addWidget(self.status_icon)
        
        # Status message
        self.status_message = QLabel("Aguardando dados...")
        self.status_message.setStyleSheet("""
            QLabel {
                color: #ecf0f1;
                font-size: 13px;
            }
        """)
        layout.addWidget(self.status_message, 1)
        
        # Last action info
        self.last_action_label = QLabel("")
        self.last_action_label.setStyleSheet("""
            QLabel {
                color: #f39c12;
                font-size: 12px;
                font-style: italic;
            }
        """)
        layout.addWidget(self.last_action_label)
        
        # Thresholds info
        tempHigh = os.getenv("TEMP_HIGH_THRESHOLD", "35")
        tempLow = os.getenv("TEMP_LOW_THRESHOLD", "28")
        thresholds_label = QLabel(f"🎚️ Histerese: {tempLow}°C ↔ {tempHigh}°C")
        thresholds_label.setStyleSheet("""
            QLabel {
                color: #7f8c8d;
                font-size: 11px;
            }
        """)
        layout.addWidget(thresholds_label)
        
        return status_frame
    
    def get_button_style(self, color):
        """Get button stylesheet with specified color"""
        return f"""
            QPushButton {{
                background-color: {color};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
                font-weight: bold;
                min-height: 40px;
            }}
            QPushButton:hover {{
                background-color: {self.adjust_color(color, -20)};
            }}
            QPushButton:pressed {{
                background-color: {self.adjust_color(color, -40)};
            }}
            QPushButton:disabled {{
                background-color: #bdc3c7;
                color: #7f8c8d;
            }}
        """
    
    def adjust_color(self, hex_color, amount):
        """Adjust hex color brightness"""
        hex_color = hex_color.lstrip('#')
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        r = max(0, min(255, r + amount))
        g = max(0, min(255, g + amount))
        b = max(0, min(255, b + amount))
        return f'#{r:02x}{g:02x}{b:02x}'
    
    def apply_stylesheet(self):
        """Apply global stylesheet"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #bdc3c7;
            }
            QLabel {
                color: #2c3e50;
            }
        """)
    
    def ensure_rack_state(self, rack_id):
        """Ensure rack state cache exists for rack"""
        if rack_id not in self.rack_states:
            self.rack_states[rack_id] = {
                'temperature': None,
                'humidity': None,
                'door_status': None,
                'ventilation_status': None,
                'buzzer_status': None,
                # GPS data (padronizado com firmware)
                'latitude': None,
                'longitude': None,
                'altitude': None,
                'gps_time': None,
                'gps_speed': None,
                # Tilt sensor (padronizado com firmware)
                'tilt': False,
                # Historical data
                'temperature_history': [],
                'humidity_history': [],
                'temperature_forecast': [],
                'humidity_forecast': [],
                'last_sample_timestamp': None
            }
        else:
            state = self.rack_states[rack_id]
            state.setdefault('temperature_history', [])
            state.setdefault('humidity_history', [])
            state.setdefault('temperature_forecast', [])
            state.setdefault('humidity_forecast', [])
            state.setdefault('last_sample_timestamp', None)
            state.setdefault('latitude', None)
            state.setdefault('longitude', None)
            # GPS data adicional (padronizado com firmware)
            state.setdefault('altitude', None)
            state.setdefault('gps_time', None)
            state.setdefault('gps_speed', None)
            # Tilt sensor (padronizado com firmware)
            state.setdefault('tilt', False)
        return self.rack_states[rack_id]

    def getOrCreateRack(self, rackId: str) -> Rack:
        """
        Obtém ou cria uma instância de Rack para o ID especificado.
        
        Args:
            rackId: Identificador do rack
            
        Returns:
            Rack: Instância do rack
        """
        if rackId not in self.racks:
            self.racks[rackId] = Rack(rackId=rackId)
        return self.racks[rackId]
    
    def syncRackFromState(self, rack: Rack, state: dict):
        """
        Sincroniza o objeto Rack com o estado em cache.
        
        Args:
            rack: Instância do Rack
            state: Dicionário de estado do rack
        """
        if state.get('temperature') is not None:
            rack.temperature = state['temperature']
        if state.get('humidity') is not None:
            rack.humidity = state['humidity']
        if state.get('door_status') is not None:
            rack.doorStatus = DoorStatus(state['door_status'])
        if state.get('ventilation_status') is not None:
            rack.ventilationStatus = VentilationStatus(state['ventilation_status'])
        if state.get('buzzer_status') is not None:
            rack.buzzerStatus = BuzzerStatus(state['buzzer_status'])
    
    def syncStateFromRack(self, rack: Rack, state: dict):
        """
        Sincroniza o estado em cache a partir do objeto Rack.
        
        Args:
            rack: Instância do Rack
            state: Dicionário de estado do rack
        """
        state['temperature'] = rack.temperature
        state['humidity'] = rack.humidity
        state['door_status'] = int(rack.doorStatus)
        state['ventilation_status'] = int(rack.ventilationStatus)
        state['buzzer_status'] = int(rack.buzzerStatus)

    def toggle_door(self):
        """
        Toggle door state (open/close) using RackControlService.
        
        A atualização da UI NÃO é feita imediatamente.
        O estado só será atualizado quando o firmware confirmar via ACK.
        """
        if not self.currentRack:
            print("[UI/Warning] ⚠️  No rack selected")
            return
        
        # Use the service to toggle door - UI update will happen on ACK
        success = self.rackControlService.toggleDoor(self.currentRack)
        
        if success:
            print(f"[UI/Command] 🚀 Door command sent, awaiting firmware confirmation...")

    def toggle_ventilation(self):
        """
        Toggle ventilation state (on/off) using RackControlService.
        
        A atualização da UI NÃO é feita imediatamente.
        O estado só será atualizado quando o firmware confirmar via ACK.
        """
        if not self.currentRack:
            print("[UI/Warning] ⚠️  No rack selected")
            return
        
        # Use the service to toggle ventilation - UI update will happen on ACK
        success = self.rackControlService.toggleVentilation(self.currentRack)
        
        if success:
            print(f"[UI/Command] 🚀 Ventilation command sent, awaiting firmware confirmation...")

    def send_command(self, command_type, value):
        """
        Send MQTT command to rack using RackControlService.
        
        A atualização da UI NÃO é feita imediatamente.
        O estado só será atualizado quando o firmware confirmar via ACK.
        
        This method is kept for backward compatibility but now delegates
        to RackControlService methods.
        """
        if not self.currentRack:
            print("[UI/Warning] ⚠️  No rack selected")
            return
        
        success = False
        if command_type == "door":
            if value == 1:
                success = self.rackControlService.openDoor(self.currentRack)
            else:
                success = self.rackControlService.closeDoor(self.currentRack)
        elif command_type == "ventilation":
            if value == 1:
                success = self.rackControlService.turnOnVentilation(self.currentRack)
            else:
                success = self.rackControlService.turnOffVentilation(self.currentRack)
        elif command_type == "buzzer":
            if value == BuzzerStatus.OFF:
                success = self.rackControlService.silenceBuzzer(self.currentRack)
            elif value == BuzzerStatus.OVERHEAT:
                success = self.rackControlService.activateCriticalTemperatureAlert(self.currentRack)
            elif value == BuzzerStatus.DOOR_OPEN:
                success = self.rackControlService.activateDoorOpenAlert(self.currentRack)
            elif value == BuzzerStatus.BREAK_IN:
                success = self.rackControlService.activateBreakInAlert(self.currentRack)
        
        if success:
            print(f"[UI/Command] 🚀 {command_type} command sent, awaiting firmware confirmation...")

    def setup_mqtt(self):
        """Configure and connect to MQTT broker"""
        # Validate required environment variables
        server = os.getenv("MQTT_SERVER")
        if not server:
            raise ValueError("MQTT_SERVER not configured in .env file. Please copy .env.example to .env and configure it.")
        
        username = os.getenv("MQTT_USERNAME")
        password = os.getenv("MQTT_PASSWORD")
        port = int(os.getenv("MQTT_PORT", 1883))
        keepalive = int(os.getenv("MQTT_KEEPALIVE", 60))
        
        # Use CallbackAPIVersion.VERSION2 to avoid deprecation warning
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        
        if username:
            self.client.username_pw_set(username, password)
        
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect(server, port, keepalive)
        self.client.loop_start()

    def on_connect(self, client, userdata, flags, rc, properties=None):
        """Callback when connected to MQTT broker (API v2)"""
        print(f"[MQTT/Connection] 🔌 Connected with result code: {rc}")
        
        # Subscribe to all rack topics
        # Tópicos padronizados com o firmware (origem):
        # - environment/door: estado da porta (0=fechada, 1=aberta)
        # - environment/temperature: temperatura ambiente
        # - environment/humidity: umidade ambiente
        # - gps: coordenadas GPS (latitude, longitude, altitude, time, speed)
        # - tilt: inclinação detectada
        # - ack/*: confirmações de comandos do firmware
        base = self.base_topic
        topics = [
            f"{base}/+/environment/door",
            f"{base}/+/environment/temperature",
            f"{base}/+/environment/humidity",
            f"{base}/+/gps",
            f"{base}/+/tilt",
            f"{base}/+/command/door",
            f"{base}/+/command/ventilation",
            f"{base}/+/command/buzzer",
            # Tópicos de confirmação (ACK) do firmware
            f"{base}/+/ack/door",
            f"{base}/+/ack/ventilation",
            f"{base}/+/ack/buzzer",
        ]
        
        for topic in topics:
            client.subscribe(topic)
            print(f"[MQTT/Subscription] 📡 Subscribed to: {topic}")

    def on_message(self, client, userdata, msg):
        """Callback when message received from MQTT broker (MQTT thread)."""
        print(f"[MQTT/Message] 📬 Received message on topic: {msg.topic}")
        try:
            topic = msg.topic
            payload = msg.payload.decode()

            prefix = f"{self.base_topic}/"
            if not topic.startswith(prefix):
                return

            remainder = topic[len(prefix):]
            parts = remainder.split('/')
            if len(parts) < 2:
                return

            rack_id = parts[0]

            # Delega o processamento para o thread da GUI via sinal Qt
            self.message_received.emit({
                'topic': topic,
                'rack_id': rack_id,
                'payload': payload,
            })

        except Exception as e:
            print(f"[MQTT/Error] ❌ Error processing message: {e}")
            import traceback
            traceback.print_exc()

    def handle_message_update(self, data):
        """Handle MQTT message in GUI thread, updating state, DB and UI."""
        try:
            topic = data.get('topic')
            rack_id = data.get('rack_id')
            payload = data.get('payload')

            if not topic or not rack_id:
                return

            # Ensure state cache
            state = self.ensure_rack_state(rack_id)

            # Add rack to list if not present and ensure Rack object exists
            items = self.list_widget.findItems(f"Rack {rack_id}", Qt.MatchExactly)
            if not items:
                self.list_widget.addItem(f"Rack {rack_id}")
            
            # Get or create Rack object for this rack_id
            rack = self.getOrCreateRack(rack_id)

            # Update state based on topic
            if topic.endswith('/environment/door'):
                # Estado da porta (lógica invertida no firmware - pull-up)
                # 0 = fechada, 1 = aberta
                raw_value = int(payload)
                state['door_status'] = 1 if raw_value == 1 else 0
                print(f"[MQTT/Environment] 🚪 Rack {rack_id} door: {'ABERTA' if state['door_status'] == 1 else 'FECHADA'}")

            elif topic.endswith('/environment/temperature'):
                temp_value = float(payload)
                state['temperature'] = temp_value
                # Append sample to temperature history for charts
                self.append_history_sample(state, 'temperature_history', temp_value, time.time())
                self.update_metric_forecast(state, 'temperature')
                print(f"[MQTT/Environment] 🌡️ Rack {rack_id} temperature: {temp_value}°C")

            elif topic.endswith('/environment/humidity'):
                hum_value = float(payload)
                state['humidity'] = hum_value
                # Append sample to humidity history for charts
                self.append_history_sample(state, 'humidity_history', hum_value, time.time())
                self.update_metric_forecast(state, 'humidity')
                print(f"[MQTT/Environment] 💧 Rack {rack_id} humidity: {hum_value}%")

            elif topic.endswith('/command/door'):
                # Porta só pode estar aberta (1) ou fechada (0)
                raw_value = int(payload)
                state['door_status'] = 1 if raw_value == 1 else 0
                print(f"[MQTT/Command] 🚪 Rack {rack_id} door command: {'OPEN' if state['door_status'] == 1 else 'CLOSE'}")

            elif topic.endswith('/command/ventilation'):
                # Ventilação só pode estar ligada (1) ou desligada (0)
                raw_value = int(payload)
                state['ventilation_status'] = 1 if raw_value == 1 else 0
                print(f"[MQTT/Command] 💨 Rack {rack_id} ventilation: {'ON' if state['ventilation_status'] == 1 else 'OFF'}")

            elif topic.endswith('/command/buzzer'):
                # Buzzer só aceita valores 0-3 (OFF, DOOR_OPEN, BREAK_IN, OVERHEAT)
                raw_value = int(payload)
                state['buzzer_status'] = raw_value if 0 <= raw_value <= 3 else 0
                buzzer_states = {0: 'Desligado', 1: 'Porta Aberta', 2: 'Arrombamento', 3: 'Superaquecimento'}
                print(f"[MQTT/Command] 🔔 Rack {rack_id} buzzer: {buzzer_states.get(state['buzzer_status'], 'Unknown')}")

            elif topic.endswith('/gps'):
                # Processa coordenadas GPS do rack (padronizado com firmware)
                # Payload JSON: {latitude, longitude, altitude, time, speed}
                try:
                    import json
                    gps_data = json.loads(payload)
                    state['latitude'] = float(gps_data.get('latitude', 0))
                    state['longitude'] = float(gps_data.get('longitude', 0))
                    state['altitude'] = float(gps_data.get('altitude', 0))
                    state['gps_time'] = int(gps_data.get('time', 0))
                    state['gps_speed'] = float(gps_data.get('speed', 0))
                    print(f"[MQTT/GPS] 📍 Rack {rack_id} GPS: lat={state['latitude']:.6f}, lon={state['longitude']:.6f}, alt={state['altitude']:.1f}m")
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    print(f"[MQTT/Error] ❌ Invalid GPS data for rack {rack_id}: {e}")
            
            elif topic.endswith('/tilt'):
                # Processa estado de inclinação do rack (padronizado com firmware)
                # Payload: "1" = inclinado, "0" = normal
                tilt_value = int(payload)
                state['tilt'] = tilt_value == 1
                print(f"[MQTT/Tilt] ⚠️ Rack {rack_id} tilt: {'INCLINADO' if state['tilt'] else 'NORMAL'}")
                if state['tilt']:
                    print(f"[MQTT/Tilt] 🚨 ALERTA: Rack {rack_id} está inclinado!")
            
            # Processa confirmações (ACK) do firmware
            elif topic.endswith('/ack/door'):
                # Confirmação de comando de porta recebida do firmware
                raw_value = int(payload)
                state['door_status'] = 1 if raw_value == 1 else 0
                rack.doorStatus = DoorStatus(state['door_status'])
                self.rackControlService.processAck(rack_id, "door", raw_value)
                print(f"[MQTT/ACK] ✅ Rack {rack_id} door ACK: {'OPEN' if state['door_status'] == 1 else 'CLOSED'}")
            
            elif topic.endswith('/ack/ventilation'):
                # Confirmação de comando de ventilação recebida do firmware
                raw_value = int(payload)
                state['ventilation_status'] = 1 if raw_value == 1 else 0
                rack.ventilationStatus = VentilationStatus(state['ventilation_status'])
                self.rackControlService.processAck(rack_id, "ventilation", raw_value)
                print(f"[MQTT/ACK] ✅ Rack {rack_id} ventilation ACK: {'ON' if state['ventilation_status'] == 1 else 'OFF'}")
            
            elif topic.endswith('/ack/buzzer'):
                # Confirmação de comando de buzzer recebida do firmware
                raw_value = int(payload)
                state['buzzer_status'] = raw_value if 0 <= raw_value <= 3 else 0
                rack.buzzerStatus = BuzzerStatus(state['buzzer_status'])
                self.rackControlService.processAck(rack_id, "buzzer", raw_value)
                buzzer_states = {0: 'Desligado', 1: 'Porta Aberta', 2: 'Arrombamento', 3: 'Superaquecimento'}
                print(f"[MQTT/ACK] ✅ Rack {rack_id} buzzer ACK: {buzzer_states.get(state['buzzer_status'], 'Unknown')}")

            # Sync Rack object with updated state
            self.syncRackFromState(rack, state)

            # Save to database
            self.save_rack_state(rack_id)

            # Send telemetry to ToolCallingService for AI analysis
            if self.toolCallingService:
                self.toolCallingService.updateTelemetry(rack_id, state)

            # If this rack is currently selected, refresh UI and charts immediately
            if self.current_rack_id == rack_id:
                self.update_ui_from_state(rack_id)

        except Exception as e:
            print(f"[UI/Error] ❌ Error handling MQTT update in GUI thread: {e}")
            import traceback
            traceback.print_exc()

    def update_ui_from_state(self, rack_id, refresh_charts=True):
        """Update UI from rack state cache"""
        try:
            if rack_id not in self.rack_states:
                return

            state = self.rack_states[rack_id]

            # Update header
            self.id_label.setText(f"🖥️ Rack {rack_id}")

            # Update door status
            if state['door_status'] is not None:
                if state['door_status'] == 1:
                    self.door_status_label.setText("🚪 Porta: ABERTA")
                    self.door_status_label.setStyleSheet(
                        """
                        QLabel {
                            color: white;
                            font-size: 16px;
                            padding: 10px 20px;
                            background-color: #27ae60;
                            border-radius: 5px;
                        }
                        """
                    )
                else:
                    self.door_status_label.setText("🔒 Porta: FECHADA")
                    self.door_status_label.setStyleSheet(
                        """
                        QLabel {
                            color: white;
                            font-size: 16px;
                            padding: 10px 20px;
                            background-color: #c0392b;
                            border-radius: 5px;
                        }
                        """
                    )

            # Update temperature
            if state['temperature'] is not None:
                temp = state['temperature']
                temp_int = int(round(temp))
                temp_clamped = max(self.temp_gauge.minValue, min(self.temp_gauge.maxValue, temp_int))
                self.temp_gauge.setValue(temp_clamped)
                self.temp_value_label.setText(f"{temp:.1f} °C")
                if refresh_charts:
                    self.update_chart(
                        self.temp_series,
                        self.temp_forecast_series,
                        self.temp_axis_x,
                        self.temp_axis_y,
                        state.get('temperature_history', []),
                        state.get('temperature_forecast', []),
                        default_min=0,
                        default_max=100
                    )

            # Update humidity
            if state['humidity'] is not None:
                hum = state['humidity']
                hum_int = int(round(hum))
                hum_clamped = max(self.hum_gauge.minValue, min(self.hum_gauge.maxValue, hum_int))
                self.hum_gauge.setValue(hum_clamped)
                self.hum_value_label.setText(f"{hum:.1f} %")
                if refresh_charts:
                    self.update_chart(
                        self.hum_series,
                        self.hum_forecast_series,
                        self.hum_axis_x,
                        self.hum_axis_y,
                        state.get('humidity_history', []),
                        state.get('humidity_forecast', []),
                        default_min=0,
                        default_max=100
                    )

            # Update door button appearance
            if state['door_status'] is not None:
                if state['door_status'] == 1:
                    self.btn_door_toggle.setText("🚪 Fechar Porta")
                    self.btn_door_toggle.setStyleSheet(self.get_button_style("#27ae60"))
                else:
                    self.btn_door_toggle.setText("🚪 Abrir Porta")
                    self.btn_door_toggle.setStyleSheet(self.get_button_style("#c0392b"))

            # Update ventilation button appearance
            if state['ventilation_status'] is not None:
                if state['ventilation_status'] == 1:
                    self.btn_vent_toggle.setText("💨 Desligar Ventilação")
                    self.btn_vent_toggle.setStyleSheet(self.get_button_style("#3498db"))
                else:
                    self.btn_vent_toggle.setText("💨 Ligar Ventilação")
                    self.btn_vent_toggle.setStyleSheet(self.get_button_style("#95a5a6"))

            # Update buzzer status
            if state['buzzer_status'] is not None:
                buzzer_states = {
                    0: ('🔕 Buzzer: Desligado', '#95a5a6'),
                    1: ('🔔 Buzzer: Porta Aberta', '#f39c12'),
                    2: ('🚨 Buzzer: ARROMBAMENTO', '#e74c3c'),
                    3: ('🔥 Buzzer: SUPERAQUECIMENTO', '#e74c3c')
                }
                text, color = buzzer_states.get(state['buzzer_status'], ('🔔 Buzzer: --', '#95a5a6'))
                self.buzzer_status_label.setText(text)
                self.buzzer_status_label.setStyleSheet(
                    f"""
                    QLabel {{
                        color: white;
                        font-size: 16px;
                        font-weight: bold;
                        padding: 15px;
                        background-color: {color};
                        border-radius: 5px;
                    }}
                    """
                )

            # Update map with all racks (only reloads if rack list changed)
            self.update_map_view(rack_id)

        except Exception as e:
            print(f"[UI/Error] ❌ Error updating UI: {e}")

    def save_rack_state(self, rack_id):
        """Save rack state to database"""
        try:
            if rack_id not in self.rack_states:
                return

            state = self.rack_states[rack_id]

            self.execute_db(
                """
                INSERT INTO rack_data 
                (id, temperature, humidity, door_status, ventilation_status, buzzer_status, latitude, longitude) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rack_id,
                    state.get('temperature'),
                    state.get('humidity'),
                    state.get('door_status'),
                    state.get('ventilation_status'),
                    state.get('buzzer_status'),
                    state.get('latitude'),
                    state.get('longitude'),
                ),
                commit=True
            )
        except Exception as e:
            print(f"[DB/Error] ❌ Error saving rack state: {e}")

    def on_rack_selected(self, current, previous):
        """Handle rack selection from list widget"""
        if current:
            try:
                # Extract rack ID from "Rack <id>" format preserving alphanumeric IDs
                text = current.text().strip()
                parts = text.split(" ", 1)
                rack_id = parts[1] if len(parts) == 2 else text
                self.current_rack_id = rack_id
                
                # Get or create Rack object and set as current
                self.currentRack = self.getOrCreateRack(rack_id)

                print(f"[UI/Selection] 🖱️ Selected rack {rack_id}")

                # Clear gauges and chart series so the new rack starts fresh
                self.reset_dashboard_metrics()

                # Load last state from database
                row = self.execute_db(
                    """
                    SELECT temperature, humidity, door_status, ventilation_status, buzzer_status, latitude, longitude
                    FROM rack_data 
                    WHERE id=? 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                    """,
                    (rack_id,),
                    fetchone=True
                )

                if row:
                    temp, hum, door, vent, buzz, lat, lon = row
                    # Update cache with DB data
                    state = self.ensure_rack_state(rack_id)
                    if temp is not None:
                        state['temperature'] = temp
                        state['temperature_history'].append(temp)
                    if hum is not None:
                        state['humidity'] = hum
                        state['humidity_history'].append(hum)
                    if door is not None:
                        state['door_status'] = door
                    if vent is not None:
                        state['ventilation_status'] = vent
                    if buzz is not None:
                        state['buzzer_status'] = buzz
                    if lat is not None:
                        state['latitude'] = lat
                    if lon is not None:
                        state['longitude'] = lon
                    
                    # Sync Rack object with state
                    self.syncRackFromState(self.currentRack, state)

                # Update UI
                self.update_ui_from_state(rack_id)

            except Exception as e:
                print(f"[DB/Error] ❌ Error loading rack data: {e}")
                import traceback
                traceback.print_exc()

    def reset_dashboard_metrics(self):
        """Clear gauge readings and chart series before showing another rack."""
        try:
            # Reset map hash cache to force update on new rack selection
            self._last_map_hash = None
            
            # Reset door status label to neutral state
            if hasattr(self, 'door_status_label'):
                self.door_status_label.setText("🚪 Status: --")
                self.door_status_label.setStyleSheet(
                    """
                    QLabel {
                        color: white;
                        font-size: 16px;
                        padding: 10px 20px;
                        background-color: #7f8c8d;
                        border-radius: 5px;
                    }
                    """
                )

            # Reset temperature gauge and chart
            if hasattr(self, 'temp_gauge') and self.temp_gauge is not None:
                self.temp_gauge.setValue(self.temp_gauge.minValue)
            if hasattr(self, 'temp_value_label') and self.temp_value_label is not None:
                self.temp_value_label.setText("-- °C")
            if self.temp_series is not None:
                self.temp_series.clear()
            if self.temp_axis_x is not None:
                self.temp_axis_x.setRange(0, self.history_limit)
                self.temp_axis_x.setTickCount(6)
            if self.temp_axis_y is not None:
                self.temp_axis_y.setRange(0, 100)

            # Reset humidity gauge and chart
            if hasattr(self, 'hum_gauge') and self.hum_gauge is not None:
                self.hum_gauge.setValue(self.hum_gauge.minValue)
            if hasattr(self, 'hum_value_label') and self.hum_value_label is not None:
                self.hum_value_label.setText("-- %")
            if self.hum_series is not None:
                self.hum_series.clear()
            if self.hum_axis_x is not None:
                self.hum_axis_x.setRange(0, self.history_limit)
                self.hum_axis_x.setTickCount(6)
            if self.hum_axis_y is not None:
                self.hum_axis_y.setRange(0, 100)

        except Exception as e:
            print(f"[UI/Error] ❌ Error resetting dashboard metrics: {e}")

    def execute_db(self, query, params=None, *, fetchone=False, fetchall=False, commit=False):
        """Thread-safe helper to execute database statements"""
        with self.db_lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute(query, params or ())
                result = None
                if fetchone:
                    result = cursor.fetchone()
                elif fetchall:
                    result = cursor.fetchall()
                if commit:
                    self.conn.commit()
                return result
            finally:
                cursor.close()
    
    def closeEvent(self, event):
        """Handle application close event - cleanup resources"""
        print("[App/Shutdown] 🛑 Shutting down application...")
        try:
            # Stop AI analysis timer
            if hasattr(self, 'aiAnalysisTimer'):
                self.aiAnalysisTimer.stop()
            
            # Stop Tool Calling Service
            if hasattr(self, 'toolCallingService') and self.toolCallingService:
                self.toolCallingService.stop()
                print("[ToolCalling/Stop] 🤖 Tool Calling Service stopped")
            
            # Stop Forecast Service
            if hasattr(self, 'forecastService') and self.forecastService:
                self.forecastService.stop()
                print("[Forecast/Stop] 📊 Forecast Service stopped")
            
            # Stop all blinking timers
            for rackId, timer in list(self.blinkingRacks.items()):
                timer.stop()
            self.blinkingRacks.clear()
            
            # Stop MQTT client
            if hasattr(self, 'client'):
                self.client.loop_stop()
                self.client.disconnect()
                print("[MQTT/Disconnect] 🔌 MQTT client disconnected")
            
            # Close database connection
            if hasattr(self, 'conn'):
                self.conn.close()
                print("[DB/Close] 💾 Database connection closed")
        except Exception as e:
            print(f"[App/Error] ❌ Error during cleanup: {e}")
        finally:
            event.accept()

    def initializeForecastService(self):
        """
        Inicializa o ForecastService para previsão de séries temporais.
        
        Implementa arquitetura híbrida:
        - Modelo Principal: IBM Granite TTM-R2
        - Fallback: SARIMA quando Granite não disponível ou MAE elevado
        
        A troca automática é baseada na métrica MAE (Mean Absolute Error).
        """
        try:
            # Configurações do forecast
            forecastHorizon = int(os.getenv("FORECAST_HORIZON", str(self.forecast_horizon)))
            contextLength = int(os.getenv("FORECAST_CONTEXT_LENGTH", "512"))
            
            # Inicializa o serviço
            self.forecastService = ForecastService(
                forecast_horizon=forecastHorizon,
                context_length=contextLength
            )
            
            # Log do status do modelo
            modelInfo = self.forecastService.get_model_info()
            if modelInfo.get('using_granite'):
                print(f"[Forecast/Init] ✅ ForecastService usando IBM Granite TTM-R2")
            else:
                print(f"[Forecast/Init] 📊 ForecastService usando SARIMA (fallback)")
            
            print(f"[Forecast/Init] 🎚️ MAE Threshold: {modelInfo.get('mae_threshold', 'N/A')}")
            
        except Exception as e:
            print(f"[Forecast/Error] ❌ Erro ao inicializar ForecastService: {e}")
            import traceback
            traceback.print_exc()
            self.forecastService = None

    def initializeToolCallingService(self):
        """
        Inicializa o ToolCallingService para controle AI-driven dos racks.
        
        Carrega configurações do .env e configura o serviço com callbacks apropriados.
        """
        try:
            # Obtém configurações do .env
            apiKey = os.getenv("GENAI_API_KEY")
            model = os.getenv("GENAI_MODEL", "granite4:3b")
            serverUrl = os.getenv("GENAI_URL", "generativa.rapport.tec.br/api/v1")
            
            # Garante o protocolo https://
            if serverUrl and not serverUrl.startswith("http"):
                serverUrl = f"https://{serverUrl}"
            
            if not apiKey:
                print("[ToolCalling/Warning] ⚠️ GENAI_API_KEY não configurada - AI control desabilitado")
                self.toolCallingService = None
                return
            
            # Inicializa o serviço
            self.toolCallingService = ToolCallingService(
                apiKey=apiKey,
                model=model,
                llmServerUrl=serverUrl,
                analysisInterval=5.0  # 5 segundos entre análises
            )
            
            # Injeta o RackControlService
            self.toolCallingService.setRackControlService(self.rackControlService)
            
            # Configura o callback para piscar racks na UI
            self.toolCallingService.setActionCallback(self.onRackActionCallback)
            
            # Configura o callback para atualizar a barra de status
            self.toolCallingService.setStatusCallback(self.onStatusCallback)
            
            print(f"[ToolCalling/Init] ✅ ToolCallingService inicializado com modelo {model}")
            print(f"[ToolCalling/Init] 🎚️ Thresholds carregados do .env")
            
        except Exception as e:
            print(f"[ToolCalling/Error] ❌ Erro ao inicializar ToolCallingService: {e}")
            import traceback
            traceback.print_exc()
            self.toolCallingService = None

    def runAiAnalysis(self):
        """
        Executa a análise AI periódica para determinar ações de controle.
        
        Este método é chamado pelo timer aiAnalysisTimer.
        """
        if not self.toolCallingService:
            return
        
        try:
            # Executa análise e ações
            executedActions = self.toolCallingService.analyzeAndExecute(self.racks)
            
            if executedActions:
                print(f"[AI/Analysis] 🤖 {len(executedActions)} ação(ões) executada(s)")
                for action in executedActions:
                    print(f"  └─ {action.function} em {action.rackId}: {action.reason}")
                    
        except Exception as e:
            print(f"[AI/Error] ❌ Erro na análise AI: {e}")

    def onRackActionCallback(self, rackId: str, action: str):
        """
        Callback chamado quando uma ação AI é executada em um rack.
        
        Emite um signal para garantir que a atualização da UI seja feita
        no thread principal.
        
        Args:
            rackId: ID do rack onde a ação está sendo executada
            action: Nome da ação sendo executada
        """
        print(f"[UI/Action] ⚡ Ação AI: {action} em Rack {rackId}")
        
        # Emite signal para atualização thread-safe da UI
        self.action_executed.emit(rackId, action)

    def onStatusCallback(self, rackId: str, action: str, reason: str):
        """
        Callback chamado para atualizar a barra de status.
        
        Emite um signal para garantir thread-safety na UI.
        
        Args:
            rackId: ID do rack
            action: Nome da ação executada
            reason: Motivo da ação
        """
        # Emite signal para atualização thread-safe da UI
        self.status_updated.emit(rackId, action, reason)

    def handleActionExecuted(self, rackId: str, action: str):
        """
        Handler para o signal action_executed.
        
        Executa no thread principal da UI para fazer o rack piscar.
        
        Args:
            rackId: ID do rack onde a ação foi executada
            action: Nome da ação executada
        """
        # Ações de alerta usam fundo vermelho
        alertActions = {
            'activateCriticalTemperatureAlert',
            'activateDoorOpenAlert',
            'activateBreakInAlert'
        }
        isAlert = action in alertActions
        self.blinkRackItem(rackId, isAlert=isAlert)

    def handleStatusUpdate(self, rackId: str, action: str, reason: str):
        """
        Handler para o signal status_updated.
        
        Atualiza a barra de status com informações da ação executada.
        
        Args:
            rackId: ID do rack
            action: Nome da ação executada
            reason: Motivo da ação
        """
        # Mapeamento de nomes de ação para texto legível
        actionNames = {
            'turnOnVentilation': '💨 Ligar Ventilação',
            'turnOffVentilation': '💨 Desligar Ventilação',
            'activateCriticalTemperatureAlert': '🔥 Alerta Temperatura Crítica',
            'deactivateCriticalTemperatureAlert': '✅ Desativar Alerta Temp.',
            'activateDoorOpenAlert': '🚪 Alerta Porta Aberta',
            'activateBreakInAlert': '🚨 Alerta Arrombamento',
            'silenceBuzzer': '🔕 Silenciar Buzzer',
            'openDoor': '🚪 Abrir Porta',
            'closeDoor': '🚪 Fechar Porta'
        }
        
        actionText = actionNames.get(action, action)
        
        # Atualiza status icon
        self.status_icon.setText("▶️")
        self.status_icon.setStyleSheet("""
            QLabel {
                color: #27ae60;
                font-size: 16px;
            }
        """)
        
        # Atualiza mensagem de status
        self.status_message.setText(f"Rack {rackId}: {actionText}")
        self.status_message.setStyleSheet("""
            QLabel {
                color: #27ae60;
                font-size: 13px;
                font-weight: bold;
            }
        """)
        
        # Atualiza última ação
        self.last_action_label.setText(f"📝 {reason}")
        
        # Timer para resetar o status após 5 segundos
        QTimer.singleShot(5000, self.resetStatusBar)

    def resetStatusBar(self):
        """Reseta a barra de status para o estado normal."""
        if hasattr(self, 'status_icon'):
            self.status_icon.setText("⏸️")
            self.status_icon.setStyleSheet("""
                QLabel {
                    color: #95a5a6;
                    font-size: 16px;
                }
            """)
        
        if hasattr(self, 'status_message'):
            self.status_message.setText("Monitorando...")
            self.status_message.setStyleSheet("""
                QLabel {
                    color: #ecf0f1;
                    font-size: 13px;
                }
            """)

    def blinkRackItem(self, rackId: str, duration: int = 2000, interval: int = 200, isAlert: bool = False):
        """
        Faz um item de rack piscar no painel esquerdo.
        
        Args:
            rackId: ID do rack para piscar
            duration: Duração total do efeito de piscar em ms (default: 2000)
            interval: Intervalo entre piscadas em ms (default: 200)
            isAlert: Se True, usa fundo vermelho para indicar alerta (default: False)
        """
        try:
            # Encontra o item na lista
            itemText = f"Rack {rackId}"
            items = self.list_widget.findItems(itemText, Qt.MatchExactly)
            
            if not items:
                print(f"[UI/Blink] ⚠️ Rack não encontrado na lista: {rackId}")
                return
            
            item = items[0]
            
            # Se já está piscando, para o timer anterior
            if rackId in self.blinkingRacks:
                self.blinkingRacks[rackId].stop()
            
            # Cores para piscar - vermelho para alertas, laranja para outras ações
            normalBg = "#34495e"
            highlightBg = "#e74c3c" if isAlert else "#f39c12"  # Vermelho para alerta, laranja para destaque
            blinkState = {"on": False, "count": 0}
            maxBlinks = duration // interval
            
            def toggleBlink():
                blinkState["count"] += 1
                
                if blinkState["count"] >= maxBlinks:
                    # Finaliza a piscagem
                    self.stopBlinkingRackItem(rackId)
                    return
                
                blinkState["on"] = not blinkState["on"]
                
                if blinkState["on"]:
                    item.setBackground(QColor(highlightBg))
                    item.setForeground(QColor("#2c3e50"))  # Texto escuro
                else:
                    item.setBackground(QColor(normalBg))
                    item.setForeground(QColor("white"))
            
            # Cria e inicia o timer
            blinkTimer = QTimer(self)
            blinkTimer.setInterval(interval)
            blinkTimer.timeout.connect(toggleBlink)
            blinkTimer.start()
            
            self.blinkingRacks[rackId] = blinkTimer
            
            # Inicia com o primeiro toggle
            toggleBlink()
            
        except Exception as e:
            print(f"[UI/Blink] ❌ Erro ao piscar rack: {e}")

    def stopBlinkingRackItem(self, rackId: str):
        """
        Para o efeito de piscar de um rack.
        
        Args:
            rackId: ID do rack para parar de piscar
        """
        try:
            if rackId in self.blinkingRacks:
                self.blinkingRacks[rackId].stop()
                del self.blinkingRacks[rackId]
            
            # Restaura a cor normal do item
            itemText = f"Rack {rackId}"
            items = self.list_widget.findItems(itemText, Qt.MatchExactly)
            
            if items:
                item = items[0]
                item.setBackground(QColor("#34495e"))
                item.setForeground(QColor("white"))
                
        except Exception as e:
            print(f"[UI/Blink] ❌ Erro ao parar piscagem: {e}")

    def checkExpiredCommands(self):
        """
        Verifica comandos pendentes que expiraram (não receberam ACK a tempo).
        
        Comandos expirados são removidos da lista de pendentes e o usuário
        é notificado via log. A UI não é atualizada para comandos expirados
        (mantém o estado anterior).
        """
        if not self.rackControlService:
            return
        
        expired = self.rackControlService.getExpiredCommands()
        
        for cmd in expired:
            print(f"[UI/Timeout] ⏱️ Comando expirado: {cmd.commandType}={cmd.value} para rack {cmd.rackId}")
            
            # Notifica o usuário sobre o timeout
            timeoutSeconds = self.rackControlService.commandTimeout
            print(f"[UI/Timeout] ⚠️ Firmware não confirmou em {timeoutSeconds}s - comando pode não ter sido executado")

if __name__ == "__main__":
    try:
        print("[App/Start] 🚀 Starting Rack Inteligente Dashboard...")
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        print("[App/Ready] ✅ Dashboard is ready!")
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        print("\n[App/Interrupt] ⚠️  Application interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"[App/Fatal] ❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
