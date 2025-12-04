"""
SARIMA Fallback Service
Serviço de fallback usando SARIMA para previsão de séries temporais

Este módulo implementa o modelo SARIMA (Seasonal AutoRegressive Integrated Moving Average)
como mecanismo de fallback quando o modelo Granite TTM não está disponível ou apresenta
erros elevados (MAE acima do limiar).

Baseado no artigo: "MAE e SARIMA como fallback na falta do Granite TTM"
Projeto: EmbarcaTech TIC-27 - Rack Inteligente

Autor: Dashboard Rack Inteligente - EmbarcaTech
"""

import logging
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
from collections import deque
import warnings
import signal
import sys
import threading

# Configuração do logger
logger = logging.getLogger(__name__)
warnings.filterwarnings('ignore')

# Tentar importar statsmodels para SARIMA
STATSMODELS_AVAILABLE = False
try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    from statsmodels.tsa.stattools import adfuller, acf, pacf
    STATSMODELS_AVAILABLE = True
    logger.info("✅ [SarimaFallbackService] statsmodels disponível para SARIMA")
except ImportError:
    logger.warning("⚠️ [SarimaFallbackService] statsmodels não disponível - usando implementação simplificada")


@dataclass
class SarimaConfig:
    """
    Configuração dos parâmetros SARIMA e do sistema de fallback.
    
    Notação SARIMA(p, d, q)(P, D, Q)_s:
    - p: ordem AR não sazonal
    - d: diferenciações não sazonais
    - q: ordem MA não sazonal  
    - P: ordem AR sazonal
    - D: diferenciações sazonais
    - Q: ordem MA sazonal
    - s: período da sazonalidade
    
    Attributes:
        p: Ordem autorregressiva (AR) não sazonal
        d: Ordem de diferenciação não sazonal
        q: Ordem de média móvel (MA) não sazonal
        P: Ordem autorregressiva (AR) sazonal
        D: Ordem de diferenciação sazonal
        Q: Ordem de média móvel (MA) sazonal
        s: Período da sazonalidade (ex: 24 para ciclo diário)
        maeThreshold: Limiar de MAE para ativar fallback
        maeWindowSize: Tamanho da janela para cálculo do MAE
        autoSelectParams: Se True, tenta detectar parâmetros automaticamente
    """
    p: int = 1
    d: int = 1
    q: int = 1
    P: int = 1
    D: int = 1
    Q: int = 0
    s: int = 24  # Ciclo diário para dados horários
    maeThreshold: float = 5.0
    maeWindowSize: int = 50
    autoSelectParams: bool = True


@dataclass
class ForecastResult:
    """
    Resultado de uma previsão com metadados.
    
    Attributes:
        predictions: Lista de valores previstos
        timestamps: Lista de timestamps correspondentes
        mae: MAE calculado (se disponível)
        modelUsed: Nome do modelo utilizado
        isFromFallback: Se a previsão veio do fallback
        confidence: Nível de confiança (0-1)
    """
    predictions: List[float]
    timestamps: List[str]
    mae: Optional[float] = None
    modelUsed: str = "SARIMA"
    isFromFallback: bool = True
    confidence: float = 0.8


