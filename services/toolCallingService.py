"""
Tool Calling Service
Servico de chamada de ferramentas (Funções) orientadas por LLMs

Este modulo implementa um servico de chamada de ferramentas (Funções) orientadas por LLMs.
Responsável por analisar dados de telemetria de múltiplos racks e determinar ações de controle.

Autor: Dashboard Rack Inteligente - EmbarcaTech
"""

import logging
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, asdict
from datetime import datetime
from openai import OpenAI

# Configuração do logger
logger = logging.getLogger(__name__)


@dataclass
class RackAction:
    """
    Representa uma ação a ser executada em um rack.
    
    Attributes:
        rackId: Identificador do rack alvo
        function: Nome da função a ser executada
        reason: Motivo/justificativa da ação
    """
    rackId: str
    function: str
    reason: str


@dataclass
class RackTelemetry:
    """
    Dados de telemetria de um rack para análise pela LLM.
    
    Attributes:
        rackId: Identificador do rack
        temperature: Temperatura atual em °C
        humidity: Umidade relativa atual em %
        doorStatus: Status da porta (0=fechada, 1=aberta)
        ventilationStatus: Status da ventilação (0=off, 1=on)
        buzzerStatus: Status do buzzer (0=off, 1=porta, 2=arrombamento, 3=superaquecimento)
        tempAvg: Média de temperatura da última hora
        tempTrend: Tendência de temperatura (°C/min) - positivo=subindo, negativo=descendo
        humAvg: Média de umidade da última hora
        humTrend: Tendência de umidade (%/min)
    """
    rackId: str
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    doorStatus: int = 0
    ventilationStatus: int = 0
    buzzerStatus: int = 0
    tempAvg: Optional[float] = None
    tempTrend: Optional[float] = None
    humAvg: Optional[float] = None
    humTrend: Optional[float] = None


@dataclass
class ThresholdConfig:
    """
    Configuração de limiares com histerese (Schmitt Trigger).
    
    Attributes:
        tempHighThreshold: Temperatura para ligar ventilação
        tempLowThreshold: Temperatura para desligar ventilação
        tempCriticalThreshold: Temperatura crítica para alerta
        tempCriticalReset: Temperatura para resetar alerta crítico
        humHighThreshold: Umidade para ligar ventilação
        humLowThreshold: Umidade para desligar ventilação
        trendHistoryWindow: Janela de histórico em minutos
        trendMinRate: Taxa mínima para considerar tendência
    """
    tempHighThreshold: float = 35.0
    tempLowThreshold: float = 28.0
    tempCriticalThreshold: float = 45.0
    tempCriticalReset: float = 40.0
    humHighThreshold: float = 80.0
    humLowThreshold: float = 60.0
    trendHistoryWindow: int = 60
    trendMinRate: float = 0.1


