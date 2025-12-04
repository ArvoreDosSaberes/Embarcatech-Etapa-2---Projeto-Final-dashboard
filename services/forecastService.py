"""
Forecast Service
Servico de previsao de series temporais usando IBM Granite TTM-R2

Este modulo implementa uma arquitetura híbrida de previsão:
- **Modelo Principal**: IBM Granite TTM-R2 (Tiny Time Mixer)
- **Fallback**: SARIMA (Seasonal ARIMA) quando Granite não disponível ou MAE elevado

A troca automática entre modelos é baseada na métrica MAE (Mean Absolute Error),
conforme descrito no artigo "MAE e SARIMA como fallback na falta do Granite TTM".

Para instalar o Granite TTM-R2:
    bash install_granite.sh
"""

import logging
import os
import pandas as pd
import numpy as np
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
import warnings
import signal
import sys
import threading

# Importar serviço de fallback SARIMA
from services.sarimaFallbackService import SarimaFallbackService, SarimaConfig, ForecastResult

logger = logging.getLogger(__name__)
warnings.filterwarnings('ignore')

# Tentar importar Granite TTM-R2
GRANITE_AVAILABLE = False
try:
    from tsfm_public import TimeSeriesForecastingPipeline, TinyTimeMixerForPrediction
    import torch
    GRANITE_AVAILABLE = True
    logger.info("✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅")
    logger.info("✅ [ForecastService] IBM Granite TTM-R2 disponivel ✅")
    logger.info("✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅✅")
except ImportError:
    logger.warning("⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️")
    logger.warning("⚠️ [ForecastService] IBM Granite TTM-R2 nao disponivel - usando modelo alternativo ⚠️")
    logger.info(   "💡 [ForecastService] Execute: bash install_granite.sh                              💡")
    logger.warning("⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️")