class SarimaFallbackService:
    """
    Serviço de fallback SARIMA para previsão de séries temporais.
    
    Este serviço implementa o modelo SARIMA como alternativa robusta quando:
    1. O modelo Granite TTM não está disponível
    2. O modelo Granite TTM apresenta erros elevados (MAE > threshold)
    3. Há problemas de conectividade ou recursos computacionais
    
    O SARIMA é ideal para fallback pois:
    - É computacionalmente leve
    - Funciona bem com poucos dados
    - É determinístico e explicável
    - Captura padrões sazonais naturalmente
    
    Attributes:
        config: Configuração SARIMA
        predictionHistory: Histórico de previsões para cálculo de MAE
        actualHistory: Histórico de valores reais para cálculo de MAE
        currentMae: MAE atual calculado
        fallbackActive: Se o fallback está ativo
        modelFitted: Modelo SARIMA treinado
    """
    
    def __init__(self, config: Optional[SarimaConfig] = None):
        """
        Inicializa o serviço de fallback SARIMA.
        
        Args:
            config: Configuração SARIMA (usa defaults se None)
        """
        self.config = config or SarimaConfig()
        
        # Históricos para cálculo de MAE
        self.predictionHistory: deque = deque(maxlen=self.config.maeWindowSize)
        self.actualHistory: deque = deque(maxlen=self.config.maeWindowSize)
        
        # Estado do fallback
        self.currentMae: float = 0.0
        self.fallbackActive: bool = False
        self.modelFitted: Optional[Any] = None
        self._running: bool = True
        
        # Lock para thread-safety
        self._lock = threading.Lock()
        
        # Registrar handler para sinais de interrupção
        self._setupSignalHandlers()
        
        logger.info(f"[SarimaFallbackService] ✅ Inicializado com SARIMA({self.config.p},{self.config.d},{self.config.q})({self.config.P},{self.config.D},{self.config.Q})_{self.config.s}")
        logger.info(f"[SarimaFallbackService] 🎚️ MAE Threshold: {self.config.maeThreshold}")
    
    def _setupSignalHandlers(self) -> None:
        """
        Configura handlers para sinais de interrupção (Ctrl+C).
        Implementa saída graciosa conforme requisitos do projeto.
        """
        def signalHandler(signum, frame):
            logger.info("[SarimaFallbackService] 🛑 Recebido sinal de interrupção, encerrando graciosamente...")
            self.stop()
            sys.exit(0)
        
        # Registra handlers apenas na thread principal
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, signalHandler)
            signal.signal(signal.SIGTERM, signalHandler)
    
    def stop(self) -> None:
        """Para o serviço graciosamente."""
        self._running = False
        logger.info("[SarimaFallbackService] 🛑 Serviço parado")
    
    def start(self) -> None:
        """Inicia/reinicia o serviço."""
        self._running = True
        logger.info("[SarimaFallbackService] ▶️ Serviço iniciado")
    
    def calculateMae(self, predictions: List[float], actuals: List[float]) -> float:
        """
        Calcula o Mean Absolute Error (MAE) entre previsões e valores reais.
        
        O MAE é a métrica escolhida para o projeto EmbarcaTech por sua:
        - Interpretação intuitiva (erro médio absoluto)
        - Robustez a outliers (comparado ao RMSE)
        - Mesma unidade da variável prevista
        
        Fórmula: MAE = (1/n) * Σ|y_t - ŷ_t|
        
        Args:
            predictions: Lista de valores previstos
            actuals: Lista de valores reais observados
        
        Returns:
            float: MAE calculado
        """
        if not predictions or not actuals:
            return 0.0
        
        n = min(len(predictions), len(actuals))
        if n == 0:
            return 0.0
        
        # Calcula erro absoluto de cada par
        errors = [abs(predictions[i] - actuals[i]) for i in range(n)]
        
        # Retorna média dos erros
        mae = sum(errors) / n
        
        logger.debug(f"[SarimaFallbackService] 📊 MAE calculado: {mae:.4f} ({n} pontos)")
        return mae
    
    def updateMaeTracking(self, predicted: float, actual: float) -> float:
        """
        Atualiza o tracking de MAE com um novo par previsão/real.
        
        Este método mantém uma janela deslizante de pares previsão/real
        para calcular o MAE atual do modelo principal.
        
        Args:
            predicted: Valor previsto
            actual: Valor real observado
        
        Returns:
            float: MAE atualizado
        """
        with self._lock:
            self.predictionHistory.append(predicted)
            self.actualHistory.append(actual)
            
            # Calcula MAE da janela atual
            self.currentMae = self.calculateMae(
                list(self.predictionHistory),
                list(self.actualHistory)
            )
            
            return self.currentMae
    
    def shouldUseFallback(self, graniteMae: Optional[float] = None) -> bool:
        """
        Verifica se o fallback SARIMA deve ser ativado.
        
        O fallback é ativado quando:
        1. O MAE do modelo principal (Granite) excede o limiar
        2. O modelo principal não está disponível
        3. Há erro na execução do modelo principal
        
        Args:
            graniteMae: MAE do modelo Granite (None = modelo indisponível)
        
        Returns:
            bool: True se deve usar fallback SARIMA
        """
        # Se Granite não disponível, usar fallback
        if graniteMae is None:
            if not self.fallbackActive:
                logger.info("[SarimaFallbackService] 🔄 Granite indisponível, ativando SARIMA")
                self.fallbackActive = True
            return True
        
        # Verifica se MAE excede threshold
        if graniteMae > self.config.maeThreshold:
            if not self.fallbackActive:
                logger.warning(f"[SarimaFallbackService] ⚠️ MAE Granite ({graniteMae:.4f}) > threshold ({self.config.maeThreshold}), ativando SARIMA")
                self.fallbackActive = True
            return True
        
        # MAE dentro do aceitável, desativa fallback se estava ativo
        if self.fallbackActive:
            logger.info(f"[SarimaFallbackService] ✅ MAE Granite ({graniteMae:.4f}) normalizado, desativando fallback")
            self.fallbackActive = False
        
        return False
    
    def _applyDifferencing(self, series: np.ndarray, d: int, s: int = 0, D: int = 0) -> np.ndarray:
        """
        Aplica diferenciação não sazonal e sazonal à série.
        
        Diferenciação não sazonal: ∇y_t = y_t - y_{t-1}
        Diferenciação sazonal: ∇_s y_t = y_t - y_{t-s}
        
        Args:
            series: Série temporal original
            d: Ordem de diferenciação não sazonal
            s: Período sazonal
            D: Ordem de diferenciação sazonal
        
        Returns:
            np.ndarray: Série diferenciada
        """
        result = series.copy()
        
        # Diferenciação sazonal primeiro
        for _ in range(D):
            if len(result) > s:
                result = result[s:] - result[:-s]
        
        # Diferenciação não sazonal
        for _ in range(d):
            if len(result) > 1:
                result = np.diff(result)
        
        return result
    
    def _invertDifferencing(
        self, 
        forecasts: np.ndarray, 
        originalSeries: np.ndarray,
        d: int, 
        s: int = 0, 
        D: int = 0
    ) -> np.ndarray:
        """
        Inverte a diferenciação para obter valores na escala original.
        
        Args:
            forecasts: Previsões na série diferenciada
            originalSeries: Série original antes da diferenciação
            d: Ordem de diferenciação não sazonal
            s: Período sazonal
            D: Ordem de diferenciação sazonal
        
        Returns:
            np.ndarray: Previsões na escala original
        """
        result = forecasts.copy()
        
        # Inverte diferenciação não sazonal
        for _ in range(d):
            # Usa último valor da série original
            lastValue = originalSeries[-1]
            cumsum = np.cumsum(result)
            result = lastValue + cumsum
        
        # Inverte diferenciação sazonal
        for _ in range(D):
            if len(originalSeries) >= s:
                baseValues = originalSeries[-s:]
                newResult = []
                for i, val in enumerate(result):
                    idx = i % s
                    newResult.append(val + baseValues[idx])
                result = np.array(newResult)
        
        return result
    
    def _fitArCoefficients(self, series: np.ndarray, p: int) -> np.ndarray:
        """
        Estima coeficientes AR usando método de Yule-Walker.
        
        O método de Yule-Walker resolve o sistema:
        R * φ = r
        
        Onde R é a matriz de autocorrelação e r é o vetor de autocorrelação.
        
        Args:
            series: Série temporal estacionária
            p: Ordem AR
        
        Returns:
            np.ndarray: Coeficientes AR estimados
        """
        if p == 0 or len(series) <= p:
            return np.array([])
        
        try:
            # Calcula autocorrelações
            n = len(series)
            mean = np.mean(series)
            centeredSeries = series - mean
            
            acorr = np.correlate(centeredSeries, centeredSeries, mode='full')
            acorr = acorr[n-1:] / acorr[n-1]  # Normaliza
            
            # Monta matriz de Toeplitz
            R = np.zeros((p, p))
            for i in range(p):
                for j in range(p):
                    R[i, j] = acorr[abs(i - j)]
            
            r = acorr[1:p+1]
            
            # Resolve sistema linear
            try:
                phi = np.linalg.solve(R, r)
            except np.linalg.LinAlgError:
                phi = np.linalg.lstsq(R, r, rcond=None)[0]
            
            return phi
            
        except Exception as e:
            logger.warning(f"[SarimaFallbackService] ⚠️ Erro ao estimar AR: {e}")
            return np.zeros(p)
    
    def _simpleSarimaForecast(
        self, 
        series: np.ndarray, 
        steps: int
    ) -> np.ndarray:
        """
        Implementação simplificada de SARIMA para quando statsmodels não está disponível.
        
        Esta implementação usa:
        1. Diferenciação para remover tendência e sazonalidade
        2. Modelo AR simples para a série estacionária
        3. Inversão da diferenciação para obter previsões
        
        Args:
            series: Série temporal original
            steps: Número de passos a prever
        
        Returns:
            np.ndarray: Valores previstos
        """
        cfg = self.config
        
        try:
            # Aplica diferenciação
            diffSeries = self._applyDifferencing(series, cfg.d, cfg.s, cfg.D)
            
            if len(diffSeries) < cfg.p + 1:
                # Dados insuficientes, usa média simples
                logger.warning("[SarimaFallbackService] ⚠️ Dados insuficientes, usando média móvel")
                lastValues = series[-min(10, len(series)):]
                return np.full(steps, np.mean(lastValues))
            
            # Estima coeficientes AR
            arCoeffs = self._fitArCoefficients(diffSeries, cfg.p)
            
            # Gera previsões na série diferenciada
            forecasts = []
            buffer = list(diffSeries[-cfg.p:]) if cfg.p > 0 else []
            
            for _ in range(steps):
                if cfg.p > 0 and len(arCoeffs) > 0:
                    # Previsão AR
                    pred = np.dot(arCoeffs, buffer[-cfg.p:][::-1])
                else:
                    # Sem AR, usa média
                    pred = np.mean(diffSeries[-10:])
                
                forecasts.append(pred)
                buffer.append(pred)
            
            forecasts = np.array(forecasts)
            
            # Inverte diferenciação
            result = self._invertDifferencing(forecasts, series, cfg.d, cfg.s, cfg.D)
            
            return result
            
        except Exception as e:
            logger.error(f"[SarimaFallbackService] ❌ Erro no forecast simplificado: {e}")
            # Fallback para média simples
            return np.full(steps, np.mean(series[-10:]))
    
    def _statsmodelsSarimaForecast(
        self, 
        series: pd.Series, 
        steps: int
    ) -> np.ndarray:
        """
        Realiza previsão usando SARIMA do statsmodels.
        
        Esta é a implementação preferida quando statsmodels está disponível,
        pois oferece estimação otimizada de parâmetros e intervalos de confiança.
        
        Args:
            series: Série temporal como pd.Series
            steps: Número de passos a prever
        
        Returns:
            np.ndarray: Valores previstos
        """
        cfg = self.config
        
        try:
            # Configura e ajusta modelo SARIMA
            model = SARIMAX(
                series,
                order=(cfg.p, cfg.d, cfg.q),
                seasonal_order=(cfg.P, cfg.D, cfg.Q, cfg.s),
                enforce_stationarity=False,
                enforce_invertibility=False
            )
            
            # Ajusta modelo (suprime warnings de convergência)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.modelFitted = model.fit(disp=False, maxiter=100)
            
            # Gera previsões
            forecast = self.modelFitted.forecast(steps=steps)
            
            logger.debug(f"[SarimaFallbackService] ✅ SARIMA forecast: {len(forecast)} pontos")
            
            return forecast.values
            
        except Exception as e:
            logger.warning(f"[SarimaFallbackService] ⚠️ Erro no statsmodels SARIMA: {e}")
            # Fallback para implementação simplificada
            return self._simpleSarimaForecast(series.values, steps)
    
    def _detectSeasonality(self, series: np.ndarray) -> int:
        """
        Detecta automaticamente o período de sazonalidade.
        
        Usa análise de autocorrelação para identificar picos periódicos.
        
        Args:
            series: Série temporal
        
        Returns:
            int: Período sazonal detectado
        """
        if len(series) < 50:
            return self.config.s  # Usa padrão se poucos dados
        
        try:
            # Calcula autocorrelação
            if STATSMODELS_AVAILABLE:
                autocorr = acf(series, nlags=min(100, len(series) // 2), fft=True)
            else:
                n = len(series)
                mean = np.mean(series)
                centered = series - mean
                autocorr = np.correlate(centered, centered, mode='full')
                autocorr = autocorr[n-1:n-1+min(100, n//2)]
                autocorr = autocorr / autocorr[0]
            
            # Encontra picos (possíveis períodos sazonais)
            peaks = []
            for i in range(2, len(autocorr) - 1):
                if autocorr[i] > autocorr[i-1] and autocorr[i] > autocorr[i+1]:
                    if autocorr[i] > 0.3:  # Threshold de significância
                        peaks.append((i, autocorr[i]))
            
            if peaks:
                # Retorna período do maior pico
                bestPeak = max(peaks, key=lambda x: x[1])
                logger.info(f"[SarimaFallbackService] 📊 Sazonalidade detectada: período = {bestPeak[0]}")
                return bestPeak[0]
            
        except Exception as e:
            logger.debug(f"[SarimaFallbackService] Falha na detecção de sazonalidade: {e}")
        
        return self.config.s
    
    def forecast(
        self, 
        dataHistory: List[Dict], 
        steps: int = 10
    ) -> Optional[ForecastResult]:
        """
        Realiza previsão SARIMA a partir do histórico de dados.
        
        Este é o método principal de previsão do fallback. Ele:
        1. Prepara os dados de entrada
        2. Detecta sazonalidade (se configurado)
        3. Aplica o modelo SARIMA
        4. Retorna previsões com metadados
        
        Args:
            dataHistory: Histórico de dados [{timestamp, value}, ...]
            steps: Número de passos a prever
        
        Returns:
            ForecastResult: Resultado da previsão ou None se erro
        """
        if not self._running:
            return None
        
        if len(dataHistory) < 10:
            logger.warning(f"[SarimaFallbackService] ⚠️ Dados insuficientes: {len(dataHistory)} < 10")
            return None
        
        try:
            # Extrai valores e timestamps
            values = np.array([point['value'] for point in dataHistory])
            timestamps = [pd.to_datetime(point['timestamp']) for point in dataHistory]
            
            # Detecta sazonalidade se configurado
            if self.config.autoSelectParams:
                detectedSeason = self._detectSeasonality(values)
                if detectedSeason != self.config.s:
                    self.config.s = detectedSeason
            
            # Cria série pandas
            series = pd.Series(values, index=timestamps)
            series = series.sort_index()
            
            # Realiza previsão
            if STATSMODELS_AVAILABLE:
                predictions = self._statsmodelsSarimaForecast(series, steps)
            else:
                predictions = self._simpleSarimaForecast(values, steps)
            
            # Calcula timestamps futuros
            lastTimestamp = timestamps[-1]
            if len(timestamps) >= 2:
                interval = (timestamps[-1] - timestamps[-2]).total_seconds()
            else:
                interval = 1.0
            
            futureTimestamps = []
            for i in range(steps):
                futureTs = lastTimestamp + timedelta(seconds=interval * (i + 1))
                futureTimestamps.append(futureTs.isoformat())
            
            # Calcula confiança baseada na variância
            variance = np.var(values[-50:]) if len(values) >= 50 else np.var(values)
            confidence = max(0.5, min(0.95, 1.0 - (variance / (np.mean(values) ** 2 + 1e-6))))
            
            result = ForecastResult(
                predictions=predictions.tolist(),
                timestamps=futureTimestamps,
                mae=self.currentMae,
                modelUsed=f"SARIMA({self.config.p},{self.config.d},{self.config.q})({self.config.P},{self.config.D},{self.config.Q})_{self.config.s}",
                isFromFallback=True,
                confidence=confidence
            )
            
            logger.info(f"[SarimaFallbackService] ✅ Previsão SARIMA: {len(predictions)} pontos, confiança={confidence:.2f}")
            
            return result
            
        except Exception as e:
            logger.error(f"[SarimaFallbackService] ❌ Erro na previsão SARIMA: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def getModelInfo(self) -> Dict:
        """
        Retorna informações sobre o estado atual do serviço.
        
        Returns:
            dict: Informações do modelo e estado do fallback
        """
        return {
            'modelType': 'SARIMA',
            'parameters': {
                'p': self.config.p,
                'd': self.config.d,
                'q': self.config.q,
                'P': self.config.P,
                'D': self.config.D,
                'Q': self.config.Q,
                's': self.config.s
            },
            'maeThreshold': self.config.maeThreshold,
            'currentMae': self.currentMae,
            'fallbackActive': self.fallbackActive,
            'statsmodelsAvailable': STATSMODELS_AVAILABLE,
            'running': self._running
        }
    
    def resetMaeTracking(self) -> None:
        """
        Reseta o histórico de MAE.
        
        Útil quando há mudança significativa nos dados ou
        após recalibração do modelo.
        """
        with self._lock:
            self.predictionHistory.clear()
            self.actualHistory.clear()
            self.currentMae = 0.0
            self.fallbackActive = False
            logger.info("[SarimaFallbackService] 🔄 MAE tracking resetado")