class ToolCallingService:
    """
    Servico de chamada de ferramentas (Funções) orientadas por LLMs.

    Esta classe fornece os mecanismos para:
    - Carregar prompts da pasta prompts/
    - Processar dados de múltiplos racks em lote
    - Chamar a LLM para decisão de ações
    - Executar ações no RackControlService
    - Notificar a UI sobre ações em execução
    
    Attributes:
        apiKey: Chave de API para o modelo LLM
        model: Nome do modelo LLM a ser utilizado
        client: Cliente OpenAI para comunicação com a LLM
        promptsPath: Caminho para a pasta de prompts
        rackControlService: Serviço de controle de racks
        pendingTelemetry: Buffer de telemetria pendente para processamento em lote
        analysisInterval: Intervalo mínimo entre análises (segundos)
        lastAnalysisTime: Timestamp da última análise
        actionCallback: Callback para notificar a UI sobre ações
        analysisLock: Lock para thread-safety
    """

    # Mapeamento de funções disponíveis para controle de racks
    AVAILABLE_FUNCTIONS = {
        'turnOnVentilation',
        'turnOffVentilation',
        'activateCriticalTemperatureAlert',
        'deactivateCriticalTemperatureAlert',
        'activateDoorOpenAlert',
        'activateBreakInAlert',
        'silenceBuzzer',
        'openDoor',
        'closeDoor'
    }

    # Definição das Tools para Function Calling nativo da API
    TOOLS_DEFINITION = [
        {
            "type": "function",
            "function": {
                "name": "turnOnVentilation",
                "description": "Liga a ventilação de um rack específico. Use quando a temperatura estiver alta (>=35°C) ou umidade alta (>=80%).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rackId": {
                            "type": "string",
                            "description": "Identificador único do rack"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Motivo para ligar a ventilação"
                        }
                    },
                    "required": ["rackId", "reason"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "turnOffVentilation",
                "description": "Desliga a ventilação de um rack específico. Use quando temperatura e umidade estiverem normais (temp <=25°C ou umidade <=40%) e ventilação estiver ligada.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rackId": {
                            "type": "string",
                            "description": "Identificador único do rack"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Motivo para desligar a ventilação"
                        }
                    },
                    "required": ["rackId", "reason"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "activateCriticalTemperatureAlert",
                "description": "Ativa o alerta sonoro de temperatura crítica/superaquecimento. Use quando temperatura >=45°C.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rackId": {
                            "type": "string",
                            "description": "Identificador único do rack"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Motivo para ativar o alerta"
                        }
                    },
                    "required": ["rackId", "reason"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "deactivateCriticalTemperatureAlert",
                "description": "Desativa o alerta de temperatura crítica. Use quando temperatura voltar ao normal (<45°C).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rackId": {
                            "type": "string",
                            "description": "Identificador único do rack"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Motivo para desativar o alerta"
                        }
                    },
                    "required": ["rackId", "reason"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "activateDoorOpenAlert",
                "description": "Ativa o alerta sonoro de porta aberta. Use quando a porta estiver aberta por tempo prolongado.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rackId": {
                            "type": "string",
                            "description": "Identificador único do rack"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Motivo para ativar o alerta"
                        }
                    },
                    "required": ["rackId", "reason"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "activateBreakInAlert",
                "description": "Ativa o alerta sonoro de arrombamento/invasão.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rackId": {
                            "type": "string",
                            "description": "Identificador único do rack"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Motivo para ativar o alerta"
                        }
                    },
                    "required": ["rackId", "reason"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "silenceBuzzer",
                "description": "Silencia o buzzer/alarme sonoro do rack.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rackId": {
                            "type": "string",
                            "description": "Identificador único do rack"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Motivo para silenciar o buzzer"
                        }
                    },
                    "required": ["rackId", "reason"]
                }
            }
        }
    ]

    def __init__(
        self, 
        apiKey: str, 
        model: str = "granite4:3b", 
        llmServerUrl: str = "https://generativa.rapport.tec.br/api/v1",
        promptsPath: Optional[str] = None,
        analysisInterval: float = 10.0
    ) -> None:
        """
        Inicializa o servico de chamada de ferramentas orientadas por LLMs.
        
        Args:
            apiKey: Chave de API para o modelo LLM
            model: Modelo LLM a ser utilizado (default: granite4:3b)
            llmServerUrl: URL do servidor LLM (default: generativa.rapport.tec.br)
            promptsPath: Caminho para a pasta de prompts (default: ../prompts relativo ao dashboard)
            analysisInterval: Intervalo mínimo entre análises em segundos (default: 10.0)
        """
        self.apiKey = apiKey
        self.model = model
        self.client = OpenAI(
            api_key=apiKey,
            base_url=llmServerUrl
        )
        
        # Determina o caminho da pasta de prompts
        if promptsPath is None:
            dashboardDir = Path(__file__).parent.parent
            self.promptsPath = dashboardDir.parent / "prompts"
        else:
            self.promptsPath = Path(promptsPath)
        
        # Referência para o serviço de controle (será injetado)
        self.rackControlService = None
        
        # Buffer de telemetria para processamento em lote
        self.pendingTelemetry: Dict[str, RackTelemetry] = {}
        
        # Histórico de telemetria para cálculo de tendências
        # Formato: {rackId: {'temp': [(timestamp, value), ...], 'hum': [(timestamp, value), ...]}}
        self.telemetryHistory: Dict[str, Dict[str, List[tuple]]] = {}
        
        # Carrega configuração de thresholds do ambiente
        self.thresholds = self._loadThresholdsFromEnv()
        
        # Controle de intervalo de análise
        self.analysisInterval = analysisInterval
        self.lastAnalysisTime: float = 0
        
        # Callback para notificar ações à UI (piscar rack)
        self.actionCallback: Optional[Callable[[str, str], None]] = None
        
        # Callback para atualizar a barra de status
        self.statusCallback: Optional[Callable[[str, str, str], None]] = None
        
        # Lock para thread-safety
        self.analysisLock = threading.Lock()
        
        # Cache do prompt carregado
        self._promptCache: Dict[str, str] = {}
        
        # Flag de running
        self._running = True
        
        logger.info(f"[ToolCallingService] ✅ Inicializado com modelo {model}")
        logger.info(f"[ToolCallingService] 🎚️ Thresholds: Temp[{self.thresholds.tempLowThreshold}-{self.thresholds.tempHighThreshold}°C], Hum[{self.thresholds.humLowThreshold}-{self.thresholds.humHighThreshold}%]")

    def _loadThresholdsFromEnv(self) -> ThresholdConfig:
        """
        Carrega os limiares de histerese do arquivo .env.
        
        Returns:
            ThresholdConfig com valores do ambiente ou defaults
        """
        return ThresholdConfig(
            tempHighThreshold=float(os.getenv("TEMP_HIGH_THRESHOLD", "35")),
            tempLowThreshold=float(os.getenv("TEMP_LOW_THRESHOLD", "28")),
            tempCriticalThreshold=float(os.getenv("TEMP_CRITICAL_THRESHOLD", "45")),
            tempCriticalReset=float(os.getenv("TEMP_CRITICAL_RESET", "40")),
            humHighThreshold=float(os.getenv("HUMIDITY_HIGH_THRESHOLD", "80")),
            humLowThreshold=float(os.getenv("HUMIDITY_LOW_THRESHOLD", "60")),
            trendHistoryWindow=int(os.getenv("TREND_HISTORY_WINDOW", "60")),
            trendMinRate=float(os.getenv("TREND_MIN_RATE", "0.1"))
        )

    def setRackControlService(self, rackControlService) -> None:
        """
        Injeta o serviço de controle de racks.
        
        Args:
            rackControlService: Instância do RackControlService
        """
        self.rackControlService = rackControlService
        logger.info("[ToolCallingService] 🔗 RackControlService vinculado")

    def setActionCallback(self, callback: Callable[[str, str], None]) -> None:
        """
        Define o callback para notificar a UI sobre ações em execução.
        
        O callback recebe:
            - rackId: ID do rack onde a ação está sendo executada
            - action: Nome da ação sendo executada
        
        Args:
            callback: Função de callback (rackId, action) -> None
        """
        self.actionCallback = callback
        logger.info("[ToolCallingService] 🔔 ActionCallback configurado")

    def setStatusCallback(self, callback: Callable[[str, str, str], None]) -> None:
        """
        Define o callback para atualizar a barra de status com informações das ações.
        
        O callback recebe:
            - rackId: ID do rack
            - action: Nome da ação executada
            - reason: Motivo da ação
        
        Args:
            callback: Função de callback (rackId, action, reason) -> None
        """
        self.statusCallback = callback
        logger.info("[ToolCallingService] 📊 StatusCallback configurado")

    def loadPrompt(self, promptName: str) -> str:
        """
        Carrega um prompt do arquivo na pasta prompts.
        
        Args:
            promptName: Nome do arquivo de prompt (sem extensão ou com .md)
        
        Returns:
            Conteúdo do prompt como string
        
        Raises:
            FileNotFoundError: Se o arquivo de prompt não existir
        """
        # Verifica cache
        if promptName in self._promptCache:
            return self._promptCache[promptName]
        
        # Adiciona extensão .md se não presente
        if not promptName.endswith('.md'):
            promptName = f"{promptName}.md"
        
        promptFile = self.promptsPath / promptName
        
        if not promptFile.exists():
            raise FileNotFoundError(f"Prompt não encontrado: {promptFile}")
        
        content = promptFile.read_text(encoding='utf-8')
        self._promptCache[promptName] = content
        
        logger.debug(f"[ToolCallingService] 📄 Prompt carregado: {promptName}")
        return content

    def updateTelemetry(self, rackId: str, telemetry: Dict[str, Any]) -> None:
        """
        Atualiza os dados de telemetria de um rack no buffer e histórico.
        
        Esta função é chamada quando novos dados chegam via MQTT.
        Os dados são acumulados até o próximo ciclo de análise.
        Também armazena histórico para cálculo de tendências.
        
        Args:
            rackId: Identificador do rack
            telemetry: Dicionário com dados de telemetria
        """
        currentTime = time.time()
        
        with self.analysisLock:
            # Inicializa estruturas se necessário
            if rackId not in self.pendingTelemetry:
                self.pendingTelemetry[rackId] = RackTelemetry(rackId=rackId)
            
            if rackId not in self.telemetryHistory:
                self.telemetryHistory[rackId] = {'temp': [], 'hum': []}
            
            rack = self.pendingTelemetry[rackId]
            history = self.telemetryHistory[rackId]
            
            # Atualiza campos presentes e armazena histórico
            if 'temperature' in telemetry and telemetry['temperature'] is not None:
                temp = float(telemetry['temperature'])
                rack.temperature = temp
                history['temp'].append((currentTime, temp))
            
            if 'humidity' in telemetry and telemetry['humidity'] is not None:
                hum = float(telemetry['humidity'])
                rack.humidity = hum
                history['hum'].append((currentTime, hum))
            
            if 'door_status' in telemetry and telemetry['door_status'] is not None:
                rack.doorStatus = int(telemetry['door_status'])
            if 'ventilation_status' in telemetry and telemetry['ventilation_status'] is not None:
                rack.ventilationStatus = int(telemetry['ventilation_status'])
            if 'buzzer_status' in telemetry and telemetry['buzzer_status'] is not None:
                rack.buzzerStatus = int(telemetry['buzzer_status'])
            
            # Limpa dados antigos do histórico (fora da janela)
            windowSeconds = self.thresholds.trendHistoryWindow * 60
            cutoffTime = currentTime - windowSeconds
            history['temp'] = [(t, v) for t, v in history['temp'] if t >= cutoffTime]
            history['hum'] = [(t, v) for t, v in history['hum'] if t >= cutoffTime]
            
            # Calcula tendências e médias
            rack.tempAvg, rack.tempTrend = self._calculateTrendStats(history['temp'])
            rack.humAvg, rack.humTrend = self._calculateTrendStats(history['hum'])
            
            logger.debug(f"[ToolCallingService] 📊 Telemetria atualizada: {rackId} (temp={rack.temperature}°C, trend={rack.tempTrend:.3f}°C/min)" if rack.tempTrend else f"[ToolCallingService] 📊 Telemetria atualizada: {rackId}")

    def _calculateTrendStats(self, historyData: List[tuple]) -> tuple:
        """
        Calcula média e tendência (taxa de variação) a partir do histórico.
        
        Usa regressão linear simples para calcular a tendência.
        
        Args:
            historyData: Lista de tuplas (timestamp, value)
        
        Returns:
            Tupla (média, tendência em unidade/minuto)
        """
        if not historyData or len(historyData) < 2:
            if historyData:
                return historyData[-1][1], 0.0
            return None, None
        
        # Calcula média
        values = [v for _, v in historyData]
        avg = sum(values) / len(values)
        
        # Calcula tendência usando regressão linear simples
        # y = a + b*x, onde b é a inclinação (tendência)
        n = len(historyData)
        timestamps = [t for t, _ in historyData]
        
        # Normaliza timestamps para minutos desde o primeiro ponto
        t0 = timestamps[0]
        xValues = [(t - t0) / 60.0 for t in timestamps]  # Em minutos
        
        # Calcula coeficientes da regressão linear
        sumX = sum(xValues)
        sumY = sum(values)
        sumXY = sum(x * y for x, y in zip(xValues, values))
        sumX2 = sum(x * x for x in xValues)
        
        denominator = n * sumX2 - sumX * sumX
        if abs(denominator) < 1e-10:
            return avg, 0.0
        
        # b = (n * Σxy - Σx * Σy) / (n * Σx² - (Σx)²)
        trend = (n * sumXY - sumX * sumY) / denominator
        
        # Ignora tendências muito pequenas
        if abs(trend) < self.thresholds.trendMinRate:
            trend = 0.0
        
        return avg, trend

    def shouldAnalyze(self) -> bool:
        """
        Verifica se é hora de executar uma nova análise.
        
        Returns:
            True se passou tempo suficiente desde a última análise
        """
        currentTime = time.time()
        return (currentTime - self.lastAnalysisTime) >= self.analysisInterval

    def buildSystemPrompt(self) -> str:
        """
        Constrói o prompt de sistema para o Tool Calling com regras de histerese.
        
        Returns:
            Prompt de sistema com regras de controle e thresholds
        """
        th = self.thresholds
        return f"""Você é um sistema inteligente de controle de racks de datacenter.
Analise os dados de telemetria e tendências para executar ações de controle preventivo.

## Limiares de Histerese (Schmitt Trigger):

### Temperatura
- **LIGAR ventilação**: temperatura atual >= {th.tempHighThreshold}°C OU (tendência positiva E média histórica >= {th.tempLowThreshold}°C)
- **DESLIGAR ventilação**: temperatura atual <= {th.tempLowThreshold}°C E tendência <= 0 E ventilação ligada
- **ALERTA CRÍTICO**: temperatura >= {th.tempCriticalThreshold}°C
- **RESETAR ALERTA**: temperatura <= {th.tempCriticalReset}°C

### Umidade
- **LIGAR ventilação**: umidade >= {th.humHighThreshold}%
- **DESLIGAR ventilação**: umidade <= {th.humLowThreshold}% E ventilação ligada

## Campos de Telemetria:

- **temperature/humidity**: Valor atual
- **tempAvg/humAvg**: Média da última hora
- **tempTrend/humTrend**: Taxa de variação (°C/min ou %/min)
  - Positivo = subindo
  - Negativo = descendo
  - Zero = estável
- **ventilationStatus**: 0=desligada, 1=ligada

## Regras de Decisão Preditiva:

1. Se a temperatura está **subindo** (tempTrend > 0) e se aproximando do limiar:
   - Ligue a ventilação PREVENTIVAMENTE para evitar superaquecimento
   
2. Se a temperatura está **descendo** (tempTrend < 0) e abaixo do limiar inferior:
   - Desligue a ventilação para economizar energia
   
3. SEMPRE verifique ventilationStatus antes de agir:
   - NÃO ligue se já está ligada
   - NÃO desligue se já está desligada

4. Para porta aberta (doorStatus=1): ative alerta apenas se persistir

## Importante:
- Use os dados de tendência para decisões preditivas
- Respeite a histerese para evitar acionamentos desnecessários
- Analise TODOS os racks fornecidos
- Indique claramente o motivo de cada ação no parâmetro 'reason'"""

    def buildUserPrompt(self, telemetryList: List[RackTelemetry]) -> str:
        """
        Constrói o prompt do usuário com dados de telemetria.
        
        Args:
            telemetryList: Lista de telemetrias de racks
        
        Returns:
            Prompt com dados JSON formatados
        """
        telemetryData = [asdict(t) for t in telemetryList]
        jsonData = json.dumps(telemetryData, indent=2, ensure_ascii=False)
        
        return f"""Analise os seguintes dados de telemetria de racks e execute as ações de controle necessárias:

```json
{jsonData}
```

Legenda dos campos:
- rackId: Identificador do rack
- temperature: Temperatura em °C (null = desconhecida)
- humidity: Umidade em % (null = desconhecida)
- doorStatus: 0=fechada, 1=aberta
- ventilationStatus: 0=desligada, 1=ligada
- buzzerStatus: 0=off, 1=porta aberta, 2=arrombamento, 3=superaquecimento

Execute as ações necessárias usando as ferramentas disponíveis."""

    def callLlmWithTools(self, telemetryList: List[RackTelemetry]) -> List[RackAction]:
        """
        Chama a LLM usando Tool Calling nativo e retorna as ações.
        
        Args:
            telemetryList: Lista de telemetrias de racks
        
        Returns:
            Lista de RackAction extraídas das tool_calls
        """
        try:
            logger.info("[ToolCallingService] 🤖 Chamando LLM com Tool Calling...")
            
            systemPrompt = self.buildSystemPrompt()
            userPrompt = self.buildUserPrompt(telemetryList)
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": systemPrompt},
                    {"role": "user", "content": userPrompt}
                ],
                tools=self.TOOLS_DEFINITION,
                tool_choice="auto",  # Permite à LLM decidir quando usar tools
                temperature=0.1,
                max_tokens=2048
            )
            
            # Extrai as tool_calls da resposta
            message = response.choices[0].message
            
            if not message.tool_calls:
                logger.info("[ToolCallingService] ℹ️ LLM não executou nenhuma ferramenta")
                # Verifica se há conteúdo de texto (resposta sem tool calls)
                if message.content:
                    logger.debug(f"[ToolCallingService] 📝 Resposta texto: {message.content[:200]}...")
                return []
            
            # Processa cada tool_call
            actions = self.parseToolCalls(message.tool_calls)
            
            logger.info(f"[ToolCallingService] 🛠️ {len(actions)} tool call(s) processada(s)")
            return actions
            
        except Exception as e:
            logger.error(f"[ToolCallingService] ❌ Erro na chamada LLM com tools: {e}")
            import traceback
            traceback.print_exc()
            return []

    def parseToolCalls(self, toolCalls) -> List[RackAction]:
        """
        Parseia as tool_calls da resposta da LLM.
        
        Args:
            toolCalls: Lista de tool_calls do response da API
        
        Returns:
            Lista de RackAction válidas
        """
        actions = []
        
        for toolCall in toolCalls:
            try:
                functionName = toolCall.function.name
                argumentsStr = toolCall.function.arguments
                
                # Parse dos argumentos JSON
                try:
                    arguments = json.loads(argumentsStr)
                except json.JSONDecodeError:
                    logger.warning(f"[ToolCallingService] ⚠️ Argumentos inválidos: {argumentsStr}")
                    continue
                
                rackId = arguments.get('rackId')
                reason = arguments.get('reason', 'Ação automática da IA')
                
                # Valida campos obrigatórios
                if not rackId:
                    logger.warning(f"[ToolCallingService] ⚠️ rackId não fornecido para {functionName}")
                    continue
                
                # Valida se a função existe
                if functionName not in self.AVAILABLE_FUNCTIONS:
                    logger.warning(f"[ToolCallingService] ⚠️ Função desconhecida: {functionName}")
                    continue
                
                actions.append(RackAction(
                    rackId=rackId,
                    function=functionName,
                    reason=reason
                ))
                
                logger.debug(f"[ToolCallingService] ✅ Tool call: {functionName}({rackId}) - {reason}")
                
            except Exception as e:
                logger.error(f"[ToolCallingService] ❌ Erro ao parsear tool_call: {e}")
                continue
        
        return actions

    def executeAction(self, action: RackAction, racksDict: Dict[str, Any]) -> bool:
        """
        Executa uma ação específica em um rack.
        
        Args:
            action: Ação a ser executada
            racksDict: Dicionário de objetos Rack (rackId -> Rack)
        
        Returns:
            True se a ação foi executada com sucesso
        """
        if not self.rackControlService:
            logger.error("[ToolCallingService] ❌ RackControlService não configurado")
            return False
        
        rackId = action.rackId
        function = action.function
        
        # Obtém ou cria o objeto Rack
        if rackId not in racksDict:
            logger.warning(f"[ToolCallingService] ⚠️ Rack não encontrado: {rackId}")
            return False
        
        rack = racksDict[rackId]
        
        # Notifica a UI antes de executar (para piscar o rack)
        if self.actionCallback:
            try:
                self.actionCallback(rackId, function)
            except Exception as e:
                logger.warning(f"[ToolCallingService] ⚠️ Erro no actionCallback: {e}")
        
        # Mapeia a função para o método do serviço
        try:
            methodName = function
            if hasattr(self.rackControlService, methodName):
                method = getattr(self.rackControlService, methodName)
                success = method(rack)
                
                if success:
                    logger.info(f"[ToolCallingService] ✅ Ação executada: {function} em {rackId} - {action.reason}")
                    
                    # Notifica a barra de status
                    if self.statusCallback:
                        try:
                            self.statusCallback(rackId, function, action.reason)
                        except Exception as e:
                            logger.warning(f"[ToolCallingService] ⚠️ Erro no statusCallback: {e}")
                else:
                    logger.warning(f"[ToolCallingService] ⚠️ Ação falhou: {function} em {rackId}")
                
                return success
            else:
                logger.error(f"[ToolCallingService] ❌ Método não encontrado: {methodName}")
                return False
                
        except Exception as e:
            logger.error(f"[ToolCallingService] ❌ Erro ao executar ação {function}: {e}")
            return False

    def analyzeAndExecute(self, racksDict: Dict[str, Any]) -> List[RackAction]:
        """
        Analisa os dados de telemetria pendentes e executa as ações necessárias.
        
        Este é o método principal que deve ser chamado periodicamente.
        Utiliza o recurso nativo de Tool Calling da LLM.
        
        Args:
            racksDict: Dicionário de objetos Rack (rackId -> Rack)
        
        Returns:
            Lista de ações executadas
        """
        if not self._running:
            return []
        
        # Verifica se é hora de analisar
        if not self.shouldAnalyze():
            return []
        
        with self.analysisLock:
            # Verifica se há telemetria pendente
            if not self.pendingTelemetry:
                return []
            
            telemetryList = list(self.pendingTelemetry.values())
            # Mantém os dados para próxima análise (atualizados incrementalmente)
        
        # Atualiza timestamp da última análise
        self.lastAnalysisTime = time.time()
        
        logger.info(f"[ToolCallingService] 🔍 Analisando {len(telemetryList)} rack(s) com Tool Calling...")
        
        # Chama a LLM com Tool Calling nativo
        actions = self.callLlmWithTools(telemetryList)
        
        if not actions:
            logger.info("[ToolCallingService] ℹ️ Nenhuma ação necessária")
            return []
        
        logger.info(f"[ToolCallingService] 📋 {len(actions)} ação(ões) identificada(s)")
        
        # Executa as ações
        executedActions = []
        for action in actions:
            if self.executeAction(action, racksDict):
                executedActions.append(action)
        
        return executedActions

    def stop(self) -> None:
        """Para o serviço graciosamente."""
        self._running = False
        logger.info("[ToolCallingService] 🛑 Serviço parado")

    def start(self) -> None:
        """Inicia/reinicia o serviço."""
        self._running = True
        logger.info("[ToolCallingService] ▶️ Serviço iniciado")

