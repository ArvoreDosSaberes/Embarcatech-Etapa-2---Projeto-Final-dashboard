"""
Anomaly Detector Service
Detecta anomalias em series temporais usando analise estatistica
"""

import logging
import statistics
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """Detector de anomalias para series temporais usando Z-score"""
    
    def __init__(
        self,
        threshold_multiplier: float = 3.0,
        window_size: int = 50,
        rolling_window_seconds: int | None = None
    ):
        self.threshold_multiplier = threshold_multiplier
        self.window_size = window_size
        self.rolling_window_seconds = rolling_window_seconds
        self.anomaly_history = []
        
        logger.info(
            f"⚠️  [AnomalyDetector] Initialized with threshold={threshold_multiplier}σ, "
            f"window={window_size}"
        )
    
    def _calculate_statistics(self, values: List[float]) -> Tuple[float, float]:
        """Calcula media e desvio padrao de uma lista de valores"""
        if len(values) < 2:
            return 0.0, 0.0
        return statistics.mean(values), statistics.stdev(values)
    
    def _calculate_zscore(self, value: float, mean: float, stdev: float) -> float:
        """Calcula Z-score (numero de desvios padrao do valor em relacao a media)"""
        if stdev == 0:
            return 0.0
        return abs(value - mean) / stdev
    
    def _filter_by_time_window(self, data_history: List[Dict]) -> List[Dict]:
        """Filtra dados pela janela de tempo configurada."""
        if not self.rolling_window_seconds or not data_history:
            return data_history

        latest_timestamp = data_history[-1]['timestamp']
        try:
            from datetime import datetime

            latest_dt = datetime.fromisoformat(latest_timestamp)
            cutoff = latest_dt.timestamp() - self.rolling_window_seconds

            filtered = [
                point for point in data_history
                if datetime.fromisoformat(point['timestamp']).timestamp() >= cutoff
            ]
            return filtered if filtered else data_history[-self.window_size:]
        except Exception as error:
            logger.error("❌ [AnomalyDetector] Failed to filter by time window", exc_info=error)
            return data_history[-self.window_size:]

    def detect(self, current_value: float, data_history: List[Dict]) -> Tuple[bool, Dict]:
        """
        Detecta se o valor atual e uma anomalia
        
        Args:
            current_value: Valor atual a avaliar
            data_history: Historico completo de dados
            
        Returns:
            tuple: (is_anomaly: bool, info: dict)
        """
        if len(data_history) < 2:
            return False, {
                'reason': 'insufficient_data',
                'data_points': len(data_history),
                'required': 2
            }
        
        window_data = self._filter_by_time_window(data_history)[-self.window_size:]
        window_values = [point['value'] for point in window_data]
        
        mean, stdev = self._calculate_statistics(window_values)
        zscore = self._calculate_zscore(current_value, mean, stdev)
        is_anomaly = zscore > self.threshold_multiplier
        
        info = {
            'value': current_value,
            'mean': mean,
            'stdev': stdev,
            'zscore': zscore,
            'threshold': self.threshold_multiplier,
            'deviation': zscore,
            'window_size': len(window_values),
            'is_anomaly': is_anomaly
        }
        
        if is_anomaly:
            if zscore > 5.0:
                info['severity'] = 'critical'
                info['severity_emoji'] = '🔴'
            elif zscore > 4.0:
                info['severity'] = 'high'
                info['severity_emoji'] = '🟠'
            else:
                info['severity'] = 'medium'
                info['severity_emoji'] = '🟡'
            
            self.anomaly_history.append({
                'timestamp': data_history[-1]['timestamp'] if data_history else None,
                'info': info
            })
            
            logger.warning(
                f"{info['severity_emoji']} [AnomalyDetector] Anomaly detected! "
                f"Value={current_value:.2f}, Mean={mean:.2f}, "
                f"Deviation={zscore:.2f}σ, Severity={info['severity']}"
            )
        else:
            info['severity'] = 'normal'
            info['severity_emoji'] = '🟢'
        
        return is_anomaly, info
    
    def get_anomaly_rate(self, recent_count: int = 100) -> float:
        """Calcula taxa de anomalias recentes"""
        if not self.anomaly_history:
            return 0.0
        recent_anomalies = self.anomaly_history[-recent_count:]
        return len(recent_anomalies) / recent_count if recent_count > 0 else 0.0
    
    def get_statistics(self) -> Dict:
        """Retorna estatisticas do detector"""
        return {
            'total_anomalies': len(self.anomaly_history),
            'threshold_multiplier': self.threshold_multiplier,
            'window_size': self.window_size,
            'recent_anomaly_rate': self.get_anomaly_rate(100)
        }
    
    def reset(self):
        """Reseta historico de anomalias"""
        self.anomaly_history.clear()
        logger.info("🔄 [AnomalyDetector] Reset anomaly history")
    
    def adjust_sensitivity(self, new_threshold: float):
        """Ajusta sensibilidade do detector"""
        old_threshold = self.threshold_multiplier
        self.threshold_multiplier = new_threshold
        logger.info(
            f"🎚️  [AnomalyDetector] Sensitivity adjusted: "
            f"{old_threshold}σ → {new_threshold}σ"
        )