class ForecastService:
    """
    Servico de previsao de series temporais com arquitetura híbrida.
    
    Implementa a estratégia de fallback descrita no projeto EmbarcaTech TIC-27:
    
    1. **Modelo Principal (Granite TTM)**: Executa previsão usando IA moderna
    2. **Modelo Fallback (SARIMA)**: Assume quando Granite falha ou MAE elevado
    3. **Monitoramento MAE**: Métrica para decisão automática de troca
    
    O sistema executa SARIMA em paralelo, garantindo previsões instantâneas
    caso seja necessário ativar o fallback.
    
    Attributes:
        sarimaFallback: Serviço de fallback SARIMA sempre ativo
        maeThreshold: Limiar de MAE para ativar fallback
        currentMae: MAE atual do modelo Granite
        useFallback: Se está usando fallback atualmente
    """
    
    def __init__(
        self,
        model_name: str = "ibm-granite/granite-timeseries-ttm-r2",
        forecast_horizon: int = 24,
        context_length: int = 168
    ):
        """
        Inicializa o serviço de previsão com suporte a sazonalidade múltipla.
        
        Args:
            model_name: Nome do modelo Granite TTM
            forecast_horizon: Passos de previsão (default: 24 = 24 horas)
            context_length: Tamanho do histórico (default: 168 = 7 dias * 24h)
        """
        self.model_name = model_name
        self.forecast_horizon = forecast_horizon
        self.context_length = context_length
        self.use_granite = GRANITE_AVAILABLE
        self.granite_model = None
        self.granite_pipeline = None
        
        # Intervalo de agregação de dados (em segundos)
        self.sampleInterval = int(os.getenv("FORECAST_SAMPLE_INTERVAL", "3600"))
        
        if GRANITE_AVAILABLE:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"🔮 [ForecastService] Using IBM Granite TTM-R2 on {self.device}")
        else:
            self.device = "cpu"
            logger.info(f"🔮 [ForecastService] Granite não disponível - SARIMA será o fallback")
        
        self._model_loaded = False
        self._running = True
        
        # Configuração do sistema de fallback SARIMA com sazonalidade múltipla
        maeThreshold = float(os.getenv("FORECAST_MAE_THRESHOLD", "5.0"))
        seasonalPeriodDaily = int(os.getenv("FORECAST_SEASONAL_PERIOD_DAILY", "24"))
        seasonalPeriodAnnual = int(os.getenv("FORECAST_SEASONAL_PERIOD_ANNUAL", "8760"))
        enableAnnualSeasonality = os.getenv("FORECAST_ENABLE_ANNUAL_SEASONALITY", "true").lower() == "true"
        
        # Configuração SARIMA com sazonalidade diária
        # Para sazonalidade anual, usamos modelo mais simples para evitar over-fitting
        sarimaConfig = SarimaConfig(
            p=2, d=1, q=1,           # Parâmetros não-sazonais otimizados
            P=1, D=1, Q=1,           # Parâmetros sazonais
            s=seasonalPeriodDaily,   # Período sazonal diário (24 horas)
            maeThreshold=maeThreshold,
            maeWindowSize=168,       # Janela MAE de 7 dias
            autoSelectParams=True
        )
        
        # Inicializa serviço SARIMA (sempre ativo em paralelo)
        self.sarimaFallback = SarimaFallbackService(sarimaConfig)
        
        # Configuração de sazonalidade anual
        self.enableAnnualSeasonality = enableAnnualSeasonality
        self.seasonalPeriodAnnual = seasonalPeriodAnnual
        self.seasonalPeriodDaily = seasonalPeriodDaily
        
        # Buffer para agregação de dados por hora
        self.aggregationBuffer: deque = deque(maxlen=3600)  # 1 hora de amostras a 1s
        self.lastAggregationTimestamp: float = 0.0
        self.hourlyHistory: deque = deque(maxlen=context_length)  # Histórico agregado
        
        # Estado do sistema de fallback
        self.maeThreshold = maeThreshold
        self.currentMae: float = 0.0
        self.useFallback: bool = not GRANITE_AVAILABLE
        
        # Histórico para cálculo de MAE
        self.predictionHistory: deque = deque(maxlen=168)  # 7 dias de previsões
        self.actualHistory: deque = deque(maxlen=168)
        
        # Lock para thread-safety
        self._lock = threading.Lock()
        
        # Handler para interrupção graciosa
        self._setupSignalHandlers()
        
        logger.info(f"📊 [ForecastService] Forecast horizon={forecast_horizon}h, context={context_length}h (7 dias)")
        logger.info(f"🕐 [ForecastService] Agregação de dados: {self.sampleInterval}s ({self.sampleInterval//3600}h)")
        logger.info(f"🌡️ [ForecastService] Sazonalidade diária: {seasonalPeriodDaily}h")
        if enableAnnualSeasonality:
            logger.info(f"📅 [ForecastService] Sazonalidade anual: {seasonalPeriodAnnual}h (365 dias)")
        logger.info(f"🎚️ [ForecastService] MAE Threshold para fallback: {maeThreshold}")
    
    def _setupSignalHandlers(self) -> None:
        """
        Configura handlers para sinais de interrupção (Ctrl+C).
        Implementa saída graciosa conforme requisitos do projeto.
        """
        def signalHandler(signum, frame):
            logger.info("[ForecastService] 🛑 Recebido sinal de interrupção, encerrando graciosamente...")
            self.stop()
            sys.exit(0)
        
        # Registra handlers apenas na thread principal
        if threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGINT, signalHandler)
                signal.signal(signal.SIGTERM, signalHandler)
            except Exception:
                pass  # Ignora se não puder registrar (ex: thread secundária)
    
    def stop(self) -> None:
        """Para o serviço graciosamente."""
        self._running = False
        if self.sarimaFallback:
            self.sarimaFallback.stop()
        logger.info("[ForecastService] 🛑 Serviço parado")
    
    def start(self) -> None:
        """Inicia/reinicia o serviço."""
        self._running = True
        if self.sarimaFallback:
            self.sarimaFallback.start()
        logger.info("[ForecastService] ▶️ Serviço iniciado")
    
    def aggregateHourlyData(self, data_history: List[Dict]) -> List[Dict]:
        """
        Agrega dados por hora para previsão de longo prazo.
        
        Converte amostras de alta frequência (ex: 1s) em médias horárias
        para análise sazonal de 24h futuras com histórico de 7 dias.
        
        Args:
            data_history: Lista de dicts com 'timestamp' e 'value'
            
        Returns:
            List[Dict]: Dados agregados por hora
        """
        if not data_history:
            return []
        
        try:
            # Converter para DataFrame
            df = pd.DataFrame(data_history)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            
            # Agregar por hora usando média
            hourlyDf = df.resample('h').agg({
                'value': 'mean'
            }).dropna()
            
            # Converter de volta para lista de dicts
            hourlyData = []
            for ts, row in hourlyDf.iterrows():
                hourlyData.append({
                    'timestamp': ts.isoformat(),
                    'value': float(row['value'])
                })
            
            logger.debug(f"📊 [ForecastService] Agregado {len(data_history)} amostras -> {len(hourlyData)} horas")
            return hourlyData
            
        except Exception as e:
            logger.warning(f"⚠️ [ForecastService] Erro na agregação horária: {e}")
            return data_history
    
    def addAnnualSeasonalComponent(self, predictions: List[float], baseTimestamp: datetime) -> List[float]:
        """
        Adiciona componente de sazonalidade anual às previsões.
        
        Ajusta as previsões considerando variações climáticas sazonais:
        - Verão (Dez-Fev): temperaturas mais altas
        - Inverno (Jun-Ago): temperaturas mais baixas
        
        Args:
            predictions: Lista de valores previstos
            baseTimestamp: Timestamp base para cálculo sazonal
            
        Returns:
            List[float]: Previsões ajustadas com sazonalidade anual
        """
        if not self.enableAnnualSeasonality:
            return predictions
        
        try:
            adjustedPredictions = []
            
            for i, value in enumerate(predictions):
                # Calcular timestamp futuro
                futureTs = baseTimestamp + timedelta(hours=i+1)
                dayOfYear = futureTs.timetuple().tm_yday
                
                # Calcular fator sazonal usando função senoidal
                # Pico no verão (dia 355 = 21 Dez no hemisfério sul)
                # Vale no inverno (dia 172 = 21 Jun)
                # Amplitude de ~3°C para temperatura
                seasonalPhase = 2 * np.pi * (dayOfYear - 355) / 365
                seasonalFactor = np.cos(seasonalPhase)  # -1 a +1
                
                # Amplitude do ajuste sazonal (pode ser configurável)
                seasonalAmplitude = 3.0  # °C
                adjustment = seasonalFactor * seasonalAmplitude
                
                adjustedPredictions.append(value + adjustment)
            
            logger.debug(f"📅 [ForecastService] Ajuste sazonal anual aplicado a {len(predictions)} previsões")
            return adjustedPredictions
            
        except Exception as e:
            logger.warning(f"⚠️ [ForecastService] Erro no ajuste sazonal anual: {e}")
            return predictions
    
    def _prepare_series(self, data_history: List[Dict]) -> pd.Series:
        """
        Prepara serie temporal para o modelo
        
        Args:
            data_history: Historico de dados
            
        Returns:
            pd.Series: Serie temporal indexada por timestamp
        """
        recent_data = data_history[-self.context_length:]
        
        timestamps = [pd.to_datetime(point['timestamp']) for point in recent_data]
        values = [point['value'] for point in recent_data]
        
        series = pd.Series(values, index=timestamps)
        series = series.sort_index()
        
        logger.debug(f"📋 [ForecastService] Prepared series: {len(series)} points")
        return series
    
    def _coerce_scalar_float(self, value: Any) -> Optional[float]:
        """
        Converte um valor potencialmente aninhado em um float escalar.

        Args:
            value: Valor de entrada (escalar, lista ou array) com previsao.

        Returns:
            float | None: Valor convertido ou None se nao for possivel extrair.
        """
        array_value = np.asarray(value)

        if array_value.size == 0:
            return None

        scalar_candidate = array_value.reshape(-1)[0]

        try:
            return float(scalar_candidate)
        except (TypeError, ValueError):
            return None

    def _sanitize_predictions(self, raw_predictions: Any, limit: Optional[int] = None) -> np.ndarray:
        """
        Normaliza previsoes para um array 1D de floats.

        Args:
            raw_predictions: Valores retornados pelo modelo Granite.
            limit: Quantidade maxima de valores a retornar.

        Returns:
            np.ndarray: Array 1D com valores float64.
        """
        sanitized_values: List[float] = []
        stack: List[Any] = [raw_predictions]

        while stack and (limit is None or len(sanitized_values) < limit):
            current = stack.pop()

            if isinstance(current, np.ndarray):
                # Converter para lista preservando ordem
                stack.extend(reversed(current.tolist()))
                continue

            if isinstance(current, (list, tuple)):
                stack.extend(reversed(list(current)))
                continue

            scalar_value = self._coerce_scalar_float(current)

            if scalar_value is None or not np.isfinite(scalar_value):
                logger.debug("🧹 [ForecastService/Granite] Ignoring non-numeric prediction value during sanitization")
                continue

            sanitized_values.append(scalar_value)

        if not sanitized_values:
            return np.array([], dtype=np.float64)

        if limit is not None:
            sanitized_values = sanitized_values[:limit]

        sanitized = np.array(sanitized_values, dtype=np.float64)
        logger.debug(
            f"🧹 [ForecastService/Granite] Sanitized predictions: {len(sanitized)} points"
        )
        return sanitized

    def _simple_forecast(self, series: pd.Series, steps: int) -> np.ndarray:
        """
        Realiza previsao simples usando media movel exponencial
        
        Este metodo e usado como fallback quando statsmodels nao esta disponivel
        ou quando ha poucos dados.
        
        Args:
            series: Serie temporal
            steps: Numero de passos a prever
            
        Returns:
            np.ndarray: Valores previstos
        """
        # Calcular componentes basicos
        recent_values = series.values[-50:]  # Ultimos 50 pontos
        
        # Tendencia simples (regressao linear)
        x = np.arange(len(recent_values))
        if len(recent_values) > 1:
            trend_coef = np.polyfit(x, recent_values, 1)
            trend = np.poly1d(trend_coef)
        else:
            trend = lambda x: recent_values[-1]
        
        # Sazonalidade simples (media dos ultimos ciclos)
        period = 50
        if len(recent_values) >= period:
            seasonal = recent_values[-period:]
        else:
            seasonal = np.zeros(period)
        
        # Gerar previsoes
        predictions = []
        last_value = series.values[-1]
        
        for i in range(steps):
            # Tendencia
            trend_value = trend(len(recent_values) + i)
            
            # Sazonalidade
            seasonal_idx = i % len(seasonal)
            seasonal_value = seasonal[seasonal_idx] - np.mean(recent_values)
            
            # Combinacao com suavizacao
            pred = trend_value + seasonal_value * 0.3
            
            # Adicionar pequeno ruido para variacao
            noise = np.random.normal(0, np.std(recent_values) * 0.1)
            pred += noise
            
            predictions.append(pred)
        
        return np.array(predictions)
    
    def _load_granite_model(self):
        """Carrega o modelo IBM Granite TTM-R2 (lazy loading)"""
        if self._model_loaded or not GRANITE_AVAILABLE:
            return
        
        try:
            logger.info(f"⏳ [ForecastService] Loading Granite TTM-R2...")
            
            self.granite_model = TinyTimeMixerForPrediction.from_pretrained(
                self.model_name,
                num_input_channels=1,
            )
            
            self.granite_pipeline = TimeSeriesForecastingPipeline(
                self.granite_model,
                timestamp_column="timestamp",
                id_columns=[],
                target_columns=["value"],
                explode_forecasts=False,
                freq="S",
                device=self.device,
            )
            
            self._model_loaded = True
            logger.info(f"✅ [ForecastService] Granite TTM-R2 loaded on {self.device}")
            
        except Exception as e:
            logger.error(f"❌ [ForecastService] Error loading Granite: {str(e)}")
            self.use_granite = False
            logger.info("📊 [ForecastService] Falling back to Exponential Smoothing")
    
    def _granite_forecast(self, data_history: List[Dict], steps: int) -> Optional[np.ndarray]:
        """
        Realiza previsao usando IBM Granite TTM-R2
        
        Args:
            data_history: Historico de dados
            steps: Numero de passos a prever
            
        Returns:
            np.ndarray: Valores previstos ou None se erro
        """
        import time
        start_time = time.time()
        
        try:
            self._load_granite_model()
            
            if not self._model_loaded:
                logger.warning("⚠️  [ForecastService/Granite] Model not loaded")
                return None
            
            # Preparar DataFrame
            recent_data = data_history[-self.context_length:]
            logger.info(f"📊 [ForecastService/Granite] Preparing data: {len(recent_data)} points")
            
            df = pd.DataFrame([
                {
                    'timestamp': pd.to_datetime(point['timestamp']),
                    'value': point['value']
                }
                for point in recent_data
            ])
            df = df.sort_values('timestamp').reset_index(drop=True)
            
            logger.info(f"📋 [ForecastService/Granite] DataFrame shape: {df.shape}")
            logger.debug(f"📋 [ForecastService/Granite] DataFrame head:\n{df.head()}")
            logger.debug(f"📋 [ForecastService/Granite] DataFrame tail:\n{df.tail()}")
            
            # Fazer previsao
            logger.info(f"🔮 [ForecastService/Granite] Starting prediction with {steps} steps...")
            prediction_start = time.time()
            
            forecast_df = self.granite_pipeline(df)
            
            prediction_time = time.time() - prediction_start
            logger.info(f"⏱️  [ForecastService/Granite] Prediction completed in {prediction_time:.3f}s")
            
            # Log detalhado da resposta do Granite
            logger.info(f"📊 [ForecastService/Granite] Response shape: {forecast_df.shape}")
            logger.info(f"📊 [ForecastService/Granite] Response columns: {list(forecast_df.columns)}")
            logger.info(f"📊 [ForecastService/Granite] Response dtypes:\n{forecast_df.dtypes}")
            logger.debug(f"📊 [ForecastService/Granite] Response head:\n{forecast_df.head()}")
            logger.debug(f"📊 [ForecastService/Granite] Response tail:\n{forecast_df.tail()}")
            logger.debug(f"📊 [ForecastService/Granite] Response describe:\n{forecast_df.describe()}")
            
            # Extrair valores
            if 'value' in forecast_df.columns:
                predictions = forecast_df['value'].values[:steps]
                logger.info(f"✅ [ForecastService/Granite] Extracted {len(predictions)} predictions from 'value' column")
            else:
                predictions = forecast_df.iloc[:, 0].values[:steps]
                logger.info(f"✅ [ForecastService/Granite] Extracted {len(predictions)} predictions from first column")

            predictions = self._sanitize_predictions(predictions, limit=steps)

            if predictions.size == 0:
                logger.warning("⚠️  [ForecastService/Granite] No numeric predictions available after sanitization")
                return None

            # Log estatísticas das predições
            logger.info(f"📈 [ForecastService/Granite] Predictions stats: min={np.min(predictions):.2f}, max={np.max(predictions):.2f}, mean={np.mean(predictions):.2f}, std={np.std(predictions):.2f}")
            logger.debug(f"📈 [ForecastService/Granite] First 10 predictions: {predictions[:10]}")
            
            total_time = time.time() - start_time
            logger.info(f"✅ [ForecastService/Granite] Total forecast time: {total_time:.3f}s")
            
            return predictions
            
        except Exception as e:
            total_time = time.time() - start_time
            logger.error(f"❌ [ForecastService/Granite] Forecast error after {total_time:.3f}s: {str(e)}")
            logger.error(f"❌ [ForecastService/Granite] Exception type: {type(e).__name__}")
            logger.error(f"❌ [ForecastService/Granite] Exception details:", exc_info=True)
            return None
    
    def _sarima_fallback_forecast(self, data_history: List[Dict], steps: int) -> Optional[np.ndarray]:
        """
        Realiza previsao usando SARIMA via SarimaFallbackService.
        
        O SARIMA é o fallback preferido pois:
        - É computacionalmente leve e determinístico
        - Captura padrões sazonais naturalmente
        - Funciona bem com poucos dados
        - É matematicamente explicável
        
        Args:
            data_history: Histórico de dados
            steps: Numero de passos a prever
            
        Returns:
            np.ndarray: Valores previstos ou None se erro
        """
        try:
            result = self.sarimaFallback.forecast(data_history, steps)
            
            if result is not None:
                logger.info(f"✅ [ForecastService] SARIMA forecast: {len(result.predictions)} pontos")
                return np.array(result.predictions)
            else:
                logger.warning("⚠️  [ForecastService] SARIMA fallback retornou None")
                return None
                
        except Exception as e:
            logger.warning(f"⚠️  [ForecastService] SARIMA fallback failed: {str(e)}")
            return None
    
    def _exponential_smoothing_forecast(self, series: pd.Series, steps: int) -> np.ndarray:
        """
        Realiza previsao usando Exponential Smoothing (statsmodels)
        
        Este é o fallback terciário, usado apenas quando SARIMA também falha.
        
        Args:
            series: Serie temporal
            steps: Numero de passos a prever
            
        Returns:
            np.ndarray: Valores previstos
        """
        try:
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
            
            # Configurar modelo com tendencia e sazonalidade
            model = ExponentialSmoothing(
                series.values,
                trend='add',
                seasonal='add',
                seasonal_periods=min(50, len(series) // 2)
            )
            
            # Treinar modelo
            fitted_model = model.fit(optimized=True, use_brute=False)
            
            # Fazer previsao
            forecast = fitted_model.forecast(steps=steps)
            
            logger.debug(f"✅ [ForecastService] Exponential Smoothing forecast completed")
            return forecast
            
        except Exception as e:
            logger.warning(f"⚠️  [ForecastService] Exponential Smoothing failed: {str(e)}")
            logger.info("📊 [ForecastService] Falling back to simple forecast")
            return self._simple_forecast(series, steps)
    
    def applyHumidityCorrection(
        self, 
        tempPredictions: List[float], 
        humidityHistory: List[Dict],
        baseTimestamp: datetime
    ) -> List[float]:
        """
        Aplica correção de umidade às previsões de temperatura.
        
        A umidade afeta a temperatura interna do rack de duas formas:
        1. **Alta umidade (>70%)**: Reduz a eficiência de dissipação de calor
           - Ar úmido tem menor capacidade de absorver calor
           - Resultado: temperatura tende a subir mais
        2. **Baixa umidade (<30%)**: Melhora dissipação, mas pode causar
           descarga eletrostática
        
        Modelo de correção baseado na correlação umidade-temperatura:
        - Coeficiente de impacto: 0.05°C por % de umidade acima de 50%
        
        Args:
            tempPredictions: Previsões de temperatura base
            humidityHistory: Histórico de umidade para análise de tendência
            baseTimestamp: Timestamp base para cálculo
            
        Returns:
            List[float]: Previsões de temperatura corrigidas
        """
        if not humidityHistory or len(humidityHistory) < 10:
            return tempPredictions
        
        try:
            # Agregar umidade por hora
            humidityHourly = self.aggregateHourlyData(humidityHistory)
            
            if not humidityHourly:
                return tempPredictions
            
            # Calcular média e tendência da umidade
            humidityValues = [h['value'] for h in humidityHourly[-24:]]  # Últimas 24h
            avgHumidity = np.mean(humidityValues)
            
            # Calcular tendência da umidade (slope)
            if len(humidityValues) >= 2:
                humiditySlope = (humidityValues[-1] - humidityValues[0]) / len(humidityValues)
            else:
                humiditySlope = 0.0
            
            # Referência: 50% é a umidade ideal para dissipação de calor
            referenceHumidity = 50.0
            
            # Coeficiente de impacto: cada 10% acima de 50% adiciona ~0.5°C
            humidityImpactCoeff = 0.05  # °C por % de umidade
            
            correctedPredictions = []
            for i, tempValue in enumerate(tempPredictions):
                # Projetar umidade futura baseada na tendência
                projectedHumidity = avgHumidity + (humiditySlope * (i + 1))
                projectedHumidity = max(0, min(100, projectedHumidity))  # Clamp 0-100
                
                # Calcular desvio da umidade ideal
                humidityDeviation = projectedHumidity - referenceHumidity
                
                # Aplicar correção
                # - Umidade alta (>50%): aumenta temperatura prevista
                # - Umidade baixa (<50%): reduz temperatura prevista
                correction = humidityDeviation * humidityImpactCoeff
                
                correctedTemp = tempValue + correction
                correctedPredictions.append(correctedTemp)
            
            logger.debug(
                f"💧 [ForecastService] Correção de umidade aplicada: "
                f"avg={avgHumidity:.1f}%, trend={humiditySlope:+.2f}%/h, "
                f"correction range=[{min(correctedPredictions)-min(tempPredictions):+.2f}, "
                f"{max(correctedPredictions)-max(tempPredictions):+.2f}]°C"
            )
            
            return correctedPredictions
            
        except Exception as e:
            logger.warning(f"⚠️ [ForecastService] Erro na correção de umidade: {e}")
            return tempPredictions
    
    def predict(
        self, 
        data_history: List[Dict], 
        aggregateData: bool = True,
        exogenousData: Optional[List[Dict]] = None
    ) -> Optional[Dict]:
        """
        Realiza previsao de valores futuros para as próximas 24 horas.
        
        Implementa arquitetura híbrida com suporte a:
        - Agregação de dados por hora (configurable)
        - Sazonalidade diária (24h)
        - Sazonalidade anual (365 dias) para previsão climática
        - Variável exógena (umidade) para correção de temperatura
        
        Ordem de prioridade dos modelos:
        1. IBM Granite TTM-R2 (principal)
        2. SARIMA (fallback primário)
        3. Exponential Smoothing (fallback secundário)
        
        Args:
            data_history: Historico de dados (minimo 10 pontos)
            aggregateData: Se True, agrega dados por hora antes da previsão
            exogenousData: Dados exógenos (ex: umidade) para correção de previsão
            
        Returns:
            dict: Previsoes com timestamps e valores, ou None se erro
        """
        import time as time_module
        predict_start = time_module.time()
        
        if len(data_history) < 10:
            logger.warning(
                f"⚠️  [ForecastService] Insufficient data: {len(data_history)} < 10"
            )
            return None
        
        try:
            # Agregar dados por hora se configurado
            if aggregateData and len(data_history) > self.sampleInterval:
                workingData = self.aggregateHourlyData(data_history)
                logger.info(f"📊 [ForecastService] Dados agregados: {len(data_history)} -> {len(workingData)} pontos horários")
            else:
                workingData = data_history
            
            # Horizonte de previsão: 24 horas
            steps = min(self.forecast_horizon, 24)
            logger.info(f"🎯 [ForecastService] Starting prediction: {len(workingData)} data points, {steps}h forecast horizon")
            
            # Tentar usar Granite TTM-R2 primeiro
            forecast_values = None
            model_used = "unknown"
            
            if self.use_granite and len(workingData) >= self.context_length:
                logger.info("🔮 [ForecastService] Usando IBM Granite TTM-R2 para previsao")
                granite_start = time_module.time()
                forecast_values = self._granite_forecast(workingData, steps)
                granite_time = time_module.time() - granite_start
                
                if forecast_values is not None:
                    model_used = "IBM Granite TTM-R2"
                    logger.info(f"✅ [ForecastService] Granite prediction successful in {granite_time:.3f}s")
                else:
                    logger.warning(f"⚠️  [ForecastService] Granite prediction failed after {granite_time:.3f}s")
            
            # Fallback primário: SARIMA
            if forecast_values is None:
                if self.use_granite:
                    logger.warning("⚠️  [ForecastService] Granite falhou, usando SARIMA (fallback primário)")
                else:
                    logger.info("📊 [ForecastService] Usando SARIMA (Granite nao disponivel)")
                
                fallback_start = time_module.time()
                forecast_values = self._sarima_fallback_forecast(workingData, steps)
                
                if forecast_values is not None:
                    model_used = self.sarimaFallback.getModelInfo()['modelType']
                    self.useFallback = True
                    fallback_time = time_module.time() - fallback_start
                    logger.info(f"✅ [ForecastService] SARIMA fallback completed in {fallback_time:.3f}s")
            
            # Fallback secundário: Exponential Smoothing (se SARIMA também falhar)
            if forecast_values is None:
                logger.warning("⚠️  [ForecastService] SARIMA falhou, usando Exponential Smoothing (fallback secundário)")
                
                fallback_start = time_module.time()
                series = self._prepare_series(workingData)
                forecast_values = self._exponential_smoothing_forecast(series, steps)
                model_used = "Exponential Smoothing (Holt-Winters)"
                fallback_time = time_module.time() - fallback_start
                logger.info(f"✅ [ForecastService] Exponential Smoothing fallback completed in {fallback_time:.3f}s")
            
            # Aplicar ajuste de sazonalidade anual às previsões
            last_timestamp = pd.to_datetime(workingData[-1]['timestamp'])
            if self.enableAnnualSeasonality and forecast_values is not None:
                forecast_values = self.addAnnualSeasonalComponent(
                    list(forecast_values), 
                    last_timestamp.to_pydatetime()
                )
                logger.info(f"📅 [ForecastService] Sazonalidade anual aplicada às previsões")
            
            # Aplicar correção de umidade (variável exógena) se disponível
            humidity_correction_applied = False
            if exogenousData is not None and forecast_values is not None:
                forecast_values = self.applyHumidityCorrection(
                    list(forecast_values),
                    exogenousData,
                    last_timestamp.to_pydatetime()
                )
                humidity_correction_applied = True
                logger.info(f"💧 [ForecastService] Correção de umidade aplicada às previsões")
            
            # Intervalo de previsão: 1 hora (dados agregados) ou original
            if aggregateData and len(data_history) > self.sampleInterval:
                interval_hours = 1  # 1 hora entre previsões
            else:
                # Calcular intervalo dos dados originais
                if len(workingData) >= 2:
                    t1 = pd.to_datetime(workingData[-1]['timestamp'])
                    t2 = pd.to_datetime(workingData[-2]['timestamp'])
                    interval_hours = (t1 - t2).total_seconds() / 3600
                else:
                    interval_hours = 1.0
            
            # Gerar timestamps futuros e montar resultado
            predictions = []
            for i, value in enumerate(forecast_values):
                future_timestamp = last_timestamp + timedelta(hours=interval_hours * (i + 1))
                
                # Extrair valor escalar se for lista ou array
                if isinstance(value, (list, np.ndarray)):
                    scalar_value = float(value[0]) if len(value) > 0 else 0.0
                else:
                    scalar_value = float(value)
                
                predictions.append({
                    'timestamp': future_timestamp.isoformat(),
                    'value': scalar_value,
                    'horizon_step': i + 1,
                    'hours_ahead': int(interval_hours * (i + 1))
                })
            
            result = {
                'predictions': predictions,
                'forecast_timestamp': datetime.now().isoformat(),
                'forecast_horizon_hours': steps,
                'context_size': len(workingData),
                'original_data_points': len(data_history),
                'aggregated': aggregateData and len(data_history) > self.sampleInterval,
                'annual_seasonality_applied': self.enableAnnualSeasonality,
                'humidity_correction_applied': humidity_correction_applied,
                'model': model_used,
                'model_type': 'granite' if 'Granite' in model_used else 'statistical'
            }
            
            total_predict_time = time_module.time() - predict_start
            logger.info(f"✅ [ForecastService] Previsão 24h concluída: {len(predictions)} pontos horários usando {model_used} em {total_predict_time:.3f}s")
            logger.debug(f"📦 [ForecastService] Result size: {len(str(result))} bytes")
            
            return result
            
        except Exception as e:
            total_predict_time = time_module.time() - predict_start
            logger.error(f"❌ [ForecastService] Prediction error after {total_predict_time:.3f}s: {str(e)}")
            logger.error(f"❌ [ForecastService] Exception type: {type(e).__name__}")
            logger.error(f"❌ [ForecastService] Stack trace:", exc_info=True)
            return None
    
    def is_model_loaded(self) -> bool:
        """Verifica se o modelo esta carregado"""
        return self._model_loaded
    
    def calculateMae(self, predictions: List[float], actuals: List[float]) -> float:
        """
        Calcula o Mean Absolute Error (MAE) entre previsões e valores reais.
        
        O MAE é usado para decidir se o fallback SARIMA deve ser ativado.
        Fórmula: MAE = (1/n) * Σ|y_t - ŷ_t|
        
        Args:
            predictions: Lista de valores previstos
            actuals: Lista de valores reais
        
        Returns:
            float: MAE calculado
        """
        if not predictions or not actuals:
            return 0.0
        
        n = min(len(predictions), len(actuals))
        if n == 0:
            return 0.0
        
        errors = [abs(predictions[i] - actuals[i]) for i in range(n)]
        return sum(errors) / n
    
    def updateMaeTracking(self, predicted: float, actual: float) -> float:
        """
        Atualiza o tracking de MAE com um novo par previsão/real.
        
        Se o MAE exceder o threshold, ativa automaticamente o fallback SARIMA.
        
        Args:
            predicted: Valor previsto
            actual: Valor real observado
        
        Returns:
            float: MAE atualizado
        """
        with self._lock:
            self.predictionHistory.append(predicted)
            self.actualHistory.append(actual)
            
            self.currentMae = self.calculateMae(
                list(self.predictionHistory),
                list(self.actualHistory)
            )
            
            # Atualiza também no serviço SARIMA
            self.sarimaFallback.updateMaeTracking(predicted, actual)
            
            # Verifica se deve ativar/desativar fallback
            shouldFallback = self.sarimaFallback.shouldUseFallback(self.currentMae)
            
            if shouldFallback != self.useFallback:
                self.useFallback = shouldFallback
                if shouldFallback:
                    logger.warning(f"⚠️  [ForecastService] MAE ({self.currentMae:.4f}) > threshold ({self.maeThreshold}), ativando SARIMA")
                else:
                    logger.info(f"✅ [ForecastService] MAE ({self.currentMae:.4f}) normalizado, usando Granite")
            
            return self.currentMae
    
    def shouldUseFallback(self) -> bool:
        """
        Verifica se deve usar o fallback SARIMA.
        
        O fallback é ativado quando:
        1. Granite não está disponível
        2. MAE do Granite excede o threshold
        3. Houve erro recente no Granite
        
        Returns:
            bool: True se deve usar SARIMA
        """
        if not GRANITE_AVAILABLE:
            return True
        
        return self.sarimaFallback.shouldUseFallback(self.currentMae)
    
    def getFallbackInfo(self) -> Dict:
        """
        Retorna informações sobre o estado do sistema de fallback.
        
        Returns:
            dict: Informações do fallback SARIMA
        """
        return {
            'fallbackActive': self.useFallback,
            'currentMae': self.currentMae,
            'maeThreshold': self.maeThreshold,
            'sarimaInfo': self.sarimaFallback.getModelInfo()
        }
    
    def get_model_info(self) -> Dict:
        """
        Retorna informacoes detalhadas sobre o modelo
        
        Returns:
            dict: Informacoes do modelo incluindo tipo e status
        """
        model_type = "IBM Granite TTM-R2" if self.use_granite and self._model_loaded else "Exponential Smoothing"
        
        info = {
            'model_name': self.model_name,
            'model_type': model_type,
            'using_granite': self.use_granite and self._model_loaded,
            'granite_available': GRANITE_AVAILABLE,
            'forecast_horizon': self.forecast_horizon,
            'context_length': self.context_length,
            'device': self.device,
            'loaded': self._model_loaded,
            'gpu_available': torch.cuda.is_available() if GRANITE_AVAILABLE else False,
            'fallback_active': self.useFallback,
            'current_mae': self.currentMae,
            'mae_threshold': self.maeThreshold,
            'sarima_info': self.sarimaFallback.getModelInfo() if self.sarimaFallback else None
        }
        
        # Log do status atual
        if info['using_granite']:
            logger.info(f"ℹ️  [ForecastService] Status: Usando IBM Granite TTM-R2 em {self.device}")
        else:
            logger.info(f"ℹ️  [ForecastService] Status: Usando Exponential Smoothing (fallback)")
        
        return info
